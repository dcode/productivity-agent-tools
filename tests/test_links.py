"""Tools that join Jira and Bitbucket."""

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from productivity_agent_tools.bitbucket.client import BitbucketClient
from productivity_agent_tools.bitbucket.settings import BitbucketSettings
from productivity_agent_tools.config import Settings, parse_settings
from productivity_agent_tools.credentials import bearer_token
from productivity_agent_tools.errors import AgentToolsError
from productivity_agent_tools.http import AtlassianClient
from productivity_agent_tools.jira.client import JiraClient
from productivity_agent_tools.links import MAX_COMMIT_LOOKUPS, issue_pull_requests
from productivity_agent_tools.server import Connected, build_server
from tests.conftest import FakeJira
from tests.test_bitbucket import FakeBitbucket

DEV = "/jira/rest/dev-status/1.0/issue/detail"


@pytest.fixture
def bitbucket() -> FakeBitbucket:
    return FakeBitbucket()


@pytest.fixture
def bb_api(bitbucket: FakeBitbucket) -> Callable[[], AtlassianClient]:
    def build() -> AtlassianClient:
        return AtlassianClient(
            base_url="https://host.example",
            credentials=bearer_token("bb-token"),
            transport=httpx.MockTransport(bitbucket.handler),
        )

    return build


def _both() -> Settings:
    return parse_settings(
        {
            "base_url": "https://host.example",
            "cookies": {"consent": "yes"},
            "jira": {},
            "bitbucket": {},
        }
    )


def _structured(result: Any) -> dict[str, Any]:
    assert not result.is_error, result.content
    return dict(result.structured_content)


async def test_pull_requests_are_merged_from_both_sources(
    make_jira: Callable[..., JiraClient], bb_api: Callable[[], AtlassianClient]
) -> None:
    bitbucket = BitbucketClient(bb_api(), BitbucketSettings())
    result = await issue_pull_requests(make_jira(), bitbucket, "abc-1")
    assert (result.issue_key, result.sources, result.unavailable) == (
        "ABC-1",
        ["Jira development panel", "Bitbucket commits"],
        [],
    )
    assert (result.commits_checked, result.more_commits) == (2, True)
    by_number = {(p.repository, p.id): p for p in result.pull_requests}
    assert set(by_number) == {("uploader", 42), ("other", 7), (None, None), ("uploader", 43)}
    first = by_number[("uploader", 42)]
    assert first.found_by == ["Jira development panel", "commit abc123d", "commit def789"]
    assert (first.project, first.title, first.state, first.source_branch) == (
        "OPS",
        "ABC-1 retry uploads",
        "OPEN",
        "feature/ABC-1",
    )
    assert by_number[("other", 7)].project is None
    assert by_number[("uploader", 43)].found_by == ["commit def789"]
    assert result.returned == 4


async def test_a_missing_source_is_reported_not_fatal(
    fake: FakeJira,
    bitbucket: FakeBitbucket,
    make_jira: Callable[..., JiraClient],
    bb_api: Callable[[], AtlassianClient],
) -> None:
    fake.overrides[DEV] = httpx.Response(404, json={"errorMessages": ["Not found"]})
    bitbucket.issue_commits = httpx.Response(404, json={"errors": [{"message": "No link"}]})
    client = BitbucketClient(bb_api(), BitbucketSettings())
    result = await issue_pull_requests(make_jira(), client, "ABC-1")
    assert (result.sources, result.returned) == ([], 0)
    assert result.unavailable[0].startswith("Jira development panel:")
    assert "probably not linked to Jira" in result.unavailable[1]


async def test_unexpected_failures_are_not_hidden(
    fake: FakeJira, make_jira: Callable[..., JiraClient], bb_api: Callable[[], AtlassianClient]
) -> None:
    class Broken(BitbucketClient):
        async def issue_commits(self, issue_key: str, max_results: int | None = None) -> Any:
            raise RuntimeError("bug")

    with pytest.raises(RuntimeError, match="bug"):
        await issue_pull_requests(make_jira(), Broken(bb_api(), BitbucketSettings()), "ABC-1")

    class BrokenLookup(BitbucketClient):
        async def commit_pull_requests(self, project: str, repository: str, commit_id: str) -> Any:
            raise RuntimeError("lookup bug")

    with pytest.raises(RuntimeError, match="lookup bug"):
        await issue_pull_requests(make_jira(), BrokenLookup(bb_api(), BitbucketSettings()), "ABC-1")

    class BrokenJira(JiraClient):
        async def development_pull_requests(self, key: str) -> Any:
            raise RuntimeError("jira bug")

    jira = make_jira()
    broken = BrokenJira(jira._core, jira.settings)
    with pytest.raises(RuntimeError, match="jira bug"):
        await issue_pull_requests(broken, BitbucketClient(bb_api(), BitbucketSettings()), "ABC-1")


async def test_commit_lookups_are_bounded(
    bitbucket: FakeBitbucket,
    make_jira: Callable[..., JiraClient],
    bb_api: Callable[[], AtlassianClient],
) -> None:
    many = [
        {
            "repository": {"slug": "uploader", "project": {"key": "OPS"}},
            "toCommit": {"id": f"{n:06x}"},
        }
        for n in range(MAX_COMMIT_LOOKUPS + 5)
    ]
    bitbucket.issue_commits = httpx.Response(200, json={"values": many, "isLastPage": True})
    result = await issue_pull_requests(
        make_jira(), BitbucketClient(bb_api(), BitbucketSettings()), "ABC-1"
    )
    assert (result.commits_checked, result.more_commits) == (MAX_COMMIT_LOOKUPS, True)
    assert result.unavailable == [
        f"Bitbucket commits: the pull requests of {MAX_COMMIT_LOOKUPS} of "
        f"{MAX_COMMIT_LOOKUPS} commits could not be read"
    ]
    lookups = [r for r in bitbucket.requests if r.url.path.endswith("/pull-requests")]
    assert len(lookups) == MAX_COMMIT_LOOKUPS


async def test_issue_commits_validate_the_key_and_ask_for_no_changes(
    bitbucket: FakeBitbucket, bb_api: Callable[[], AtlassianClient]
) -> None:
    client = BitbucketClient(bb_api(), BitbucketSettings())
    with pytest.raises(AgentToolsError, match="not a valid issue key"):
        await client.issue_commits("nope")
    commits, more = await client.issue_commits(" abc-1 ", 5)
    assert dict(bitbucket.requests[0].url.params) == {"limit": "5", "maxChanges": "0"}
    assert [(c.short_id, c.author, c.message) for c in commits] == [
        ("abc123d", "sauthor", "ABC-1 retry"),
        (None, None, ""),
    ]
    assert (commits[0].authored, more) == ("2026-11-10T08:40:00+00:00", True)


async def test_development_panel_needs_the_issue_id(
    fake: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    fake.overrides["/jira/rest/api/2/issue/ABC-1"] = httpx.Response(200, json={"key": "ABC-1"})
    with pytest.raises(AgentToolsError, match="internal id of ABC-1"):
        await make_jira().development_pull_requests("ABC-1")


async def test_describe_issues_uses_one_search_and_skips_bad_keys(
    fake: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    describe = make_jira().describe_issues
    assert await describe(["bad key", "abc-1"]) == {}
    assert fake.requests == []
    details = await describe(["ABC-1", "XYZ-2", "ABC-1"])
    (search,) = fake.requests
    assert search.url.params["jql"] == "key in (ABC-1, XYZ-2)"
    assert (details["ABC-1"].summary, details["ABC-1"].status) == ("Fix the thing", "To Do")


async def test_tools_join_up_only_when_both_products_are_configured(
    fake: FakeJira,
    make_core: Callable[..., AtlassianClient],
    bb_api: Callable[[], AtlassianClient],
) -> None:
    jira_only = build_server(_both(), {"jira": Connected(make_core(), "jira")})
    names = {t.name for t in await jira_only.list_tools()}
    assert "jira_get_issue_pull_requests" not in names
    assert "jira_get_issue_pull_requests" not in (jira_only.instructions or "")

    server = build_server(
        _both(), {"jira": Connected(make_core(), "jira"), "bitbucket": Connected(bb_api())}
    )
    assert "jira_get_issue_pull_requests" in (server.instructions or "")
    linked = _structured(await server.call_tool("jira_get_issue_pull_requests", {"key": "ABC-1"}))
    assert linked["returned"] == 4

    pull = {"project": "OPS", "repository": "uploader", "pull_request_id": 42}
    issues = _structured(await server.call_tool("bitbucket_get_pull_request_issues", pull))
    assert issues["issues"] == [
        {
            "key": "ABC-1",
            "url": "https://host.example/jira/browse/ABC-1",
            "summary": "Fix the thing",
            "status": "To Do",
            "assignee": "Pat Example",
        }
    ]

    fake.overrides["/jira/rest/api/2/search"] = httpx.Response(
        400, json={"errorMessages": ["An issue with key 'ABC-1' does not exist"]}
    )
    plain = _structured(await server.call_tool("bitbucket_get_pull_request_issues", pull))
    assert plain["issues"][0]["summary"] is None
    assert "does not exist" in plain["details_note"]


async def test_issues_jira_does_not_return_stay_as_keys(
    fake: FakeJira,
    make_core: Callable[..., AtlassianClient],
    bitbucket: FakeBitbucket,
    bb_api: Callable[[], AtlassianClient],
) -> None:
    bitbucket.issues = httpx.Response(200, json=[{"key": "XYZ-2"}])
    server = build_server(
        _both(), {"jira": Connected(make_core(), "jira"), "bitbucket": Connected(bb_api())}
    )
    pull = {"project": "OPS", "repository": "uploader", "pull_request_id": 42}
    issues = _structured(await server.call_tool("bitbucket_get_pull_request_issues", pull))
    assert issues["issues"] == [
        {"key": "XYZ-2", "url": None, "summary": None, "status": None, "assignee": None}
    ]
    bitbucket.issues = httpx.Response(200, json=[])
    empty = _structured(await server.call_tool("bitbucket_get_pull_request_issues", pull))
    assert empty["issues"] == []
