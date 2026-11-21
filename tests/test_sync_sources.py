"""What Jira, Bitbucket and the calendar offer the Obsidian sync: tasks and meetings with keys."""

import datetime
import json
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from productivity_agent_tools.bitbucket.client import BitbucketClient
from productivity_agent_tools.bitbucket.settings import BitbucketSettings
from productivity_agent_tools.capabilities import MeetingSource, TaskSource
from productivity_agent_tools.credentials import bearer_token
from productivity_agent_tools.errors import AgentToolsError
from productivity_agent_tools.http import AtlassianClient
from productivity_agent_tools.jira.client import JiraClient
from productivity_agent_tools.jira.settings import (
    DEFAULT_TASKS_JQL,
    JiraSettings,
    parse_jira_settings,
)
from productivity_agent_tools.microsoft.calendar_client import OutlookCalendarClient
from productivity_agent_tools.microsoft.outlook import OutlookClient
from productivity_agent_tools.microsoft.outlook_settings import OutlookSettings
from productivity_agent_tools.products import ToolContext

type Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
async def atlassian() -> AsyncIterator[Callable[[Handler], AtlassianClient]]:
    made: list[AtlassianClient] = []

    def build(handler: Handler) -> AtlassianClient:
        client = AtlassianClient(
            base_url="https://host.example",
            credentials=bearer_token("t"),
            transport=httpx.MockTransport(handler),
        )
        made.append(client)
        return client

    yield build
    for client in made:
        await client.aclose()


# --- Jira ------------------------------------------------------------------------------


def _issue(key: str, category: str, **fields: object) -> dict[str, object]:
    return {
        "key": key,
        "fields": {
            "summary": f"Summary of {key}",
            "status": {"name": f"{category} status", "statusCategory": {"key": category}},
            **fields,
        },
    }


ISSUES = [
    _issue("ABC-1", "new", priority={"name": "Highest"}, duedate="2026-11-21", timeestimate=5400),
    _issue("ABC-2", "indeterminate", priority={"name": "Medium"}, timeoriginalestimate=90),
    _issue("ABC-3", "indeterminate", priority={"name": "Custom"}, resolution={"name": "Fixed"}),
    _issue("ABC-4", "done", priority={"name": "Lowest"}, timeestimate=0, timeoriginalestimate=1800),
    {"key": "ABC-5", "fields": {"status": {"name": "Odd"}}},
]


async def test_jira_offers_its_open_issues_as_tasks(
    atlassian: Callable[[Handler], AtlassianClient],
) -> None:
    seen: list[httpx.QueryParams] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params)
        return httpx.Response(200, json={"issues": ISSUES})

    jira = JiraClient(atlassian(handler), JiraSettings(tasks_jql="project = ABC"))
    assert isinstance(jira, TaskSource)
    assert jira.task_source == "jira"
    tasks = await jira.my_open_tasks()
    assert seen[0]["jql"] == "project = ABC"
    assert seen[0]["fields"] == (
        "summary,status,priority,duedate,resolution,timeestimate,timeoriginalestimate"
    )
    assert seen[0]["maxResults"] == "100"
    by_key = {task.key: task for task in tasks}
    first = by_key["ABC-1"]
    assert (first.title, first.status, first.done, first.priority, first.due) == (
        "Summary of ABC-1",
        "open",
        False,
        "high",
        "2026-11-21",
    )
    assert first.url == "https://host.example/jira/browse/ABC-1"
    assert first.source_status == "new status"
    assert (by_key["ABC-2"].status, by_key["ABC-2"].priority) == ("in-progress", "normal")
    assert (first.estimate_minutes, by_key["ABC-2"].estimate_minutes) == (90, 2)
    assert by_key["ABC-3"].estimate_minutes is None
    assert by_key["ABC-4"].estimate_minutes == 30
    assert (by_key["ABC-3"].status, by_key["ABC-3"].done) == ("done", True)
    assert (by_key["ABC-4"].done, by_key["ABC-4"].priority) == (True, "low")
    odd = by_key["ABC-5"]
    assert (odd.title, odd.status, odd.priority) == ("ABC-5", "open", None)


async def test_jira_looks_tasks_up_by_key_in_chunks(
    atlassian: Callable[[Handler], AtlassianClient],
) -> None:
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        jql = request.url.params["jql"]
        queries.append(jql)
        keys = jql.removeprefix("key in (").removesuffix(")").split(", ")
        return httpx.Response(200, json={"issues": [_issue(k, "done") for k in keys[:2]]})

    jira = JiraClient(atlassian(handler), JiraSettings())
    keys = [f"ABC-{n}" for n in range(1, 151)] + ["not a key", "ABC-1"]
    found = await jira.tasks_by_key(keys)
    assert len(queries) == 2
    assert queries[0].startswith("key in (ABC-1, ABC-10, ABC-100,")
    assert all(task.done for task in found.values())
    assert await jira.tasks_by_key(["nope"]) == {}


def test_the_tasks_query_is_configurable() -> None:
    assert parse_jira_settings({}).tasks_jql == DEFAULT_TASKS_JQL
    assert parse_jira_settings({"tasks_jql": " project = X "}).tasks_jql == "project = X"
    assert parse_jira_settings({"tasks_jql": ""}).tasks_jql == DEFAULT_TASKS_JQL


# --- Bitbucket -------------------------------------------------------------------------


def _pull(number: int, state: str = "OPEN", mine: str | None = None) -> dict[str, object]:
    reviewers = [{"user": {"slug": "sam", "name": "sam"}, "status": "APPROVED"}]
    if mine:
        reviewers.append({"user": {"slug": "pat", "name": "pat"}, "status": mine})
    return {
        "id": number,
        "title": f"Change {number}",
        "state": state,
        "toRef": {"displayId": "main", "repository": {"slug": "web", "project": {"key": "PROJ"}}},
        "reviewers": reviewers,
        "links": {"self": [{"href": f"https://host.example/bitbucket/pr/{number}"}]},
    }


async def test_bitbucket_offers_reviews_waiting_on_the_user(
    atlassian: Callable[[Handler], AtlassianClient],
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"values": [_pull(42)], "isLastPage": True})

    bitbucket = BitbucketClient(atlassian(handler), BitbucketSettings(user_slug="pat"))
    assert isinstance(bitbucket, TaskSource)
    assert bitbucket.task_source == "bitbucket"
    [task] = await bitbucket.my_open_tasks()
    params = seen[0].url.params
    assert seen[0].url.path.endswith("/dashboard/pull-requests")
    assert (params["role"], params["state"], params["participantStatus"]) == (
        "REVIEWER",
        "OPEN",
        "UNAPPROVED",
    )
    assert (task.key, task.title, task.status, task.done) == (
        "PROJ/web#42",
        "Review: Change 42",
        "open",
        False,
    )
    assert task.url == "https://host.example/bitbucket/pr/42"


async def test_a_review_is_finished_once_merged_or_reviewed(
    atlassian: Callable[[Handler], AtlassianClient],
) -> None:
    pulls = {
        "1": _pull(1, "OPEN", "UNAPPROVED"),
        "2": _pull(2, "MERGED", "UNAPPROVED"),
        "3": _pull(3, "OPEN", "NEEDS_WORK"),
        "4": _pull(4, "OPEN"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        number = request.url.path.rsplit("/", 1)[-1]
        if number in pulls:
            return httpx.Response(200, json=pulls[number])
        return httpx.Response(404, json={"errors": [{"message": "No such pull request"}]})

    bitbucket = BitbucketClient(atlassian(handler), BitbucketSettings(user_slug="pat"))
    keys = ["PROJ/web#1", "PROJ/web#2", "PROJ/web#3", "PROJ/web#4", "PROJ/web#9", "bad key"]
    found = await bitbucket.tasks_by_key(keys)
    assert {key: task.done for key, task in found.items()} == {
        "PROJ/web#1": False,
        "PROJ/web#2": True,
        "PROJ/web#3": True,
        "PROJ/web#4": False,
    }
    assert found["PROJ/web#2"].source_status == "MERGED"


async def test_without_the_users_slug_only_the_state_counts(
    atlassian: Callable[[Handler], AtlassianClient],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "pull-requests" in request.url.path:
            return httpx.Response(200, json=_pull(3, "OPEN", "NEEDS_WORK"))
        return httpx.Response(404, json={"errors": [{"message": "no"}]})

    bitbucket = BitbucketClient(atlassian(handler), BitbucketSettings())
    found = await bitbucket.tasks_by_key(["PROJ/web#3"])
    assert found["PROJ/web#3"].done is False


# --- calendar --------------------------------------------------------------------------


def _event(uid: str | None, start: str, **values: object) -> dict[str, object]:
    return {
        "Id": f"id-{uid}",
        "Subject": f"Meeting {uid}",
        "Start": {"DateTime": start, "TimeZone": "UTC"},
        "End": {"DateTime": "2026-11-19T15:00:00.0000000", "TimeZone": "UTC"},
        "iCalUId": uid,
        **values,
    }


async def test_the_calendar_offers_meetings_with_their_keys() -> None:
    seen: list[httpx.Request] = []
    events = [
        _event(
            "u1",
            "2026-11-19T14:00:00.0000000",
            SeriesMasterId="s1",
            IsAllDay=False,
            IsCancelled=True,
            ResponseStatus={"Response": "Accepted"},
            ShowAs="Busy",
            Organizer={"EmailAddress": {"Name": "Sam", "Address": "sam@x"}},
            Location={"DisplayName": "Room A"},
            OnlineMeetingUrl="https://join/1",
            WebLink="https://owa/1",
        ),
        _event("u2", "2026-11-19T16:00:00+01:00", End=None, Subject=None),
        _event(None, "2026-11-19T17:00:00.0000000"),
        _event("u4", "not a time"),
        {"iCalUId": "u5", "Start": None},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"value": events})

    api = OutlookClient(
        base_url="https://outlook.example/api/v2.0",
        credentials=bearer_token("t"),
        transport=httpx.MockTransport(handler),
    )
    calendar = OutlookCalendarClient(api, OutlookSettings())
    assert isinstance(calendar, MeetingSource)
    zone = datetime.timezone(datetime.timedelta(hours=-5))
    start = datetime.datetime(2026, 11, 19, tzinfo=zone)
    meetings = await calendar.my_meetings(start, start + datetime.timedelta(days=1))
    await api.aclose()
    params = seen[0].url.params
    assert (params["startDateTime"], params["endDateTime"]) == (
        "2026-11-19T05:00:00Z",
        "2026-11-20T05:00:00Z",
    )
    assert [meeting.ical_uid for meeting in meetings] == ["u1", "u2"]
    first, second = meetings
    assert first.start == datetime.datetime(2026, 11, 19, 14, tzinfo=datetime.UTC)
    assert (first.series_id, first.cancelled, first.response, first.show_as) == (
        "s1",
        True,
        "Accepted",
        "Busy",
    )
    assert (first.organizer, first.location, first.join_url, first.url) == (
        "Sam",
        "Room A",
        "https://join/1",
        "https://owa/1",
    )
    assert second.start == datetime.datetime(2026, 11, 19, 15, tzinfo=datetime.UTC)
    assert second.end == second.start
    assert (second.title, second.all_day, second.organizer) == ("(no subject)", False, None)


# --- finding every source --------------------------------------------------------------


def test_peers_of_finds_every_source(atlassian: Callable[[Handler], AtlassianClient]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=json.loads("{}"))

    jira = JiraClient(atlassian(handler), JiraSettings())
    bitbucket = BitbucketClient(atlassian(handler), BitbucketSettings())
    context = ToolContext(
        dry_run=True, token_source="", peers={"jira": jira, "x": object(), "bitbucket": bitbucket}
    )
    assert context.peers_of(TaskSource) == [jira, bitbucket]
    assert context.peers_of(MeetingSource) == []


async def test_a_source_reports_a_failed_request(
    atlassian: Callable[[Handler], AtlassianClient],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"errorMessages": ["Bad JQL"], "errors": {}})

    with pytest.raises(AgentToolsError, match="Bad JQL"):
        await JiraClient(atlassian(handler), JiraSettings()).my_open_tasks()


async def test_the_calendar_follows_pages() -> None:
    skips: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        skip = request.url.params["$skip"]
        skips.append(skip)
        body: dict[str, object] = {"value": [_event(f"u{skip}", "2026-11-19T14:00:00.0000000")]}
        if skip != "200":
            body["@odata.nextLink"] = "https://next"
        return httpx.Response(200, json=body)

    api = OutlookClient(
        base_url="https://outlook.example/api/v2.0",
        credentials=bearer_token("t"),
        transport=httpx.MockTransport(handler),
    )
    calendar = OutlookCalendarClient(api, OutlookSettings())
    start = datetime.datetime(2026, 11, 19, tzinfo=datetime.UTC)
    meetings = await calendar.my_meetings(start, start + datetime.timedelta(days=1))
    await api.aclose()
    assert skips == ["0", "100", "200"]
    assert [m.ical_uid for m in meetings] == ["u0", "u100", "u200"]


async def test_the_calendar_stops_after_ten_pages() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["$skip"])
        return httpx.Response(200, json={"value": [], "@odata.nextLink": "https://next"})

    api = OutlookClient(
        base_url="https://outlook.example/api/v2.0",
        credentials=bearer_token("t"),
        transport=httpx.MockTransport(handler),
    )
    calendar = OutlookCalendarClient(api, OutlookSettings())
    start = datetime.datetime(2026, 11, 19, tzinfo=datetime.UTC)
    assert await calendar.my_meetings(start, start + datetime.timedelta(days=30)) == []
    await api.aclose()
    assert calls == [str(n * 100) for n in range(10)]
