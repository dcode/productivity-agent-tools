"""Tests for the ServiceNow adapter, session storage, sign-in flow and product."""

import dataclasses
import sys
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

import httpx
import keyring
import keyring.errors
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult
from pydantic import JsonValue

from productivity_agent_tools import cli
from productivity_agent_tools.browser import BrowserCookie
from productivity_agent_tools.config import Connection, Settings, parse_settings
from productivity_agent_tools.credentials import bearer_token
from productivity_agent_tools.errors import (
    AgentToolsError,
    ApiError,
    AuthenticationError,
    ConfigError,
    ConnectivityError,
    InterstitialPageError,
    NotFoundError,
    PermissionDeniedError,
)
from productivity_agent_tools.products import installed_products
from productivity_agent_tools.server import Connected, build_server
from productivity_agent_tools.servicenow.client import ServiceNow
from productivity_agent_tools.servicenow.http import ServiceNowClient
from productivity_agent_tools.servicenow.login import sign_in
from productivity_agent_tools.servicenow.product import PRODUCT, doctor
from productivity_agent_tools.servicenow.session import (
    BrowserSession,
    browser_session,
    load_session,
    store_session,
)
from productivity_agent_tools.servicenow.settings import (
    ServiceNowSettings,
    parse_servicenow_settings,
)
from productivity_agent_tools.tokens import TokenSources
from productivity_agent_tools.transport import HttpAdapter

BASE = "https://desk.example"
TOKEN = "ab" * 36
GOOD = "JSESSIONID=good"
ME = {"sys_id": "u1", "user_name": "pat.example", "name": "Pat Example", "email": "p@x.example"}


class FakeServiceNow:
    """An instance that knows one session: the cookie ``GOOD`` plus the token ``TOKEN``."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.accept_token = True

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        known = GOOD in request.headers.get("cookie", "")
        proven = known and request.headers.get("x-usertoken") == TOKEN and self.accept_token
        who = {"x-is-logged-in": "true" if known else "false"}
        path = request.url.path
        if path == "/home":
            if not known:
                return httpx.Response(302, headers={"location": "https://sso.example/in?r=1"})
            return httpx.Response(
                200, headers={"content-type": "text/html"}, text=f"<script>g_ck = '{TOKEN}';"
            )
        if path == "/blank":
            return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>hi</p>")
        if not proven:
            body = {"error": {"message": "User Not Authenticated", "detail": "Required"}}
            return httpx.Response(401, headers=who, json=body)
        if path == "/api/now/table/sys_user":
            return httpx.Response(200, headers=who, json={"result": [ME]})
        if path == "/api/empty-user":
            return httpx.Response(200, headers=who, json={"result": []})
        if path == "/api/forbidden":
            return httpx.Response(403, json={"error": {"message": "ACL", "detail": ""}})
        if path == "/api/missing":
            return httpx.Response(404, json={"error": "No Record found"})
        if path == "/api/broken":
            return httpx.Response(500, text="oops")
        if path == "/api/odd-error":
            return httpx.Response(400, json=["not", "an", "object"])
        if path == "/api/odder-error":
            return httpx.Response(400, json={"error": 7})
        if path == "/api/moved":
            return httpx.Response(302, headers={"location": "https://sso.example/in?SAML=x"})
        if path == "/api/guest":
            return httpx.Response(200, headers={"x-is-logged-in": "false"}, json={"data": None})
        if path == "/api/page":
            return httpx.Response(200, headers={"content-type": "text/html"}, text="<html>")
        if path == "/api/nothing":
            return httpx.Response(204)
        if path == "/api/csv":
            return httpx.Response(200, headers={"content-type": "text/csv"}, text="a,b")
        if path == "/api/untyped":
            return httpx.Response(200, content=b"??")
        return httpx.Response(201, headers=who, json={"result": {"path": path}})


@pytest.fixture
def instance() -> FakeServiceNow:
    return FakeServiceNow()


@pytest.fixture
def keychain(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], str]:
    saved: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(keyring, "set_password", lambda s, u, p: saved.update({(s, u): p}))
    monkeypatch.setattr(keyring, "get_password", lambda s, u: saved.get((s, u)))
    return saved


def make_api(
    instance: FakeServiceNow, cookie: str = GOOD, *, dry_run: bool = True
) -> ServiceNowClient:
    session = BrowserSession.collected_now(cookie, TOKEN)

    async def headers() -> dict[str, str]:
        return session.headers()

    return ServiceNowClient(
        base_url=BASE,
        credentials=headers,
        dry_run=dry_run,
        transport=httpx.MockTransport(instance.handler),
    )


# --- settings ---------------------------------------------------------------------------


def test_settings_defaults_and_overrides() -> None:
    assert parse_servicenow_settings({}) == ServiceNowSettings()
    parsed = parse_servicenow_settings(
        {"browser": " /opt/chrome ", "login_timeout_seconds": 60, "base_url": BASE}
    )
    assert (parsed.browser, parsed.login_timeout_seconds, parsed.base_url) == (
        "/opt/chrome",
        60.0,
        BASE,
    )


@pytest.mark.parametrize("bad", [0, -5, "long", True])
def test_settings_refuse_a_bad_login_timeout(bad: object) -> None:
    with pytest.raises(ConfigError, match="login_timeout_seconds"):
        parse_servicenow_settings({"login_timeout_seconds": bad})


def test_the_product_is_configured_by_its_own_table() -> None:
    settings = parse_settings({"servicenow": {"base_url": BASE}})
    assert isinstance(settings.products["servicenow"], ServiceNowSettings)


# --- session ----------------------------------------------------------------------------


async def test_a_session_round_trips_through_the_keychain(
    keychain: dict[tuple[str, str], str],
) -> None:
    assert await load_session("servicenow") is None
    session = BrowserSession.collected_now("a=1; b=2", "tok")
    await store_session("servicenow", session)
    assert await load_session("servicenow") == session
    assert session.headers() == {"Cookie": "a=1; b=2", "X-UserToken": "tok"}
    assert "a=1" not in repr(session) and "tok" not in repr(session)


@pytest.mark.parametrize(
    "stored", ["a-token-stored-by-mistake", "[1]", '{"cookie": "a=1"}', '{"cookie": 1}']
)
async def test_something_else_in_the_keychain_is_not_a_session(
    keychain: dict[tuple[str, str], str], stored: str
) -> None:
    keychain["productivity-agent-tools", "servicenow"] = stored
    assert await load_session("servicenow") is None


async def test_an_unavailable_keychain_means_no_session(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(service: str, user: str) -> str:
        raise keyring.errors.KeyringError("locked")

    monkeypatch.setattr(keyring, "get_password", broken)
    assert await load_session("servicenow") is None


async def test_the_credential_needs_a_saved_session(keychain: dict[tuple[str, str], str]) -> None:
    with pytest.raises(AuthenticationError, match="login servicenow"):
        await browser_session("servicenow", TokenSources())


async def test_the_credential_follows_a_new_sign_in(keychain: dict[tuple[str, str], str]) -> None:
    first = BrowserSession(cookie="s=1", user_token="t1", saved_at="2026-11-14T10:00:00+00:00")
    await store_session("servicenow", first)
    credential = await browser_session("servicenow", TokenSources())
    assert credential.label == "browser session"
    assert "2026-11-14T10:00:00" in credential.source
    assert await credential.headers() == {"Cookie": "s=1", "X-UserToken": "t1"}
    second = BrowserSession(cookie="s=2", user_token="t2", saved_at="2026-11-14T11:00:00+00:00")
    await store_session("servicenow", second)
    assert await credential.headers() == {"Cookie": "s=2", "X-UserToken": "t2"}
    assert credential.describe is not None
    assert (
        await credential.describe() == "the system keychain, signed in at 2026-11-14T11:00:00+00:00"
    )
    assert "10:00:00" in credential.source  # the start-up description stays as it was
    keychain.clear()
    with pytest.raises(AuthenticationError, match="No ServiceNow browser session"):
        await credential.headers()
    assert "login servicenow" in await credential.describe()


# --- adapter ----------------------------------------------------------------------------


async def test_the_adapter_sends_the_session_and_reads_json(instance: FakeServiceNow) -> None:
    async with make_api(instance) as api:
        assert await api.get("/api/now/table/sys_user") == {"result": [ME]}
        assert await api.get("/api/nothing") is None
        assert await api.get_text("/api/csv") == "a,b"
    sent = instance.requests[0].headers
    assert (sent["cookie"], sent["x-usertoken"]) == (GOOD, TOKEN)
    assert api.username is None


async def test_extra_cookies_from_the_config_join_the_session_cookie(
    instance: FakeServiceNow,
) -> None:
    connection = Connection(base_url=BASE, cookies={"consent": "yes"})

    async def headers() -> dict[str, str]:
        return {"Cookie": GOOD, "X-UserToken": TOKEN}

    transport = httpx.MockTransport(instance.handler)
    async with ServiceNowClient.from_connection(connection, headers, transport=transport) as api:
        await api.get("/api/now/table/sys_user")
    assert instance.requests[0].headers["cookie"] == f"{GOOD}; consent=yes"


@pytest.mark.parametrize(
    ("path", "error", "message"),
    [
        ("/api/forbidden", PermissionDeniedError, r"permission for this action\. \(ACL\)"),
        ("/api/missing", NotFoundError, r"/api/missing\. \(No Record found\)"),
        ("/api/broken", ApiError, r"HTTP 500\.$"),
        ("/api/odd-error", ApiError, r"HTTP 400\.$"),
        ("/api/odder-error", ApiError, r"HTTP 400\.$"),
        ("/api/moved", AuthenticationError, r"redirected the API call to https://sso.example/in "),
        ("/api/guest", AuthenticationError, "as if nobody were signed in"),
        ("/api/page", InterstitialPageError, "web page instead of API data"),
        ("/api/csv", ApiError, "Expected JSON but ServiceNow returned 'text/csv'"),
        ("/api/untyped", ApiError, "returned 'unknown'"),
    ],
)
async def test_failures_are_explained(
    instance: FakeServiceNow, path: str, error: type[Exception], message: str
) -> None:
    async with make_api(instance) as api:
        with pytest.raises(error, match=message) as info:
            await api.get(path)
    assert "SAML" not in str(info.value)


async def test_an_expired_session_says_how_to_sign_in_again(instance: FakeServiceNow) -> None:
    async with make_api(instance, cookie="JSESSIONID=stale") as api:
        with pytest.raises(AuthenticationError) as info:
            await api.get("/api/now/table/sys_user")
    assert "HTTP 401" in str(info.value)
    assert "login servicenow" in str(info.value)
    assert "(User Not Authenticated: Required)" in str(info.value)


async def test_writes_respect_dry_run(instance: FakeServiceNow) -> None:
    async with make_api(instance) as api:
        blocked = await api.write("POST", "/api/order", {"quantity": 1})
    assert blocked.sent is False
    assert instance.requests == []
    async with make_api(instance, dry_run=False) as api:
        outcome = await api.write("POST", "/api/order", {"quantity": 1})
    assert outcome.sent is True
    assert outcome.response == {"result": {"path": "/api/order"}}


async def test_the_shared_adapter_cannot_interpret_on_its_own(instance: FakeServiceNow) -> None:
    transport = httpx.MockTransport(instance.handler)
    async with HttpAdapter(
        base_url=BASE, credentials=bearer_token("t"), transport=transport
    ) as api:
        with pytest.raises(NotImplementedError):
            await api.get("/anything")


# --- client, doctor and tools -----------------------------------------------------------


async def test_doctor_names_who_is_signed_in(instance: FakeServiceNow) -> None:
    async with make_api(instance) as api:
        lines = [line async for line in doctor(ServiceNow(api, ServiceNowSettings()))]
    assert lines == [f"authenticated to {BASE} as Pat Example"]


async def test_a_missing_user_record_is_an_error(instance: FakeServiceNow) -> None:
    class Elsewhere(ServiceNowClient):
        async def get(self, path: str, params: dict[str, str | int] | None = None) -> JsonValue:
            return await super().get("/api/empty-user")

    session = BrowserSession.collected_now(GOOD, TOKEN)

    async def headers() -> dict[str, str]:
        return session.headers()

    transport = httpx.MockTransport(instance.handler)
    async with Elsewhere(base_url=BASE, credentials=headers, transport=transport) as api:
        with pytest.raises(ApiError, match="did not return your own user record"):
            await ServiceNow(api, ServiceNowSettings()).me()


def servicenow_settings() -> Settings:
    return parse_settings({"servicenow": {"base_url": BASE}})


async def test_status_tool_reports_the_session(instance: FakeServiceNow) -> None:
    async with make_api(instance) as api:
        server = build_server(
            servicenow_settings(), {"servicenow": Connected(api, "the keychain, signed in at X")}
        )
        result = await server.call_tool("servicenow_status", {})
    assert isinstance(result, CallToolResult)
    assert result.structured_content == {
        "base_url": BASE,
        "session_source": "the keychain, signed in at X",
        "username": "pat.example",
        "display_name": "Pat Example",
        "dry_run": True,
    }
    assert "login servicenow" in (server.instructions or "")


async def test_status_tool_passes_on_the_sign_in_advice(instance: FakeServiceNow) -> None:
    async with make_api(instance, cookie="JSESSIONID=stale") as api:
        server = build_server(servicenow_settings(), {"servicenow": Connected(api, "x")})
        with pytest.raises(ToolError, match="login servicenow"):
            await server.call_tool("servicenow_status", {})


# --- sign-in ----------------------------------------------------------------------------


class FakeBrowser:
    """A browser whose tabs and cookies follow a script, one step per look."""

    def __init__(self, steps: list[tuple[list[str], str]]) -> None:
        self.steps = steps
        self.looks = 0
        self.closed = False

    def version(self) -> str:
        return "Fake/1.0"

    def page_paths(self, host: str) -> list[str]:
        assert host == "desk.example"
        self.looks += 1
        return self._step()[0]

    def cookies(self, host: str) -> list[BrowserCookie]:
        name, _, value = self._step()[1].partition("=")
        return [BrowserCookie(name=name, value=value, domain=host)]

    def _step(self) -> tuple[list[str], str]:
        return self.steps[min(self.looks, len(self.steps)) - 1]

    def launcher(self) -> Callable[[str, str], object]:
        @contextmanager
        def launch(binary: str, url: str) -> Generator[FakeBrowser]:
            assert url == f"{BASE}/"
            try:
                yield self
            finally:
                self.closed = True

        return launch


def login_settings(timeout: float = 5.0) -> ServiceNowSettings:
    return ServiceNowSettings(browser=sys.executable, login_timeout_seconds=timeout)


async def run_sign_in(
    instance: FakeServiceNow, browser: FakeBrowser, wait: float = 5.0
) -> tuple[str, list[str]]:
    said: list[str] = []
    message = await sign_in(
        Connection(base_url=BASE),
        login_settings(wait),
        launch=browser.launcher(),  # ty: ignore[invalid-argument-type]
        transport=httpx.MockTransport(instance.handler),
        poll_seconds=0.01,
        announce=said.append,
    )
    return message, said


async def test_sign_in_waits_for_the_browser_then_stores_the_session(
    instance: FakeServiceNow, keychain: dict[tuple[str, str], str]
) -> None:
    browser = FakeBrowser(
        [
            ([], "JSESSIONID=guest"),  # away at the sign-in service
            (["/home?x=1"], "JSESSIONID=guest"),  # back, but not signed in yet
            (["/blank", "/home?x=1"], GOOD),  # signed in
        ]
    )
    message, said = await run_sign_in(instance, browser)
    assert message.startswith(f"Signed in to {BASE} as Pat Example.")
    assert "Sign in there as usual" in said[0]
    stored = await load_session("servicenow")
    assert stored is not None
    assert stored.headers() == {"Cookie": GOOD, "X-UserToken": TOKEN}
    assert browser.closed is True
    # Nothing was sent while the tab was away, and the guest page was never fetched.
    assert len(instance.requests) == 1 + 4
    assert [r.url.path for r in instance.requests[1:]] == [
        "/api/now/table/sys_user",
        "/blank",
        "/home",
        "/api/now/table/sys_user",
    ]
    assert GOOD not in message and TOKEN not in message


@pytest.mark.parametrize(
    ("steps", "accept_token", "progress"),
    [
        ([([], "JSESSIONID=guest")], True, "no tab is on the instance yet"),
        ([(["/home"], "JSESSIONID=guest")], True, "has not finished signing in"),
        ([(["/blank"], GOOD)], True, "no page with a request token is open yet"),
        ([(["/home"], GOOD)], False, r"refused the collected session \(HTTP 401\)"),
    ],
)
async def test_a_sign_in_that_does_not_finish_stores_nothing(
    instance: FakeServiceNow,
    keychain: dict[tuple[str, str], str],
    steps: list[tuple[list[str], str]],
    accept_token: bool,
    progress: str,
) -> None:
    instance.accept_token = accept_token
    browser = FakeBrowser(steps)
    with pytest.raises(AgentToolsError, match=progress) as info:
        await run_sign_in(instance, browser, wait=0.1)
    assert "Nothing was stored" in str(info.value)
    assert keychain == {}
    assert browser.closed is True


async def test_sign_in_reports_an_unreachable_instance(
    keychain: dict[tuple[str, str], str],
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    browser = FakeBrowser([(["/home"], GOOD)])
    with pytest.raises(ConnectivityError, match=r"Could not reach https://desk\.example"):
        await sign_in(
            Connection(base_url=BASE),
            login_settings(),
            launch=browser.launcher(),  # ty: ignore[invalid-argument-type]
            transport=httpx.MockTransport(refuse),
            poll_seconds=0.01,
            announce=lambda line: None,
        )
    assert browser.closed is True


async def test_sign_in_builds_a_verifying_client_and_needs_a_browser() -> None:
    settings = ServiceNowSettings(browser="no-such-browser-xyz")
    with pytest.raises(ConfigError, match="configured browser"):
        await sign_in(Connection(base_url=BASE), settings)
    browser = FakeBrowser([([], "a=b")])
    with pytest.raises(AgentToolsError, match="did not finish"):
        await sign_in(
            Connection(base_url=BASE),
            login_settings(0.05),
            launch=browser.launcher(),  # ty: ignore[invalid-argument-type]
            poll_seconds=0.01,
            announce=lambda line: None,
        )


# --- command line -----------------------------------------------------------------------


async def test_login_runs_the_products_sign_in(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Connection, object]] = []

    async def fake_login(connection: Connection, settings: ServiceNowSettings) -> str:
        seen.append((connection, settings))
        return "Signed in."

    products = dict(installed_products())
    products["servicenow"] = dataclasses.replace(PRODUCT, login=fake_login)
    monkeypatch.setattr(cli, "installed_products", lambda: products)
    settings = servicenow_settings()
    assert await cli._login(settings, "servicenow") == "Signed in."
    assert seen[0][0].base_url == BASE
    assert seen[0][1] is settings.products["servicenow"]


async def test_login_explains_when_it_does_not_apply() -> None:
    settings = servicenow_settings()
    with pytest.raises(ConfigError, match=r"'jira' has no interactive sign-in.*servicenow"):
        await cli._login(settings, "jira")
    with pytest.raises(ConfigError, match="'nope' has no interactive sign-in"):
        await cli._login(settings, "nope")
    with pytest.raises(ConfigError, match=r"Add a \[servicenow\] table"):
        await cli._login(parse_settings({"base_url": BASE, "jira": {}}), "servicenow")


def test_login_command_prints_the_result_or_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(f'[servicenow]\nbase_url = "{BASE}"\n')

    async def done(settings: Settings, name: str) -> str:
        return f"Signed in to {name}."

    monkeypatch.setattr(cli, "_login", done)
    cli.login(" ServiceNow ", config=config)
    assert "Signed in to servicenow." in capsys.readouterr().out

    async def failed(settings: Settings, name: str) -> str:
        raise AgentToolsError("The sign-in did not finish.")

    monkeypatch.setattr(cli, "_login", failed)
    with pytest.raises(SystemExit) as info:
        cli.login("servicenow", config=config)
    assert info.value.code == 1
    assert "did not finish" in capsys.readouterr().err


def test_set_token_points_a_browser_product_at_login(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.set_token("servicenow")
    assert "login servicenow" in capsys.readouterr().err


async def test_doctor_labels_the_session_and_reports_a_missing_one(
    instance: FakeServiceNow,
    keychain: dict[tuple[str, str], str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = servicenow_settings()
    transport = httpx.MockTransport(instance.handler)
    assert await cli._doctor(Path("/cfg.toml"), settings, transport) is False
    assert "[fail] No ServiceNow browser session is saved" in capsys.readouterr().out
    await store_session("servicenow", BrowserSession.collected_now(GOOD, TOKEN))
    assert await cli._doctor(Path("/cfg.toml"), settings, transport) is True
    output = capsys.readouterr().out
    assert "servicenow browser session found in the system keychain" in output
    assert f"authenticated to {BASE} as Pat Example" in output
