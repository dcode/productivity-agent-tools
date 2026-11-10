import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver import MCPServer

from productivity_agent_tools import cli
from productivity_agent_tools.bitbucket.client import BitbucketClient
from productivity_agent_tools.config import BitbucketSettings, Settings, parse_settings
from productivity_agent_tools.errors import (
    AtlassianError,
    AuthenticationError,
    ConfigError,
    TokenNotFoundError,
)
from productivity_agent_tools.http import AtlassianClient
from productivity_agent_tools.server import build_server
from tests.conftest import FakeJira

API = "/bitbucket/rest/api/1.0"
PULL: dict[str, Any] = {
    "id": 42,
    "title": "Add retry to the uploader",
    "description": "Retries **three** times.",
    "state": "OPEN",
    "createdDate": 1794300000000,
    "updatedDate": 1794330000000,
    "fromRef": {"displayId": "feature/retry", "latestCommit": "abc123"},
    "toRef": {
        "displayId": "main",
        "repository": {"slug": "uploader", "project": {"key": "OPS"}},
    },
    "author": {"user": {"displayName": "Sam Author"}},
    "reviewers": [
        {"user": {"displayName": "Pat Example"}, "status": "UNAPPROVED"},
        {"user": {"name": "rlee"}, "status": "APPROVED"},
    ],
    "properties": {"commentCount": 3, "openTaskCount": 1},
    "links": {
        "self": [
            {"href": "https://host.example/bitbucket/projects/OPS/repos/uploader/pull-requests/42"}
        ]
    },
}
ACTIVITIES: dict[str, Any] = {
    "values": [
        {"action": "APPROVED"},
        {
            "action": "COMMENTED",
            "comment": {
                "id": 3,
                "text": "newest",
                "author": {"displayName": "Pat Example"},
                "createdDate": 1794329000000,
            },
            "commentAnchor": {"path": "src/upload.py"},
        },
        {"action": "COMMENTED", "comment": {"id": 2, "text": "middle"}},
        {"action": "COMMENTED", "comment": {"id": 1, "text": "oldest"}},
    ]
}


PROJECT: dict[str, Any] = {
    "key": "OPS",
    "name": "Cloud Operations",
    "description": "Runs the cloud",
    "links": {"self": [{"href": "https://host.example/bitbucket/projects/OPS"}]},
}
REPOSITORY: dict[str, Any] = {
    "slug": "uploader",
    "name": "Uploader",
    "project": {"key": "OPS"},
    "links": {"self": [{"href": "https://host.example/bitbucket/projects/OPS/repos/uploader"}]},
}
COMMITS: list[dict[str, Any]] = [
    {
        "id": "abc123def456",
        "displayId": "abc123d",
        "author": {"name": "sauthor", "displayName": "Sam Author"},
        "authorTimestamp": 1794300000000,
        "message": "Retry uploads",
    },
    {"id": "000111", "author": {"name": "sauthor"}, "message": "Add test"},
]
CHANGES: list[dict[str, Any]] = [
    {"path": {"toString": "src/upload.py"}, "type": "MODIFY"},
    {
        "path": {"toString": "docs/retry.md"},
        "type": "MOVE",
        "srcPath": {"toString": "docs/old.md"},
    },
]
DIFF = "diff --git a/src/upload.py b/src/upload.py\n-old\n+new\n"


SEARCH: dict[str, Any] = {
    "scope": {"type": "GLOBAL"},
    "code": {
        "category": "primary",
        "isLastPage": False,
        "count": 37,
        "values": [
            {
                "repository": {"slug": "uploader", "project": {"key": "OPS"}},
                "file": "src/upload.py",
                "hitCount": 2,
                "hitContexts": [
                    [
                        {"line": 10, "text": "def <em>retry</em>(times):"},
                        {"line": 11, "text": "    if a &lt; b:"},
                    ],
                    [{"line": 40, "text": "<em>retry</em>(3)"}],
                ],
            },
            {"repository": {"slug": "docs"}, "file": "README.md", "hitContexts": None},
        ],
    },
}


class FakeBitbucket:
    """Answers the Bitbucket endpoints the client uses."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.builds: httpx.Response = httpx.Response(
            200,
            json={"values": [{"state": "FAILED", "key": "unit", "url": "https://ci.example/1"}]},
        )
        self.last_page = True
        self.diff: httpx.Response = httpx.Response(200, text=DIFF)
        self.search: httpx.Response = httpx.Response(200, json=SEARCH)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("authorization") != "Bearer bb-token":
            return httpx.Response(
                401, json={"errors": [{"message": "Authentication failed", "context": None}]}
            )
        path = request.url.path
        if path == f"{API}/application-properties":
            return httpx.Response(200, json={"version": "9.4.2"})
        if path == f"{API}/dashboard/pull-requests":
            return httpx.Response(200, json={"values": [PULL], "isLastPage": self.last_page})
        if path == "/bitbucket/rest/search/1.0/search" and request.method == "POST":
            return self.search
        if path == f"{API}/projects":
            return httpx.Response(200, json={"values": [PROJECT], "isLastPage": True})
        if path == f"{API}/projects/OPS":
            return httpx.Response(200, json=PROJECT)
        if path == f"{API}/projects/OPS/repos":
            return httpx.Response(200, json={"values": [REPOSITORY], "isLastPage": False})
        if path == f"{API}/projects/OPS/repos/uploader/pull-requests":
            return httpx.Response(200, json={"values": [PULL], "isLastPage": True})
        base = f"{API}/projects/OPS/repos/uploader/pull-requests/42"
        if path == base:
            return httpx.Response(200, json=PULL)
        if path == f"{base}/commits":
            return httpx.Response(200, json={"values": COMMITS, "isLastPage": True})
        if path == f"{base}/changes":
            return httpx.Response(200, json={"values": CHANGES, "isLastPage": True})
        if path == f"{base}.diff":
            return self.diff
        if path == f"{base}/activities":
            return httpx.Response(200, json=ACTIVITIES)
        if path == "/bitbucket/rest/build-status/1.0/commits/abc123":
            return self.builds
        return httpx.Response(
            404, json={"errors": [{"message": "Pull request 7 does not exist in OPS/uploader."}]}
        )


@pytest.fixture
def bitbucket() -> FakeBitbucket:
    return FakeBitbucket()


@pytest.fixture
async def make_api(bitbucket: FakeBitbucket) -> AsyncIterator[Callable[..., AtlassianClient]]:
    created: list[AtlassianClient] = []

    def build(token: str = "bb-token") -> AtlassianClient:
        api = AtlassianClient(
            base_url="https://host.example",
            token=token,
            transport=httpx.MockTransport(bitbucket.handler),
        )
        created.append(api)
        return api

    yield build
    for api in created:
        await api.aclose()


@pytest.fixture
def client(make_api: Callable[..., AtlassianClient]) -> BitbucketClient:
    return BitbucketClient(make_api(), BitbucketSettings())


async def test_list_asks_for_role_and_state_and_returns_compact_rows(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    result = await client.list_pull_requests(" Reviewer ", "OPEN", 500)
    assert dict(bitbucket.requests[0].url.params) == {
        "role": "REVIEWER",
        "limit": "100",
        "order": "NEWEST",
        "state": "OPEN",
    }
    assert (result.returned, result.more_available) == (1, False)
    row = result.pull_requests[0]
    assert (row.project, row.repository, row.id) == ("OPS", "uploader", 42)
    assert (row.source_branch, row.target_branch) == ("feature/retry", "main")
    assert [(r.name, r.status) for r in row.reviewers] == [
        ("Pat Example", "UNAPPROVED"),
        ("rlee", "APPROVED"),
    ]
    assert (row.comment_count, row.open_task_count) == (3, 1)
    assert row.updated == "2026-11-10T17:00:00+00:00"
    assert row.url is not None and row.url.endswith("/pull-requests/42")


async def test_all_states_omits_the_filter_and_more_pages_are_flagged(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    bitbucket.last_page = False
    result = await client.list_pull_requests("author", "all")
    params = dict(bitbucket.requests[0].url.params)
    assert "state" not in params
    assert (params["role"], params["limit"]) == ("AUTHOR", "25")
    assert result.more_available is True


async def test_detail_has_newest_comments_oldest_first_and_builds(
    client: BitbucketClient,
) -> None:
    detail = await client.get_pull_request("uploader", 42, "OPS", max_comments=2)
    assert detail.description == "Retries **three** times."
    assert detail.created == "2026-11-10T08:40:00+00:00"
    assert detail.latest_commit == "abc123"
    assert [c.text for c in detail.comments] == ["middle", "newest"]
    assert (detail.comments[1].author, detail.comments[1].file) == ("Pat Example", "src/upload.py")
    assert [(b.state, b.name, b.url) for b in detail.builds] == [
        ("FAILED", "unit", "https://ci.example/1")
    ]
    assert detail.builds_available is True


async def test_no_comments_requested_skips_the_activity_call(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    detail = await client.get_pull_request("uploader", 42, "OPS", max_comments=0)
    assert detail.comments == []
    assert not any(r.url.path.endswith("/activities") for r in bitbucket.requests)


async def test_unreadable_builds_are_reported_as_unknown_not_as_a_failure(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    bitbucket.builds = httpx.Response(404, json={"errors": [{"message": "no such plugin"}]})
    detail = await client.get_pull_request("uploader", 42, "OPS")
    assert (detail.builds, detail.builds_available) == ([], False)


@pytest.mark.parametrize(
    ("project", "repository", "number"),
    [("..", "uploader", 42), ("OPS", "a/b", 42), ("OPS", "", 42), ("OPS", "uploader", 0)],
)
async def test_identifiers_are_validated_before_any_request(
    bitbucket: FakeBitbucket, client: BitbucketClient, project: str, repository: str, number: int
) -> None:
    with pytest.raises(AtlassianError, match="not a valid"):
        await client.get_pull_request(repository, number, project)
    assert bitbucket.requests == []


async def test_bad_role_and_state_list_the_choices(client: BitbucketClient) -> None:
    with pytest.raises(AtlassianError, match="reviewer, author, participant"):
        await client.list_pull_requests("owner")
    with pytest.raises(AtlassianError, match="open, merged, declined, all"):
        await client.list_pull_requests(state="closed")


async def test_bitbucket_error_messages_reach_the_user(
    make_api: Callable[..., AtlassianClient], client: BitbucketClient
) -> None:
    with pytest.raises(AtlassianError, match=r"Pull request 7 does not exist in OPS/uploader"):
        await client.get_pull_request("uploader", 7, "OPS")
    expired = BitbucketClient(make_api(token="old"), BitbucketSettings())
    with pytest.raises(AuthenticationError, match="Authentication failed"):
        await expired.server_version()


def _settings(**bitbucket: Any) -> Settings:
    return parse_settings(
        {"base_url": "https://host.example", "cookies": {"consent": "yes"}, "bitbucket": bitbucket}
    )


def test_bitbucket_is_off_until_its_table_exists() -> None:
    assert parse_settings({"base_url": "https://host.example"}).bitbucket is None
    settings = _settings()
    assert settings.bitbucket == BitbucketSettings()
    assert settings.connection_for(settings.bitbucket).base_url == "https://host.example"


def test_bitbucket_table_overrides_and_is_validated() -> None:
    settings = _settings(base_url="https://git.example", path="", default_max_results=5, cookies={})
    assert settings.bitbucket is not None
    assert (settings.bitbucket.path, settings.bitbucket.default_max_results) == ("", 5)
    connection = settings.connection_for(settings.bitbucket)
    assert (connection.base_url, connection.cookies) == ("https://git.example", {})
    for bad in ({"default_max_results": 0}, {"base_url": "http://git.example"}):
        with pytest.raises(ConfigError, match="bitbucket"):
            _settings(**bad)
    with pytest.raises(ConfigError, match="'bitbucket' must be a table"):
        parse_settings({"base_url": "https://host.example", "bitbucket": "yes"})


def _structured(result: Any) -> dict[str, Any]:
    assert not result.is_error, result.content
    return dict(result.structured_content)


async def test_tools_are_registered_only_when_bitbucket_is_connected(
    fake: FakeJira,
    make_core: Callable[..., AtlassianClient],
    make_api: Callable[..., AtlassianClient],
) -> None:
    settings = _settings()
    without = build_server(settings, make_core(), "jira source")
    assert not [t.name for t in await without.list_tools() if t.name.startswith("bitbucket_")]
    assert "bitbucket_" not in (without.instructions or "")

    server = build_server(
        settings,
        make_core(),
        "jira source",
        bitbucket_api=make_api(),
        bitbucket_token_source="bb source",
    )
    tools = {t.name: t for t in await server.list_tools() if t.name.startswith("bitbucket_")}
    assert sorted(tools) == [
        "bitbucket_get_pull_request",
        "bitbucket_get_pull_request_changes",
        "bitbucket_get_pull_request_commits",
        "bitbucket_get_pull_request_diff",
        "bitbucket_list_projects",
        "bitbucket_list_pull_requests",
        "bitbucket_list_repositories",
        "bitbucket_list_repository_pull_requests",
        "bitbucket_search_code",
        "bitbucket_set_default_project",
        "bitbucket_status",
    ]
    not_read_only = [
        name
        for name, tool in tools.items()
        if tool.annotations is None or not tool.annotations.read_only_hint
    ]
    assert not_read_only == ["bitbucket_set_default_project"]
    assert "bitbucket_" in (server.instructions or "")

    status = _structured(await server.call_tool("bitbucket_status", {}))
    assert status == {
        "base_url": "https://host.example/bitbucket",
        "bitbucket_version": "9.4.2",
        "token_source": "bb source",
        "authenticated": True,
        "default_project": None,
        "extra_cookies": ["consent"],
    }
    listed = _structured(await server.call_tool("bitbucket_list_pull_requests", {}))
    assert listed["pull_requests"][0]["title"] == "Add retry to the uploader"
    detail = _structured(
        await server.call_tool(
            "bitbucket_get_pull_request",
            {"project": "OPS", "repository": "uploader", "pull_request_id": 42},
        )
    )
    assert detail["builds"][0]["state"] == "FAILED"

    chosen = _structured(
        await server.call_tool("bitbucket_set_default_project", {"project": "OPS"})
    )
    assert (chosen["key"], chosen["name"]) == ("OPS", "Cloud Operations")
    assert "until the server restarts" in chosen["message"]
    assert _structured(await server.call_tool("bitbucket_status", {}))["default_project"] == "OPS"
    for tool, key in [
        ("bitbucket_list_repositories", "repositories"),
        ("bitbucket_list_projects", "projects"),
    ]:
        assert _structured(await server.call_tool(tool, {}))[key]
    pull = {"repository": "uploader", "pull_request_id": 42}
    for tool, key in [
        ("bitbucket_get_pull_request", "title"),
        ("bitbucket_get_pull_request_commits", "commits"),
        ("bitbucket_get_pull_request_changes", "files"),
        ("bitbucket_get_pull_request_diff", "diff"),
    ]:
        assert _structured(await server.call_tool(tool, pull))[key]
    listed_in_repo = _structured(
        await server.call_tool(
            "bitbucket_list_repository_pull_requests", {"repository": "uploader"}
        )
    )
    assert listed_in_repo["project"] == "OPS"


def _both(fake: FakeJira, bitbucket: FakeBitbucket) -> httpx.MockTransport:
    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/bitbucket/"):
            return bitbucket.handler(request)
        return fake.handler(request)

    return httpx.MockTransport(route)


async def test_doctor_checks_bitbucket_when_configured(
    fake: FakeJira,
    bitbucket: FakeBitbucket,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("JIRA_TOKEN", "good-token")
    monkeypatch.setenv("BITBUCKET_TOKEN", "bb-token")
    bitbucket.last_page = False
    assert await cli._doctor(Path("/cfg.toml"), _settings(), _both(fake, bitbucket)) is True
    output = capsys.readouterr().out
    assert "[ok]   bitbucket token found in environment variable BITBUCKET_TOKEN" in output
    assert "[ok]   Bitbucket version 9.4.2 at https://host.example/bitbucket" in output
    assert "[ok]   pull requests readable (1+ open with you as reviewer)" in output

    monkeypatch.setenv("BITBUCKET_TOKEN", "expired")
    assert await cli._doctor(Path("/cfg.toml"), _settings(), _both(fake, bitbucket)) is False
    failed = capsys.readouterr().out
    assert "[ok]   search works" in failed
    assert "[fail] The server rejected the access token" in failed


async def test_doctor_says_when_bitbucket_is_not_configured(
    fake: FakeJira,
    make_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("JIRA_TOKEN", "good-token")
    assert await cli._doctor(Path("/cfg.toml"), make_settings(), httpx.MockTransport(fake.handler))
    assert "[info] bitbucket is not configured" in capsys.readouterr().out


async def test_serve_connects_bitbucket_and_needs_its_token(
    fake: FakeJira, bitbucket: FakeBitbucket, monkeypatch: pytest.MonkeyPatch
) -> None:
    served: list[list[str]] = []

    async def run_stdio(self: MCPServer) -> None:
        served.append([tool.name for tool in await self.list_tools()])

    async def no_keychain(product: str) -> None:
        return None

    monkeypatch.setattr(MCPServer, "run_stdio_async", run_stdio)
    monkeypatch.setattr("productivity_agent_tools.tokens._from_keychain", no_keychain)
    monkeypatch.setenv("JIRA_TOKEN", "good-token")
    monkeypatch.delenv("BITBUCKET_TOKEN", raising=False)
    with pytest.raises(TokenNotFoundError, match="BITBUCKET_TOKEN"):
        await cli._serve(_settings(), _both(fake, bitbucket))

    monkeypatch.setenv("BITBUCKET_TOKEN", "bb-token")
    await cli._serve(_settings(), _both(fake, bitbucket))
    assert "bitbucket_list_pull_requests" in served[0]
    assert "jira_search" in served[0]


def test_set_token_rejects_unknown_products(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        cli.set_token("confluence")
    assert info.value.code == 1
    assert "Use one of: jira, bitbucket" in capsys.readouterr().err


async def test_projects_can_be_listed_and_filtered_by_name(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    result = await client.list_projects(" cloud ", 5)
    assert dict(bitbucket.requests[0].url.params) == {"limit": "5", "name": "cloud"}
    project = result.projects[0]
    assert (project.key, project.name, project.description) == (
        "OPS",
        "Cloud Operations",
        "Runs the cloud",
    )
    assert project.url == "https://host.example/bitbucket/projects/OPS"
    await client.list_projects()
    assert "name" not in dict(bitbucket.requests[1].url.params)


async def test_calls_without_a_project_need_a_default(client: BitbucketClient) -> None:
    with pytest.raises(AtlassianError, match="bitbucket_set_default_project"):
        await client.list_repositories()
    with pytest.raises(AtlassianError, match="default_project under"):
        await client.get_pull_request("uploader", 42)


async def test_default_project_comes_from_config_or_is_chosen_for_the_session(
    make_api: Callable[..., AtlassianClient],
) -> None:
    configured = BitbucketClient(make_api(), BitbucketSettings(default_project="OPS"))
    assert configured.default_project == "OPS"
    assert (await configured.list_repositories()).project == "OPS"

    chosen = BitbucketClient(make_api(), BitbucketSettings())
    with pytest.raises(AtlassianError, match="does not exist"):
        await chosen.set_default_project("NOPE")
    assert chosen.default_project is None
    await chosen.set_default_project(" OPS ")
    repositories = await chosen.list_repositories()
    assert (repositories.returned, repositories.more_available) == (1, True)
    repository = repositories.repositories[0]
    assert (repository.project, repository.slug, repository.name) == ("OPS", "uploader", "Uploader")
    assert repository.url is not None and repository.url.endswith("/repos/uploader")
    assert (await chosen.list_repositories("OPS")).project == "OPS"


def test_default_project_is_read_from_config() -> None:
    settings = _settings(default_project=" CO ")
    assert settings.bitbucket is not None and settings.bitbucket.default_project == "CO"
    assert _settings().bitbucket == BitbucketSettings()


async def test_repository_pull_requests_filter_by_state_and_text(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    result = await client.list_repository_pull_requests("uploader", "OPS", "all", " retry ", 10)
    assert dict(bitbucket.requests[0].url.params) == {
        "state": "ALL",
        "order": "NEWEST",
        "limit": "10",
        "filterText": "retry",
    }
    assert (result.project, result.repository, result.state) == ("OPS", "uploader", "all")
    assert result.pull_requests[0].id == 42
    await client.list_repository_pull_requests("uploader", "OPS")
    assert dict(bitbucket.requests[1].url.params)["state"] == "OPEN"
    assert "filterText" not in dict(bitbucket.requests[1].url.params)


async def test_commits_and_changed_files(client: BitbucketClient) -> None:
    commits = await client.get_pull_request_commits("uploader", 42, "OPS")
    assert [(c.short_id, c.author, c.message) for c in commits.commits] == [
        ("abc123d", "Sam Author", "Retry uploads"),
        (None, "sauthor", "Add test"),
    ]
    assert commits.commits[0].authored == "2026-11-10T08:40:00+00:00"
    changes = await client.get_pull_request_changes("uploader", 42, "OPS")
    assert [(f.path, f.change, f.previous_path) for f in changes.files] == [
        ("src/upload.py", "MODIFY", None),
        ("docs/retry.md", "MOVE", "docs/old.md"),
    ]


async def test_diff_is_requested_as_text_and_cut_to_size(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    whole = await client.get_pull_request_diff("uploader", 42, "OPS", context_lines=99)
    request = bitbucket.requests[0]
    assert request.url.path.endswith("/pull-requests/42.diff")
    assert request.headers["accept"] == "text/plain"
    assert dict(request.url.params) == {"contextLines": "50"}
    assert (whole.diff, whole.total_chars, whole.truncated) == (DIFF, len(DIFF), False)

    cut = await client.get_pull_request_diff("uploader", 42, "OPS", max_chars=10)
    assert (cut.diff, cut.total_chars, cut.truncated) == (DIFF[:10], len(DIFF), True)

    bitbucket.diff = httpx.Response(200)
    empty = await client.get_pull_request_diff("uploader", 42, "OPS")
    assert (empty.diff, empty.truncated) == ("", False)


async def test_code_search_posts_the_query_and_cleans_up_the_hits(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    result = await client.search_code(" retry ext:py ", max_results=500)
    request = bitbucket.requests[0]
    assert request.method == "POST"
    assert json.loads(request.content) == {
        "query": "retry ext:py",
        "entities": {"code": {}},
        "limits": {"primary": 100, "secondary": 10},
    }
    assert (result.query, result.total, result.returned, result.more_available) == (
        "retry ext:py",
        37,
        2,
        True,
    )
    first, second = result.hits
    assert (first.project, first.repository, first.file, first.hit_count) == (
        "OPS",
        "uploader",
        "src/upload.py",
        2,
    )
    assert [(line.line, line.text) for line in first.lines] == [
        (10, "def retry(times):"),
        (11, "    if a < b:"),
        (40, "retry(3)"),
    ]
    assert (
        first.url
        == "https://host.example/bitbucket/projects/OPS/repos/uploader/browse/src/upload.py"
    )
    assert (second.project, second.lines, second.url) == ("", [], None)


async def test_code_search_scopes_only_when_asked_and_runs_in_dry_run(
    bitbucket: FakeBitbucket, make_api: Callable[..., AtlassianClient]
) -> None:
    api = make_api()
    assert api.dry_run is True
    client = BitbucketClient(api, BitbucketSettings(default_project="OPS"))
    await client.search_code("retry")
    await client.search_code("retry", project="XY", repository="uploader")
    queries = [json.loads(request.content)["query"] for request in bitbucket.requests]
    assert queries == ["retry", "retry project:CO repo:uploader"]


async def test_code_search_problems_are_explained(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    with pytest.raises(AtlassianError, match="query is empty"):
        await client.search_code("   ")
    with pytest.raises(AtlassianError, match="not a valid project key"):
        await client.search_code("retry", project="a b")
    assert bitbucket.requests == []

    bitbucket.search = httpx.Response(404, json={"errors": [{"message": "No such resource"}]})
    with pytest.raises(AtlassianError, match="does not document"):
        await client.search_code("retry")

    bitbucket.search = httpx.Response(503)
    with pytest.raises(AtlassianError, match=r"unavailable right now \(HTTP 503\).*one by one"):
        await client.search_code("retry")

    bitbucket.search = httpx.Response(400, json={"errors": [{"message": "Bad query syntax"}]})
    with pytest.raises(AtlassianError, match="Bad query syntax"):
        await client.search_code("retry")

    bitbucket.search = httpx.Response(200, json={"scope": {"type": "GLOBAL"}})
    empty = await client.search_code("retry")
    assert (empty.total, empty.returned, empty.more_available, empty.hits) == (None, 0, False, [])
