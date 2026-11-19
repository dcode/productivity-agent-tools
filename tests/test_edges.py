"""Error paths and malformed-response handling across the shared core and Jira."""

import ssl
from collections.abc import Callable
from pathlib import Path

import certifi
import httpx
import keyring
import keyring.errors
import pytest
import truststore

from productivity_agent_tools import transport
from productivity_agent_tools.config import Settings, load_settings, parse_settings
from productivity_agent_tools.credentials import bearer_token
from productivity_agent_tools.errors import (
    ApiError,
    ConfigError,
    ConnectivityError,
    PermissionDeniedError,
)
from productivity_agent_tools.http import AtlassianClient
from productivity_agent_tools.jira.client import JiraClient
from productivity_agent_tools.jira.settings import JiraSettings
from productivity_agent_tools.server import Connected, build_server
from productivity_agent_tools.tokens import TokenSources, resolve_token, store_token
from tests.conftest import FakeJira

API = "/jira/rest/api/2"


@pytest.mark.parametrize(
    "raw",
    [
        {"base_url": 5},
        {"base_url": "https://host.example", "timeout_seconds": 0},
        {"base_url": "https://host.example", "timeout_seconds": True},
        {"base_url": "https://host.example", "jira": "nope"},
    ],
)
def test_more_invalid_config_is_rejected(raw: dict[str, object]) -> None:
    with pytest.raises(ConfigError):
        parse_settings(raw)


async def test_unreadable_and_malformed_config_files(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Could not read"):
        await load_settings(tmp_path)
    bad = tmp_path / "bad.toml"
    bad.write_text("base_url = ")
    with pytest.raises(ConfigError, match="not valid TOML"):
        await load_settings(bad)


async def test_jira_at_site_root_uses_unprefixed_urls() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"total": 0, "issues": [{"key": "ABC-1"}]})

    async with AtlassianClient(
        base_url="https://jira.example",
        credentials=bearer_token("t"),
        transport=httpx.MockTransport(handler),
    ) as core:
        jira = JiraClient(core, JiraSettings(path=""))
        result = await jira.search("x")
    assert paths == ["/rest/api/2/search"]
    assert jira.base_url == "https://jira.example"
    assert result.issues[0].url == "https://jira.example/browse/ABC-1"


def test_system_trust_store_is_the_default() -> None:
    assert isinstance(transport.build_ssl_context(None), truststore.SSLContext)


async def test_client_builds_real_tls_contexts() -> None:
    async with AtlassianClient(
        base_url="https://host.example", credentials=bearer_token("t")
    ) as default:
        assert default.dry_run is True
    bundle = Path(certifi.where())
    async with AtlassianClient(
        base_url="https://host.example", credentials=bearer_token("t"), ca_bundle=bundle
    ):
        pass


def _core_raising(error: Exception) -> AtlassianClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    return AtlassianClient(
        base_url="https://host.example",
        credentials=bearer_token("t"),
        transport=httpx.MockTransport(handler),
    )


async def test_timeout_and_untrusted_certificate_messages() -> None:
    async with _core_raising(httpx.ReadTimeout("slow")) as core:
        with pytest.raises(ConnectivityError, match="Timed out"):
            await core.get("/x")
    chained = httpx.ConnectError("handshake failed")
    chained.__cause__ = ssl.SSLCertVerificationError("unable to get local issuer")
    by_text = httpx.ConnectError("('\u201chost\u201d certificate is not trusted',)")
    for tls in (chained, by_text):
        async with _core_raising(tls) as core:
            with pytest.raises(ConnectivityError, match="system trust store"):
                await core.get("/x")


@pytest.mark.parametrize(
    ("response", "error", "message"),
    [
        (httpx.Response(403, json={"message": "No access"}), PermissionDeniedError, "No access"),
        (httpx.Response(403, json=["x"]), PermissionDeniedError, "permission"),
        (httpx.Response(500, text="boom"), ApiError, "HTTP 500"),
        (httpx.Response(400, json={"errors": {"body": "too long"}}), ApiError, "body: too long"),
        (httpx.Response(302, headers={"location": "/login"}), ApiError, "login page"),
        (httpx.Response(200, text="hello"), ApiError, "Expected JSON"),
    ],
)
async def test_unusual_responses_become_clear_errors(
    response: httpx.Response,
    error: type[Exception],
    message: str,
    fake: FakeJira,
    make_core: Callable[..., AtlassianClient],
) -> None:
    fake.overrides[f"{API}/myself"] = response
    with pytest.raises(error, match=message):
        await make_core().get(f"{API}/myself")


async def test_empty_success_body_is_none(
    fake: FakeJira, make_core: Callable[..., AtlassianClient]
) -> None:
    fake.overrides[f"{API}/myself"] = httpx.Response(200)
    assert await make_core().get(f"{API}/myself") is None


async def test_malformed_jira_payloads_do_not_crash(
    fake: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    jira = make_jira()
    fake.overrides[f"{API}/serverInfo"] = httpx.Response(200, json={"version": {"n": 1}})
    assert await jira.server_version() is None
    fake.overrides[f"{API}/serverInfo"] = httpx.Response(200, json=["9.12.0"])
    assert await jira.server_version() is None

    fake.overrides[f"{API}/issue/ABC-1/transitions"] = httpx.Response(
        200, json={"transitions": "none"}
    )
    assert await jira.list_transitions("ABC-1") == []

    fake.overrides[f"{API}/search"] = httpx.Response(200, json={"issues": [{"key": "ABC-2"}, 7]})
    result = await jira.search("x")
    assert (result.total, result.issues[0].status) == (1, "Unknown")
    fake.overrides[f"{API}/search"] = httpx.Response(200, json=[])
    with pytest.raises(ApiError, match="unexpected search"):
        await jira.search("x")

    fake.overrides[f"{API}/issue/ABC-1"] = httpx.Response(200, json={"key": "ABC-1"})
    bare = await jira.get_issue("ABC-1", max_comments=0)
    assert (bare.comments, bare.comment_total, bare.labels) == ([], 0, [])


async def test_remaining_tools_through_mcp(
    fake: FakeJira,
    make_core: Callable[..., AtlassianClient],
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(dry_run=False)
    server = build_server(settings, {"jira": Connected(make_core(dry_run=False), "test")})

    async def call(name: str, **arguments: str) -> object:
        result = await server.call_tool(name, dict(arguments))
        return getattr(result, "structured_content", None)

    assert await call("jira_search", jql="project = ABC") is not None
    assert await call("jira_get_issue", key="ABC-1") is not None
    assert await call("jira_list_transitions", key="ABC-1") is not None
    assert await call("jira_list_presets") is not None
    moved = await call("jira_transition_issue", key="abc-1", transition="11")
    assert isinstance(moved, dict)
    assert moved["sent"] is True
    assert moved["message"] == "ABC-1 moved via 'Start Progress' (now 'In Progress')."
    commented = await call("jira_add_comment", key="ABC-1", body="done")
    assert isinstance(commented, dict)
    assert commented["message"] == "Comment added to ABC-1."
    assert len(fake.writes()) == 2


async def test_keychain_fallbacks_and_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(keyring, "set_password", lambda s, u, p: saved.update({(s, u): p}))
    monkeypatch.setattr(keyring, "get_password", lambda s, u: saved.get((s, u)))
    await store_token("jira", "kept")

    other = tmp_path / "other.env"
    other.write_text("SOMETHING_ELSE=1\n")
    token = await resolve_token("jira", TokenSources(env_file=other), {})
    assert (token.value, token.source) == ("kept", "the system keychain")

    def broken(service: str, user: str) -> str:
        raise keyring.errors.KeyringError("locked")

    monkeypatch.setattr(keyring, "get_password", broken)
    with pytest.raises(Exception, match="No jira token found"):
        await resolve_token("jira", None, {})
