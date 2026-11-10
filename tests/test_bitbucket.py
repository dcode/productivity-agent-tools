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


class FakeBitbucket:
    """Answers the Bitbucket endpoints the client uses."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.builds: httpx.Response = httpx.Response(
            200,
            json={"values": [{"state": "FAILED", "key": "unit", "url": "https://ci.example/1"}]},
        )
        self.last_page = True

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
        base = f"{API}/projects/OPS/repos/uploader/pull-requests/42"
        if path == base:
            return httpx.Response(200, json=PULL)
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
    detail = await client.get_pull_request("OPS", "uploader", 42, max_comments=2)
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
    detail = await client.get_pull_request("OPS", "uploader", 42, max_comments=0)
    assert detail.comments == []
    assert not any(r.url.path.endswith("/activities") for r in bitbucket.requests)


async def test_unreadable_builds_are_reported_as_unknown_not_as_a_failure(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    bitbucket.builds = httpx.Response(404, json={"errors": [{"message": "no such plugin"}]})
    detail = await client.get_pull_request("OPS", "uploader", 42)
    assert (detail.builds, detail.builds_available) == ([], False)


@pytest.mark.parametrize(
    ("project", "repository", "number"),
    [("..", "uploader", 42), ("OPS", "a/b", 42), ("OPS", "", 42), ("OPS", "uploader", 0)],
)
async def test_identifiers_are_validated_before_any_request(
    bitbucket: FakeBitbucket, client: BitbucketClient, project: str, repository: str, number: int
) -> None:
    with pytest.raises(AtlassianError, match="not a valid"):
        await client.get_pull_request(project, repository, number)
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
        await client.get_pull_request("OPS", "uploader", 7)
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
        "bitbucket_list_pull_requests",
        "bitbucket_status",
    ]
    assert all(t.annotations is not None and t.annotations.read_only_hint for t in tools.values())
    assert "bitbucket_" in (server.instructions or "")

    status = _structured(await server.call_tool("bitbucket_status", {}))
    assert status == {
        "base_url": "https://host.example/bitbucket",
        "bitbucket_version": "9.4.2",
        "token_source": "bb source",
        "authenticated": True,
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
