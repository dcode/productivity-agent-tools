import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from pydantic import JsonValue

from productivity_agent_tools.api import ApiClient, WriteOutcome
from productivity_agent_tools.config import JiraSettings, Settings
from productivity_agent_tools.errors import AtlassianError
from productivity_agent_tools.http import AtlassianClient
from productivity_agent_tools.jira.client import (
    JiraClient,
    jira_time,
    normalize_duration,
    normalize_issue_key,
)
from productivity_agent_tools.server import build_server
from tests.conftest import FakeJira


@pytest.mark.parametrize("bad", ["", "ABC", "ABC-0", "abc-1/../x", "A B-1", "ABC-1?x=1"])
def test_issue_keys_are_validated(bad: str) -> None:
    with pytest.raises(AtlassianError):
        normalize_issue_key(bad)


def test_issue_key_is_normalized() -> None:
    assert normalize_issue_key(" abc-12 ") == "ABC-12"


async def test_search_returns_compact_rows(
    fake: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    result = await make_jira().search("assignee = currentUser()", 500)
    assert (result.total, result.returned) == (7, 1)
    row = result.issues[0]
    assert (row.key, row.status, row.priority) == ("ABC-1", "To Do", "High")
    assert row.url == "https://host.example/jira/browse/ABC-1"
    assert fake.requests[0].url.params["maxResults"] == "100"


async def test_get_issue_keeps_only_newest_comments(make_jira: Callable[..., JiraClient]) -> None:
    issue = await make_jira().get_issue("abc-1", max_comments=2)
    assert issue.description == "h3. Details"
    assert issue.labels == ["ops"]
    assert [c.body for c in issue.comments] == ["note 2", "note 3"]
    assert issue.comment_total == 3


async def test_dry_run_comment_and_transition_send_no_writes(
    fake: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    jira = make_jira()
    comment = await jira.add_comment("ABC-1", "hello")
    match, moved = await jira.transition_issue("ABC-1", "start progress")
    assert comment.sent is False and moved.sent is False
    assert match.id == "11"
    assert moved.payload == {"transition": {"id": "11"}}
    assert fake.writes() == []


async def test_live_transition_posts_resolved_id(
    fake: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    match, outcome = await make_jira(dry_run=False).transition_issue("ABC-1", "Done")
    assert outcome.sent is True and match.to_status == "Done"
    assert json.loads(fake.writes()[0].content) == {"transition": {"id": "21"}}


async def test_unknown_transition_lists_what_is_available(
    fake: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    with pytest.raises(AtlassianError, match="'Start Progress', 'Done'"):
        await make_jira(dry_run=False).transition_issue("ABC-1", "Reopen")
    assert fake.writes() == []


async def test_empty_comment_is_rejected(make_jira: Callable[..., JiraClient]) -> None:
    with pytest.raises(AtlassianError, match="empty"):
        await make_jira().add_comment("ABC-1", "   ")


def _structured(result: Any) -> dict[str, Any]:
    """Pull the structured payload out of a tool call result."""
    assert not result.is_error, result.content
    return dict(result.structured_content)


async def test_server_exposes_tools_with_read_write_annotations(
    make_core: Callable[..., AtlassianClient], make_settings: Callable[..., Settings]
) -> None:
    server = build_server(make_settings(), make_core(), "test")
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert set(tools) == {
        "jira_status",
        "jira_list_presets",
        "jira_run_preset",
        "jira_search",
        "jira_get_issue",
        "jira_list_transitions",
        "jira_add_comment",
        "jira_transition_issue",
        "jira_list_worklogs",
        "jira_log_work",
        "jira_assign_issue",
        "jira_get_edit_fields",
        "jira_edit_issue",
        "jira_get_create_fields",
        "jira_create_issue",
        "jira_list_link_types",
        "jira_link_issues",
    }
    writes = {
        name
        for name, tool in tools.items()
        if tool.annotations is not None and not tool.annotations.read_only_hint
    }
    assert writes == {
        "jira_add_comment",
        "jira_transition_issue",
        "jira_log_work",
        "jira_assign_issue",
        "jira_edit_issue",
        "jira_create_issue",
        "jira_link_issues",
    }


async def test_tools_end_to_end_through_mcp(
    fake: FakeJira,
    make_core: Callable[..., AtlassianClient],
    make_settings: Callable[..., Settings],
) -> None:
    server = build_server(make_settings(), make_core(), "test source")

    status = _structured(await server.call_tool("jira_status", {}))
    assert status["display_name"] == "Pat Example"
    assert status["jira_version"] == "9.12.0"
    assert status["dry_run"] is True
    assert status["extra_cookies"] == ["consent"]

    preset = _structured(await server.call_tool("jira_run_preset", {"name": "mine-open"}))
    assert preset["issues"][0]["key"] == "ABC-1"

    comment = _structured(
        await server.call_tool("jira_add_comment", {"key": "ABC-1", "body": "hi"})
    )
    assert comment["sent"] is False
    assert comment["message"].startswith("DRY RUN")
    assert comment["request"]["body"] == {"body": "hi"}
    assert fake.writes() == []


async def test_tool_errors_reach_the_model_with_their_guidance(
    make_core: Callable[..., AtlassianClient], make_settings: Callable[..., Settings]
) -> None:
    server = build_server(make_settings(), make_core(token="expired"), "test")
    with pytest.raises(ToolError, match="rejected the access token") as info:
        await server.call_tool("jira_search", {"jql": "x"})
    assert not isinstance(info.value, UnexpectedToolError)
    with pytest.raises(ToolError, match=r"Available presets: .*mine-open"):
        await server.call_tool("jira_run_preset", {"name": "nope"})


class RecordingApi:
    """A second ApiClient adapter: no HTTP, just canned answers."""

    base_url = "https://other.example"
    dry_run = True

    def __init__(self) -> None:
        self.reads: list[str] = []

    async def get(self, path: str, params: dict[str, str | int] | None = None) -> JsonValue:
        self.reads.append(path)
        return {"version": "10.0.0"}

    async def get_text(self, path: str, params: dict[str, str | int] | None = None) -> str:
        return "text"

    async def query(self, path: str, payload: dict[str, JsonValue]) -> JsonValue:
        return {"asked": payload}

    async def write(self, method: str, path: str, payload: dict[str, JsonValue]) -> WriteOutcome:
        return WriteOutcome(sent=False, method=method, path=path, payload=payload)

    async def aclose(self) -> None:
        return None


async def test_jira_client_works_with_any_api_adapter(
    make_core: Callable[..., AtlassianClient],
) -> None:
    adapters: list[ApiClient] = [RecordingApi(), make_core()]
    api = adapters[0]
    jira = JiraClient(api, JiraSettings(path=""))
    assert await jira.server_version() == "10.0.0"
    assert await api.get_text("/anything") == "text"
    assert await api.query("/search", {"q": "x"}) == {"asked": {"q": "x"}}
    assert jira.base_url == "https://other.example"
    outcome = await jira.add_comment("ABC-1", "hello")
    assert (outcome.sent, outcome.path) == (False, "/rest/api/2/issue/ABC-1/comment")
    await api.aclose()


async def test_identical_writes_from_an_agent_are_sent_once(
    fake: FakeJira,
    make_core: Callable[..., AtlassianClient],
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(dry_run=False)
    server = build_server(settings, make_core(dry_run=False), "test")
    comment = {"key": "ABC-1", "body": "HONK"}

    both = await asyncio.gather(
        server.call_tool("jira_add_comment", comment),
        server.call_tool("jira_add_comment", comment),
    )
    again = _structured(await server.call_tool("jira_add_comment", comment))
    results = [_structured(result) for result in both]
    assert sorted(r["repeat_suppressed"] for r in results) == [False, True]
    assert all(r["sent"] for r in results)
    assert again["repeat_suppressed"] is True
    assert "not sent a second time" in again["message"]

    other = _structured(
        await server.call_tool("jira_add_comment", {"key": "ABC-1", "body": "different"})
    )
    assert other["repeat_suppressed"] is False

    move = {"key": "ABC-1", "transition": "Done"}
    first = _structured(await server.call_tool("jira_transition_issue", move))
    second = _structured(
        await server.call_tool("jira_transition_issue", {"key": "abc-1", "transition": " done "})
    )
    assert (first["repeat_suppressed"], second["repeat_suppressed"]) == (False, True)

    posts = [request.url.path.rsplit("/", 1)[-1] for request in fake.writes()]
    assert posts == ["comment", "comment", "transitions"]


async def test_dry_run_writes_are_never_treated_as_repeats(
    make_core: Callable[..., AtlassianClient], make_settings: Callable[..., Settings]
) -> None:
    server = build_server(make_settings(), make_core(), "test")
    comment = {"key": "ABC-1", "body": "hi"}
    for _ in range(2):
        result = _structured(await server.call_tool("jira_add_comment", comment))
        assert (result["sent"], result["repeat_suppressed"]) == (False, False)


@pytest.mark.parametrize(
    ("given", "expected"),
    [("1h 30m", "1h 30m"), (" 2D ", "2d"), ("1 h  30 m", "1h 30m"), ("1.5h", "1.5h")],
)
def test_durations_are_tidied(given: str, expected: str) -> None:
    assert normalize_duration(given) == expected


@pytest.mark.parametrize("bad", ["", "90", "1x", "h1", "1h,30m", "an hour"])
def test_bad_durations_explain_the_syntax(bad: str) -> None:
    with pytest.raises(AtlassianError, match="'1h 30m', '2d' or '45m'"):
        normalize_duration(bad)


def test_start_times_are_converted_for_jira() -> None:
    assert jira_time("2026-11-11T14:30:00+01:00") == "2026-11-11T14:30:00.000+0100"
    assert jira_time(" 2026-11-11T14:30Z ") == "2026-11-11T14:30:00.000+0000"
    local = jira_time("2026-11-11T14:30")
    assert local.startswith("2026-11-11T14:30:00.000") and len(local) == 28
    assert jira_time("2026-11-11").startswith("2026-11-11T09:00:00.000")
    with pytest.raises(AtlassianError, match="not an ISO 8601"):
        jira_time("yesterday")
    with pytest.raises(AtlassianError, match="not an ISO 8601"):
        jira_time("2026-13-01")


async def test_worklogs_show_the_newest_and_totals(make_jira: Callable[..., JiraClient]) -> None:
    result = await make_jira().list_worklogs("abc-1", max_results=2)
    assert (result.issue_key, result.total, result.total_seconds, result.returned) == (
        "ABC-1",
        3,
        12600,
        2,
    )
    assert [(w.id, w.author, w.seconds) for w in result.worklogs] == [
        ("2", "sam", 1800),
        ("3", None, 7200),
    ]
    first = (await make_jira().list_worklogs("ABC-1", 3)).worklogs[0]
    assert (first.author, first.time_spent, first.comment) == ("Pat Example", "1h", "triage")
    assert (await make_jira().list_worklogs("ABC-1", 0)).worklogs == []


async def test_log_work_sends_only_what_was_given(
    fake: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    jira = make_jira(dry_run=False)
    await jira.log_work("ABC-1", "1 h 30 m")
    await jira.log_work("ABC-1", "2h", "Fixed *it*", "2026-11-11T09:00:00+00:00")
    await jira.log_work("ABC-1", "15m", "  ", "  ")
    assert [json.loads(r.content) for r in fake.writes()] == [
        {"timeSpent": "1h 30m"},
        {
            "timeSpent": "2h",
            "comment": "Fixed *it*",
            "started": "2026-11-11T09:00:00.000+0000",
        },
        {"timeSpent": "15m"},
    ]


@pytest.mark.parametrize(
    ("person", "username"),
    [
        ("me", "pat"),
        (" None ", None),
        ("unassigned", None),
        ("kim", "kim"),
        ("Jo Lee", "jlee"),
        ("JLEE", "jlee"),
    ],
)
async def test_assignee_is_resolved_against_assignable_users(
    fake: FakeJira, make_jira: Callable[..., JiraClient], person: str, username: str | None
) -> None:
    who = await make_jira().find_assignee("ABC-1", person)
    assert who.username == username
    searches = [r for r in fake.requests if r.url.path.endswith("/assignable/search")]
    if searches:
        assert searches[0].url.params["issueKey"] == "ABC-1"


async def test_ambiguous_or_unknown_assignees_are_refused(
    make_jira: Callable[..., JiraClient],
) -> None:
    with pytest.raises(AtlassianError, match=r"more than one.*Jo Lee \(jlee\), Jamie Leeson"):
        await make_jira().find_assignee("ABC-1", "lee")
    with pytest.raises(AtlassianError, match="Nobody matching 'zed'"):
        await make_jira().find_assignee("ABC-1", "zed")


async def test_long_candidate_lists_are_cut(
    fake: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    many = [{"name": f"user{n}", "displayName": f"User {n}"} for n in range(13)]
    fake.overrides["/jira/rest/api/2/user/assignable/search"] = httpx.Response(200, json=many)
    with pytest.raises(AtlassianError, match=r"User 9 \(user9\) and 3 more"):
        await make_jira().find_assignee("ABC-1", "user")


async def test_worklog_and_assign_tools_end_to_end(
    fake: FakeJira,
    make_core: Callable[..., AtlassianClient],
    make_settings: Callable[..., Settings],
) -> None:
    dry = build_server(make_settings(), make_core(), "test")
    blocked = _structured(
        await dry.call_tool("jira_assign_issue", {"key": "abc-1", "assignee": "kim"})
    )
    assert (blocked["sent"], blocked["request"]["body"]) == (False, {"name": "kim"})
    assert blocked["created_id"] is None
    with pytest.raises(ToolError, match="not a Jira duration"):
        await dry.call_tool("jira_log_work", {"key": "ABC-1", "time_spent": "lots"})
    assert fake.writes() == []

    live = build_server(make_settings(dry_run=False), make_core(dry_run=False), "test")
    work = {"key": "ABC-1", "time_spent": "1h 30m", "comment": "review"}
    logged = _structured(await live.call_tool("jira_log_work", work))
    again = _structured(await live.call_tool("jira_log_work", {**work, "time_spent": "1 H 30 M"}))
    assert (logged["message"], logged["created_id"]) == ("Logged 1h 30m on ABC-1.", "501")
    assert again["repeat_suppressed"] is True

    me = _structured(await live.call_tool("jira_assign_issue", {"key": "ABC-1", "assignee": "me"}))
    myself = _structured(
        await live.call_tool("jira_assign_issue", {"key": "ABC-1", "assignee": "pat"})
    )
    nobody = _structured(
        await live.call_tool("jira_assign_issue", {"key": "ABC-1", "assignee": "none"})
    )
    assert me["message"] == "ABC-1 is now assigned to Pat Example."
    assert myself["repeat_suppressed"] is True  # "me" and "pat" are the same change
    assert nobody["message"] == "ABC-1 is now unassigned."
    assert [json.loads(r.content) for r in fake.writes()][1:] == [
        {"name": "pat"},
        {"name": None},
    ]

    comment = _structured(await live.call_tool("jira_add_comment", {"key": "ABC-1", "body": "x"}))
    assert comment["created_id"] == "99"
    listed = _structured(await live.call_tool("jira_list_worklogs", {"key": "ABC-1"}))
    assert listed["total_seconds"] == 12600
