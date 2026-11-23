"""Tests for the ServiceNow adapter, session storage, sign-in flow and product."""

import dataclasses
import json
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
from productivity_agent_tools.api import WriteOutcome
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
from productivity_agent_tools.servicenow.forms import (
    CatalogForm,
    parse_form,
    parse_portal_form,
    parse_scripts,
)
from productivity_agent_tools.servicenow.http import ServiceNowClient
from productivity_agent_tools.servicenow.journal import parse_journal
from productivity_agent_tools.servicenow.login import sign_in
from productivity_agent_tools.servicenow.product import PRODUCT, doctor
from productivity_agent_tools.servicenow.rules import (
    Condition,
    Effect,
    Rule,
    State,
    evaluate,
    parse_rules,
)
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
INCIDENT_PATH = "/api/now/table/incident"
INCIDENTS: list[dict[str, str]] = [
    {
        "sys_id": "i1",
        "number": "INC0010001",
        "short_description": "VPN drops",
        "description": "Every hour.",
        "state": "In Progress",
        "urgency": "2 - Medium",
        "impact": "3 - Low",
        "priority": "4 - Low",
        "category": "Network",
        "assignment_group": "Service Desk",
        "assigned_to": "",
        "opened_at": "2026-11-20 09:00:00",
        "sys_updated_on": "2026-11-21 10:00:00",
        "resolved_at": "",
        "close_notes": "",
        "comments": "2026-11-21 10:00:00 - Sam Desk (Additional comments)\nLooking.",
    },
    {"sys_id": "", "number": "INC0010002", "short_description": "", "state": "New"},
]
FORM_ID = "f" * 32
SITE_ID = "5" * 32  # the "N/A" site's record
PORTAL_ID = "7" * 32
ITEM_ID = "e" * 32
# A software item as the catalog API describes it.
SOFTWARE_ITEM: dict[str, JsonValue] = {
    "sys_id": ITEM_ID,
    "name": "Visual Studio Code",
    "short_description": "Code editor",
    "sys_class_name": "sc_cat_item",
    "variables": [
        {
            "name": "justification",
            "label": "Business justification",
            "friendly_type": "multi_line_text",
            "mandatory": True,
        },
        {
            "name": "u_preferred_contact_phone_number",
            "label": "Phone",
            "friendly_type": "single_line_text",
            "mandatory": True,
        },
        {
            "name": "platform",
            "label": "Platform",
            "friendly_type": "select_box",
            "mandatory": True,
            "choices": [{"value": "mac", "label": "macOS"}, {"value": "win", "label": "Windows"}],
        },
    ],
}
REQUEST_ITEMS: list[dict[str, str]] = [
    {
        "sys_id": "ri1",
        "number": "RITM0010001",
        "short_description": "Visual Studio Code",
        "cat_item": "Visual Studio Code",
        "quantity": "1",
        "stage": "Waiting for Approval",
        "state": "Open",
        "approval": "Requested",
        "request": "REQ0010001",
        "requested_for": "Pat Example",
        "estimated_delivery": "2026-11-30 17:00:00",
        "due_date": "",
        "opened_at": "2026-11-23 09:00:00",
        "sys_updated_on": "2026-11-23 10:00:00",
        "closed_at": "",
        "close_notes": "",
        "assignment_group": "Software Desk",
        "assigned_to": "",
        "comments": (
            "2026-11-23 10:00:00 - Sam Desk (Additional comments)\nWaiting on your manager."
        ),
    },
    {"sys_id": "", "number": "RITM0010002", "stage": "Fulfillment"},
]
# The work instance's site table names its records in u_name (2026-11-23).
U_SITES: list[dict[str, str]] = [
    {"sys_id": "998d2bf1afd2d5505732526559274912", "u_name": "N/A"},
    {"sys_id": "6f7653b358dfa700eb7b1e2c93eae76b", "u_name": "Other"},
]
CATALOG = "/api/sn_sc/servicecatalog/items"


def select(name: str, label: str, *choices: tuple[str, str], mandatory: bool = True) -> dict:
    return {
        "name": name,
        "label": label,
        "friendly_type": "select_box",
        "type": 5,
        "mandatory": mandatory,
        "value": "",
        "choices": [{"value": "", "label": "-- None --"}]
        + [{"value": v, "label": lbl} for v, lbl in choices],
    }


# Modelled on the work instance's "Create an incident" form (2026-11-23).
FORM: dict = {
    "sys_id": FORM_ID,
    "name": "Create an incident",
    "short_description": "Report something broken",
    "variables": [
        {"name": "intro", "label": "Tell us", "friendly_type": "container_start"},
        {
            "name": "contact_phone",
            "label": "Preferred Contact Phone Number",
            "friendly_type": "single_line_text",
            "mandatory": "true",
            "value": "",
        },
        {
            "name": "u_site",
            "label": "Site",
            "friendly_type": "reference",
            "reference": "cmn_location",
            "mandatory": True,
        },
        select("category", "Category", ("software", "Software"), ("network", "Network")),
        select("impact", "What's the impact", ("1", ">100 users"), ("4", "< 5 users")),
        select(
            "urgency",
            "What's the urgency",
            ("1", "Business work stoppage (no workarounds)"),
            ("5", "No / little business impact"),
        ),
        select("network_connection", "Network Connection", ("vpn", "VPN"), ("wired", "Wired")),
        {
            "name": "description",
            "label": "Please describe your issue below",
            "type": 2,
            "mandatory": True,
            "help_text": "Please include as much relevant information as possible.",
        },
        {"name": "watch_list", "label": "Also notify", "type": 6, "value": "nobody"},
        {"name": "room", "label": "Room", "type": 6, "value": False},
        {"label": "nameless"},
        {"name": "rule", "type": 12},
    ],
}
ANSWERS: dict[str, str] = {
    "Preferred Contact Phone Number": "555-0100",
    "Site": SITE_ID,
    "category": "Software",
    "What's the impact?": "< 5 users",
    "urgency": "5",
    "Network Connection": "VPN",
    "Please describe your issue below": "The VPN drops hourly.",
}
ME = {"sys_id": "u1", "user_name": "pat.example", "name": "Pat Example", "email": "p@x.example"}


class FakeServiceNow:
    """An instance that knows one session: the cookie ``GOOD`` plus the token ``TOKEN``."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.accept_token = True
        self.catalog_hits: list[dict[str, JsonValue]] = [
            {"sys_id": "x2", "name": "Create an incident (VIP)"},
            {"sys_id": FORM_ID, "name": "create an incident"},
        ]
        self.portal_page: JsonValue = None
        self.portal_html = f"<script>NOW.portal_id = '{PORTAL_ID}';</script>"
        self.portal_html_reads = 0
        self.portal_status = 404
        self.sites: list[dict[str, str]] = [{"sys_id": SITE_ID, "name": "N/A"}]
        self.site_status = 200
        self.request_item_status = 200

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
        if path == CATALOG:
            return httpx.Response(200, headers=who, json={"result": self.catalog_hits})
        if path == "/arlsp2":
            self.portal_html_reads += 1
            return httpx.Response(200, headers={"content-type": "text/html"}, text=self.portal_html)
        if path == "/api/now/sp/page":
            if self.portal_page is None:
                return httpx.Response(self.portal_status, json={"error": {"message": "no"}})
            return httpx.Response(200, headers=who, json=self.portal_page)
        if path == "/api/now/table/u_site":
            query = request.url.params.get("sysparm_query", "")
            rows = [site for site in U_SITES if query.endswith(f"u_name={site['u_name']}")]
            return httpx.Response(200, headers=who, json={"result": rows})
        if path == "/api/now/table/cmn_location":
            if self.site_status != 200:
                return httpx.Response(self.site_status, json={"error": {"message": "ACL"}})
            query = request.url.params.get("sysparm_query", "")
            rows = [site for site in self.sites if query == f"name={site['name']}"]
            return httpx.Response(200, headers=who, json={"result": rows})
        if path == f"{CATALOG}/{FORM_ID}":
            return httpx.Response(200, headers=who, json={"result": FORM})
        if path == f"{CATALOG}/{ITEM_ID}":
            return httpx.Response(200, headers=who, json={"result": SOFTWARE_ITEM})
        if path == f"{CATALOG}/{ITEM_ID}/order_now":
            ordered = {
                "sys_id": "r9",
                "number": "REQ0010009",
                "request_number": "REQ0010009",
                "request_id": "r9",
                "table": "sc_request",
            }
            return httpx.Response(200, headers=who, json={"result": ordered})
        if path == "/api/now/table/sc_req_item":
            if self.request_item_status != 200:
                return httpx.Response(self.request_item_status, json={"error": "ACL"})
            query = request.url.params.get("sysparm_query", "")
            if query == "request=r9":
                rows: list[dict[str, str]] = [{"number": "RITM0010009"}, {"number": ""}]
            elif query.startswith("number="):
                rows = [r for r in REQUEST_ITEMS if f"number={r['number']}" == query]
            else:
                rows = REQUEST_ITEMS
            return httpx.Response(200, headers=who, json={"result": rows})
        if path == f"{CATALOG}/{FORM_ID}/submit_producer":
            created = {"sys_id": "i9", "number": "INC0010009", "table": "incident"}
            return httpx.Response(200, headers=who, json={"result": created})
        if path == INCIDENT_PATH:
            query = request.url.params.get("sysparm_query", "")
            if query.startswith("number="):
                rows = [row for row in INCIDENTS if f"number={row['number']}" == query]
            else:
                rows = INCIDENTS
            if request.url.params.get("sysparm_display_value") == "all":
                fields = request.url.params["sysparm_fields"].split(",")
                stored = {"sys_id": "i1", "u_site": SITE_ID}
                rows = [
                    {
                        name: {
                            "value": stored.get(name, row.get(name, "")),
                            "display_value": "N/A" if name == "u_site" else row.get(name, ""),
                        }
                        for name in fields
                    }
                    for row in rows
                ]
            return httpx.Response(200, headers=who, json={"result": rows})
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
        async def get(
            self,
            path: str,
            params: dict[str, str | int] | None = None,
            headers: dict[str, str] | None = None,
        ) -> JsonValue:
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
    monkeypatch.setattr(cli, "remote_products", lambda: products)
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


# --- incidents --------------------------------------------------------------------------


async def call(
    instance: FakeServiceNow, tool: str, arguments: dict[str, object], *, dry_run: bool = True
) -> dict[str, object] | list[object]:
    async with make_api(instance, dry_run=dry_run) as api:
        server = build_server(servicenow_settings(), {"servicenow": Connected(api, "x")})
        result = await server.call_tool(tool, arguments)
    assert isinstance(result, CallToolResult)
    assert result.structured_content is not None
    content = result.structured_content
    return content["result"] if set(content) == {"result"} else content


async def test_list_incidents_asks_for_the_users_open_ones(instance: FakeServiceNow) -> None:
    rows = await call(instance, "servicenow_list_incidents", {})
    assert rows == [
        {
            "number": "INC0010001",
            "short_description": "VPN drops",
            "state": "In Progress",
            "priority": "4 - Low",
            "opened_at": "2026-11-20 09:00:00",
            "updated_at": "2026-11-21 10:00:00",
            "url": f"{BASE}/incident.do?sys_id=i1",
        },
        {
            "number": "INC0010002",
            "short_description": None,
            "state": "New",
            "priority": None,
            "opened_at": None,
            "updated_at": None,
            "url": None,
        },
    ]
    params = instance.requests[-1].url.params
    assert params["sysparm_query"] == (
        "active=true^caller_id=javascript:gs.getUserID()^ORopened_by=javascript:gs.getUserID()"
        "^ORDERBYDESCopened_at"
    )
    assert params["sysparm_limit"] == "20"
    assert params["sysparm_display_value"] == "true"


async def test_list_incidents_can_include_closed_ones(instance: FakeServiceNow) -> None:
    await call(instance, "servicenow_list_incidents", {"include_closed": True, "max_results": 5})
    params = instance.requests[-1].url.params
    assert params["sysparm_query"].startswith("caller_id=")
    assert params["sysparm_limit"] == "5"


async def test_get_incident_reads_one_by_number(instance: FakeServiceNow) -> None:
    incident = await call(instance, "servicenow_get_incident", {"number": " inc0010001 "})
    assert isinstance(incident, dict)
    assert incident["number"] == "INC0010001"
    assert incident["description"] == "Every hour."
    assert incident["assignment_group"] == "Service Desk"
    assert incident["assigned_to"] is None
    assert incident["comments"] == [
        {
            "at": "2026-11-21 10:00:00",
            "author": "Sam Desk",
            "kind": "Additional comments",
            "body": "Looking.",
        }
    ]
    assert instance.requests[-1].url.params["sysparm_query"] == "number=INC0010001"


@pytest.mark.parametrize(
    ("number", "match"),
    [("INC0099999", "No incident INC0099999"), ("12; drop", "not a record number")],
)
async def test_get_incident_explains_a_bad_or_unknown_number(
    instance: FakeServiceNow, number: str, match: str
) -> None:
    with pytest.raises(ToolError, match=match):
        await call(instance, "servicenow_get_incident", {"number": number})


async def test_the_incident_form_lists_its_questions(instance: FakeServiceNow) -> None:
    form = await call(instance, "servicenow_get_incident_form", {})
    assert isinstance(form, dict)
    assert form["name"] == "Create an incident"
    assert form["description"] == "Report something broken"
    questions = form["questions"]
    assert isinstance(questions, list)
    assert [q["name"] for q in questions] == [
        "contact_phone",
        "u_site",
        "category",
        "impact",
        "urgency",
        "network_connection",
        "description",
        "watch_list",
        "room",
    ]
    by_name = {q["name"]: q for q in questions}
    assert by_name["contact_phone"]["required"] is True
    assert by_name["contact_phone"]["kind"] == "single_line_text"
    assert by_name["category"]["choices"] == [
        {"value": "software", "label": "Software"},
        {"value": "network", "label": "Network"},
    ]
    assert by_name["description"]["kind"] == "2"
    assert by_name["room"]["default"] is None
    assert by_name["description"]["help_text"].startswith("Please include")
    assert by_name["watch_list"] == {
        "name": "watch_list",
        "label": "Also notify",
        "kind": "6",
        "shown": True,
        "required": False,
        "choices": [],
        "default": "nobody",
        "help_text": None,
        "lookup_table": None,
    }
    assert by_name["u_site"]["lookup_table"] == "cmn_location"
    assert by_name["u_site"]["choices"] == []
    search = instance.requests[0].url.params
    assert search["sysparm_text"] == "Create an incident"


def with_settings(**values: object) -> Settings:
    return parse_settings({"servicenow": {"base_url": BASE, **values}})


async def call_with(
    instance: FakeServiceNow,
    settings: Settings,
    tool: str,
    arguments: dict[str, object],
    *,
    dry_run: bool = True,
) -> dict[str, object]:
    async with make_api(instance, dry_run=dry_run) as api:
        server = build_server(settings, {"servicenow": Connected(api, "x")})
        result = await server.call_tool(tool, arguments)
    assert isinstance(result, CallToolResult)
    assert isinstance(result.structured_content, dict)
    return result.structured_content


async def test_the_form_shows_configured_defaults(instance: FakeServiceNow) -> None:
    settings = with_settings(incident_defaults={"Site": SITE_ID, "contact_phone": "555-0100"})
    form = await call_with(instance, settings, "servicenow_get_incident_form", {})
    questions = form["questions"]
    assert isinstance(questions, list)
    defaults = {q["name"]: q["default"] for q in questions}
    assert defaults["u_site"] == SITE_ID
    assert defaults["contact_phone"] == "555-0100"
    assert defaults["category"] is None


async def test_the_form_can_be_named_by_sys_id(instance: FakeServiceNow) -> None:
    settings = with_settings(incident_form=FORM_ID)
    await call_with(instance, settings, "servicenow_get_incident_form", {})
    assert [r.url.path for r in instance.requests] == ["/api/now/sp/page", f"{CATALOG}/{FORM_ID}"]


async def test_a_form_that_is_not_found_names_what_the_search_found(
    instance: FakeServiceNow,
) -> None:
    instance.catalog_hits = [{"sys_id": "x2", "name": "Create an incident (VIP)"}, {}]
    with pytest.raises(
        ToolError, match=r"found: Create an incident \(VIP\); \?\. Set incident_form"
    ):
        await call(instance, "servicenow_get_incident_form", {})
    instance.catalog_hits = []
    with pytest.raises(ToolError, match="found: none"):
        await call(instance, "servicenow_get_incident_form", {})


async def test_an_item_without_its_own_sys_id_keeps_the_one_it_was_found_by(
    instance: FakeServiceNow,
) -> None:
    class NoId(ServiceNowClient):
        async def get(
            self,
            path: str,
            params: dict[str, str | int] | None = None,
            headers: dict[str, str] | None = None,
        ) -> JsonValue:
            answer = await super().get(path, params)
            if path.endswith(FORM_ID) and isinstance(answer, dict):
                result = answer["result"]
                assert isinstance(result, dict)
                return {"result": {k: v for k, v in result.items() if k != "sys_id"}}
            return answer

    session = BrowserSession.collected_now(GOOD, TOKEN)

    async def headers() -> dict[str, str]:
        return session.headers()

    transport = httpx.MockTransport(instance.handler)
    async with NoId(base_url=BASE, credentials=headers, transport=transport) as api:
        form = await ServiceNow(api, ServiceNowSettings()).catalog_form("Create an incident")
    assert form.sys_id == FORM_ID


async def test_create_incident_in_dry_run_sends_nothing(instance: FakeServiceNow) -> None:
    result = await call(instance, "servicenow_create_incident", {"answers": ANSWERS})
    assert isinstance(result, dict)
    assert result["sent"] is False
    assert result["number"] is None
    assert result["request"] == {
        "method": "POST",
        "path": f"{CATALOG}/{FORM_ID}/submit_producer",
        "body": {
            "variables": {
                "contact_phone": "555-0100",
                "u_site": SITE_ID,
                "category": "software",
                "impact": "4",
                "urgency": "5",
                "network_connection": "vpn",
                "description": "The VPN drops hourly.",
                "watch_list": "nobody",
            },
            "get_portal_messages": "true",
            "sysparm_no_validation": "true",
        },
    }
    # Search, the portal page (none here), then the catalog API's form.
    assert [r.method for r in instance.requests] == ["GET", "GET", "GET"]


async def test_create_incident_submits_the_form_and_reports_the_number(
    instance: FakeServiceNow,
) -> None:
    settings = with_settings(incident_defaults={"Site": SITE_ID, "watch_list": "nobody else"})
    answers = {k: v for k, v in ANSWERS.items() if k != "Site"} | {"watch_list": ""}
    result = await call_with(
        instance, settings, "servicenow_create_incident", {"answers": answers}, dry_run=False
    )
    assert result["sent"] is True
    assert result["number"] == "INC0010009"
    assert result["url"] == f"{BASE}/incident.do?sys_id=i9"
    assert result["message"] == "Opened incident INC0010009."
    posted = instance.requests[-1]
    assert posted.method == "POST"
    variables = json.loads(posted.content)["variables"]
    assert variables["u_site"] == SITE_ID  # from the config
    assert variables["watch_list"] == "nobody"  # cleared default falls back to the form's


async def test_an_identical_create_is_not_sent_twice(instance: FakeServiceNow) -> None:
    async with make_api(instance, dry_run=False) as api:
        server = build_server(servicenow_settings(), {"servicenow": Connected(api, "x")})
        await server.call_tool("servicenow_create_incident", {"answers": ANSWERS})
        again = await server.call_tool("servicenow_create_incident", {"answers": ANSWERS})
    assert isinstance(again, CallToolResult)
    assert again.structured_content is not None
    assert again.structured_content["repeat_suppressed"] is True
    assert [r.method for r in instance.requests].count("POST") == 1


async def test_a_submission_without_a_record_in_the_answer_still_reports(
    instance: FakeServiceNow,
) -> None:
    class Bare(ServiceNowClient):
        async def write(
            self, method: str, path: str, payload: dict[str, JsonValue]
        ) -> WriteOutcome:
            return WriteOutcome(sent=True, method=method, path=path, payload=payload)

    session = BrowserSession.collected_now(GOOD, TOKEN)

    async def headers() -> dict[str, str]:
        return session.headers()

    transport = httpx.MockTransport(instance.handler)
    async with Bare(base_url=BASE, credentials=headers, transport=transport) as api:
        server = build_server(servicenow_settings(), {"servicenow": Connected(api, "x")})
        result = await server.call_tool("servicenow_create_incident", {"answers": ANSWERS})
    assert isinstance(result, CallToolResult)
    assert result.structured_content is not None
    assert result.structured_content["message"] == "Submitted the incident form."
    assert result.structured_content["url"] is None


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (
            {"Category": "Printers"},
            r"'Printers' is not a choice for 'Category'\. Choices: Software; Network\.",
        ),
        (
            {"Colour": "red"},
            r"'Colour' is not a question on 'Create an incident'\. Questions: contact_phone, ",
        ),
        ({"Site": ""}, r"requires answers to: u_site \(Site\)\. .*Nothing was sent\."),
    ],
)
async def test_create_incident_refuses_answers_the_form_would_not_take(
    instance: FakeServiceNow, change: dict[str, str], match: str
) -> None:
    with pytest.raises(ToolError, match=match):
        await call(
            instance, "servicenow_create_incident", {"answers": ANSWERS | change}, dry_run=False
        )
    assert "POST" not in [r.method for r in instance.requests]


async def test_a_bad_configured_default_says_it_came_from_the_config(
    instance: FakeServiceNow,
) -> None:
    settings = with_settings(incident_defaults={"network_connection": "Mars"})
    with pytest.raises(ToolError, match=r": In \[servicenow\.incident_defaults\]: 'Mars'"):
        await call_with(instance, settings, "servicenow_create_incident", {"answers": ANSWERS})


@pytest.mark.parametrize("bad", [["Site"], {"Site": 3}])
def test_incident_defaults_must_be_a_table_of_strings(bad: object) -> None:
    with pytest.raises(ConfigError, match="incident_defaults"):
        parse_servicenow_settings({"incident_defaults": bad})


def test_incident_form_settings() -> None:
    assert parse_servicenow_settings({}).incident_form == "Create an incident"
    custom = parse_servicenow_settings(
        {"incident_form": "  Report a problem ", "incident_defaults": {"Site": "N/A"}}
    )
    assert custom.incident_form == "Report a problem"
    assert custom.incident_defaults == {"Site": "N/A"}


@pytest.mark.parametrize(
    ("key", "name"),
    [
        ("network_connection", "network_connection"),
        ("networkConnection", "network_connection"),
        ("NetworkConnection", "network_connection"),
        ("preferred_contact_phone_number", "contact_phone"),
        ("whats_the_impact", "impact"),
        ("WHAT'S THE IMPACT", "impact"),
        ("please_describe_your_issue_below", "description"),
        ("U_SITE", "u_site"),
    ],
)
def test_a_question_can_be_named_as_an_identifier(key: str, name: str) -> None:
    assert parse_form(FORM).question(key).name == name


@pytest.mark.parametrize("key", ["___", "network"])
def test_an_identifier_must_match_a_whole_label(key: str) -> None:
    with pytest.raises(AgentToolsError, match="is not a question"):
        parse_form(FORM).question(key)


def test_defaults_in_the_config_need_no_quoted_keys() -> None:
    settings = parse_servicenow_settings(
        {"incident_defaults": {"site": SITE_ID, "network_connection": "VPN"}}
    )
    variables = parse_form(FORM).answers(
        {k: v for k, v in ANSWERS.items() if k not in ("Site", "Network Connection")},
        settings.incident_defaults,
    )
    assert variables["u_site"] == SITE_ID
    assert variables["network_connection"] == "vpn"


@pytest.mark.parametrize(
    ("site", "sites", "status"),
    [
        ("N/A", [{"sys_id": SITE_ID, "name": "N/A"}] * 2, 200),  # two by that name
        ("N/A", [], 200),  # none
        ("N/A", [{"sys_id": SITE_ID, "name": "N/A"}], 403),  # the table cannot be read
        ("N/A", [{"sys_id": "", "name": "N/A"}], 200),  # a row without its sys_id
        ("N/A^ORname=x", [{"sys_id": SITE_ID, "name": "N/A^ORname=x"}], 200),  # not a name
    ],
)
async def test_a_lookup_answer_must_resolve_to_one_record(
    instance: FakeServiceNow, site: str, sites: list[dict[str, str]], status: int
) -> None:
    instance.sites = sites
    instance.site_status = status
    with pytest.raises(
        ToolError,
        match=r"'Site' is picked from a table \(cmn_location\), so its answer is the chosen "
        r"record's sys_id .*No single record with name 'N/A.* ask the user for the sys_id\.",
    ):
        await call(instance, "servicenow_create_incident", {"answers": ANSWERS | {"Site": site}})
    assert "POST" not in [r.method for r in instance.requests]


async def test_a_lookup_answer_given_by_name_is_looked_up(instance: FakeServiceNow) -> None:
    settings = with_settings(incident_defaults={"site": "n/a"})
    instance.sites = [{"sys_id": SITE_ID, "name": "n/a"}]
    answers = {k: v for k, v in ANSWERS.items() if k != "Site"}
    result = await call_with(instance, settings, "servicenow_create_incident", {"answers": answers})
    request = result["request"]
    assert isinstance(request, dict)
    assert request["body"]["variables"]["u_site"] == SITE_ID
    looked_up = [r for r in instance.requests if r.url.path == "/api/now/table/cmn_location"]
    assert looked_up[0].url.params["sysparm_query"] == "name=n/a"
    form = await call_with(instance, settings, "servicenow_get_incident_form", {})
    questions = form["questions"]
    assert isinstance(questions, list)
    assert {q["name"]: q["default"] for q in questions}["u_site"] == SITE_ID


async def test_only_lookup_answers_are_looked_up(instance: FakeServiceNow) -> None:
    with pytest.raises(ToolError, match="'Colour' is not a question"):
        await call(instance, "servicenow_create_incident", {"answers": ANSWERS | {"Colour": "x"}})
    await call(
        instance, "servicenow_create_incident", {"answers": ANSWERS | {"Site": SITE_ID, "Room": ""}}
    )
    assert not [r for r in instance.requests if r.url.path == "/api/now/table/cmn_location"]


def test_a_reference_without_its_table_named_is_still_a_lookup() -> None:
    variable: dict[str, JsonValue] = {"name": "u_site", "label": "Site", "type": 8}
    form = parse_form({"sys_id": FORM_ID, "name": "F", "variables": [variable]})
    assert form.questions[0].lookup_table == "an unnamed table"


async def test_get_incident_reads_extra_fields_in_both_forms(instance: FakeServiceNow) -> None:
    incident = await call(
        instance,
        "servicenow_get_incident",
        {"number": "INC0010001", "extra_fields": [" u_site ", "", "u_site"]},
    )
    assert isinstance(incident, dict)
    assert incident["extra_fields"] == {"u_site": {"value": SITE_ID, "display": "N/A"}}
    # The usual fields still read as the form shows them.
    assert incident["state"] == "In Progress"
    assert incident["url"] == f"{BASE}/incident.do?sys_id=i1"
    params = instance.requests[-1].url.params
    assert params["sysparm_display_value"] == "all"
    assert params["sysparm_fields"].endswith(",comments,u_site")


async def test_get_incident_refuses_a_field_that_is_not_a_column(instance: FakeServiceNow) -> None:
    with pytest.raises(ToolError, match=r"'u_site\^ORx' is not a field name"):
        await call(
            instance,
            "servicenow_get_incident",
            {"number": "INC0010001", "extra_fields": ["u_site^ORx"]},
        )
    assert instance.requests == []


async def test_several_forms_with_the_name_are_refused_with_their_ids(
    instance: FakeServiceNow,
) -> None:
    instance.catalog_hits = [
        {"sys_id": "a" * 32, "name": "Create an Incident", "short_description": "Stock"},
        {"sys_id": FORM_ID, "name": "create an incident"},
    ]
    with pytest.raises(
        ToolError,
        match=rf"2 catalog items are named 'Create an incident': {'a' * 32} \(Stock\); "
        rf"{FORM_ID} \(no description\)\. Set incident_form",
    ):
        await call(instance, "servicenow_get_incident_form", {})


PORTAL_ITEM: dict[str, JsonValue] = {
    "sys_id": FORM_ID,
    "name": "Create an Incident",
    "short_description": "The portal's form",
    "_fields": {
        "IO:1": {"name": "intro", "label": "Intro", "type": "container_start"},
        "IO:2": {
            "name": "u_site",
            "label": "Site",
            "type": "reference",
            "mandatory": True,
            "ed": {"reference": "cmn_location", "qualifier": ""},
            "value": "",
            "displayValue": "",
        },
        "IO:3": {
            "name": "category",
            "label": "Category",
            "type": "choice",
            "mandatory": True,
            "choices": [
                {"value": "", "label": "-- None --"},
                {"value": "Network", "label": "Network"},
            ],
            "instructions": "Network: connectivity, VPN",
        },
        "IO:4": {
            "name": "caller_id",
            "label": "Caller",
            "type": "reference",
            "mandatory": True,
            "refTable": "sys_user",
            "value": "u1",
        },
        "IO:5": {"name": "u_hidden", "label": "Hidden", "type": "string", "visible": False},
        "IO:6": {
            "name": "comments",
            "label": "Please describe your issue below",
            "type": "textarea",
            "mandatory": True,
        },
        "IO:7": {"name": "u_where", "label": "Where", "type": "reference", "mandatory": False},
    },
}


def portal_page(item: JsonValue) -> JsonValue:
    """A page answer with the catalog item where the portal keeps it: deep in a widget."""
    widget: JsonValue = {"widget": {"data": {"other": [1, "x"], "sc_cat_item": item}}}
    page: JsonValue = {"result": {"containers": [{"rows": [{"columns": [{"widgets": [widget]}]}]}]}}
    return page


async def test_the_form_is_read_from_the_portal_page(instance: FakeServiceNow) -> None:
    instance.portal_page = portal_page(PORTAL_ITEM)
    settings = with_settings(incident_form=FORM_ID, portal_id="p" * 32)
    form = await call_with(instance, settings, "servicenow_get_incident_form", {})
    assert form["source"] == "portal page"
    assert form["description"] == "The portal's form"
    questions = form["questions"]
    assert isinstance(questions, list)
    by_name = {q["name"]: q for q in questions}
    assert list(by_name) == ["u_site", "category", "caller_id", "u_hidden", "comments", "u_where"]
    assert by_name["u_hidden"]["shown"] is False
    assert by_name["u_site"]["lookup_table"] == "cmn_location"
    assert by_name["u_site"]["required"] is True
    assert by_name["caller_id"]["lookup_table"] == "sys_user"
    assert by_name["caller_id"]["default"] == "u1"
    assert by_name["u_where"]["lookup_table"] == "an unnamed table"
    assert by_name["category"]["choices"] == [{"value": "Network", "label": "Network"}]
    assert by_name["category"]["help_text"] == "Network: connectivity, VPN"
    params = instance.requests[0].url.params
    assert dict(params) == {"id": "sc_cat_item", "sys_id": FORM_ID, "portal_id": "p" * 32}
    # The catalog API was not needed.
    assert len(instance.requests) == 1


async def test_a_form_from_the_portal_page_is_submitted_as_the_portal_does(
    instance: FakeServiceNow,
) -> None:
    instance.portal_page = portal_page(PORTAL_ITEM)
    answers = {"site": "N/A", "category": "network", "comments": "VPN drops"}
    result = await call(instance, "servicenow_create_incident", {"answers": answers})
    assert isinstance(result, dict)
    request = result["request"]
    assert isinstance(request, dict)
    assert request["path"] == f"{CATALOG}/{FORM_ID}/submit_producer"
    assert request["body"] == {
        "variables": {
            "u_site": SITE_ID,
            "category": "Network",
            "caller_id": "u1",
            "comments": "VPN drops",
        },
        "get_portal_messages": "true",
        "sysparm_no_validation": "true",
    }


NO_FORM = "the page held no catalog item form"


@pytest.mark.parametrize(
    ("page", "settings", "why"),
    [
        (portal_page({**PORTAL_ITEM, "_fields": {}}), {}, "the form on the page had no questions"),
        (
            portal_page({"sys_id": FORM_ID, "name": "No fields"}),
            {},
            f"{NO_FORM}; set portal under [servicenow]",
        ),
        ({"result": "nothing here"}, {"portal_id": "p" * 32}, NO_FORM),
    ],
)
async def test_a_page_without_a_usable_form_falls_back_and_says_why(
    instance: FakeServiceNow, page: JsonValue, settings: dict[str, str], why: str
) -> None:
    instance.portal_page = page
    form = await call_with(instance, with_settings(**settings), "servicenow_get_incident_form", {})
    assert form["source"] == f"catalog API (the portal page was not used: {why})"
    assert form["name"] == "Create an incident"


async def test_a_portal_page_that_fails_falls_back_and_says_why(
    instance: FakeServiceNow,
) -> None:
    instance.portal_status = 500
    form = await call(instance, "servicenow_get_incident_form", {})
    assert isinstance(form, dict)
    assert form["source"] == (
        "catalog API (the portal page was not used: ServiceNow returned HTTP 500. (no))"
    )


def test_a_portal_form_may_list_its_fields_and_lack_a_sys_id() -> None:
    item: dict[str, JsonValue] = {
        "name": "F",
        "_fields": [{"name": "comments", "type": "textarea"}],
    }
    form = parse_portal_form(portal_page(item))
    assert form is not None
    assert form.sys_id == ""
    assert [q.name for q in form.questions] == ["comments"]


async def test_a_portal_form_without_its_sys_id_keeps_the_one_it_was_found_by(
    instance: FakeServiceNow,
) -> None:
    instance.portal_page = portal_page({k: v for k, v in PORTAL_ITEM.items() if k != "sys_id"})
    async with make_api(instance) as api:
        form = await ServiceNow(api, ServiceNowSettings()).catalog_form(FORM_ID)
    assert form.sys_id == FORM_ID
    assert form.source == "portal page"


def test_the_portal_id_setting() -> None:
    assert parse_servicenow_settings({}).portal_id is None
    assert parse_servicenow_settings({"portal_id": " abc "}).portal_id == "abc"


async def test_the_portal_is_found_from_its_address_and_asked_as_the_portal_asks(
    instance: FakeServiceNow,
) -> None:
    instance.portal_page = portal_page(PORTAL_ITEM)
    async with make_api(instance) as api:
        servicenow = ServiceNow(api, parse_servicenow_settings({"portal": "/arlsp2/"}))
        first = await servicenow.catalog_form(FORM_ID)
        await servicenow.catalog_form(FORM_ID)
    assert first.source == "portal page"
    assert instance.portal_html_reads == 1  # read once, then remembered
    page_request = instance.requests[1]
    assert dict(page_request.url.params) == {
        "id": "sc_cat_item",
        "sys_id": FORM_ID,
        "portal_id": PORTAL_ID,
        "request_uri": f"/arlsp2?id=sc_cat_item&sys_id={FORM_ID}",
    }
    assert page_request.headers["x-portal"] == PORTAL_ID
    assert page_request.headers["x-usertoken"] == TOKEN  # the session still wins


async def test_a_portal_page_that_does_not_state_its_id_is_still_asked(
    instance: FakeServiceNow,
) -> None:
    instance.portal_html = "<p>no id here</p>"
    instance.portal_page = portal_page(PORTAL_ITEM)
    async with make_api(instance) as api:
        servicenow = ServiceNow(api, parse_servicenow_settings({"portal": "arlsp2"}))
        form = await servicenow.catalog_form(FORM_ID)
        assert await servicenow.portal_id() is None
    assert form.source == "portal page"
    page_request = instance.requests[1]
    assert "portal_id" not in page_request.url.params
    assert "x-portal" not in page_request.headers


async def test_a_configured_portal_id_is_used_without_reading_the_portal(
    instance: FakeServiceNow,
) -> None:
    instance.portal_page = portal_page(PORTAL_ITEM)
    async with make_api(instance) as api:
        settings = parse_servicenow_settings({"portal": "arlsp2", "portal_id": "p" * 32})
        await ServiceNow(api, settings).catalog_form(FORM_ID)
    assert instance.portal_html_reads == 0
    assert instance.requests[0].headers["x-portal"] == "p" * 32


def test_portal_questions_follow_the_forms_layout() -> None:
    sections: JsonValue = [
        {"columns": [{"fields": [{"name": "category"}, {"type": "field"}]}]},
        {"columns": [{"fields": [{"name": "u_site"}, {"name": "gone"}]}]},
    ]
    fields = PORTAL_ITEM["_fields"]
    assert isinstance(fields, dict)
    widget: JsonValue = {"name": "contextual_search_results", "type": "widget"}
    item: dict[str, JsonValue] = {
        **PORTAL_ITEM,
        "_sections": sections,
        "_fields": {**fields, "IO:8": widget},
    }
    form = parse_portal_form(portal_page(item))
    assert form is not None
    assert [q.name for q in form.questions] == [
        "category",
        "u_site",
        "caller_id",
        "u_hidden",
        "comments",
        "u_where",
    ]


async def test_the_form_shows_defaults_that_do_not_fit_as_problems(
    instance: FakeServiceNow,
) -> None:
    settings = with_settings(
        incident_defaults={"u_colour": "red", "category": "Printers", "network_connection": "VPN"}
    )
    form = await call_with(instance, settings, "servicenow_get_incident_form", {})
    problems = form["problems"]
    assert isinstance(problems, list)
    assert len(problems) == 2
    assert problems[0].startswith("In [servicenow.incident_defaults]: 'u_colour' is not a question")
    assert "'Printers' is not a choice for 'Category'" in problems[1]
    questions = form["questions"]
    assert isinstance(questions, list)
    assert {q["name"]: q["default"] for q in questions}["network_connection"] == "vpn"
    # Creating still refuses until the config is fixed.
    with pytest.raises(ToolError, match="u_colour"):
        await call_with(instance, settings, "servicenow_create_incident", {"answers": ANSWERS})


def test_the_portal_settings() -> None:
    settings = parse_servicenow_settings({"portal": " /sp/ "})
    assert settings.portal == "sp"
    assert parse_servicenow_settings({}).portal is None


def u_site_form(qualifier: str) -> JsonValue:
    site: JsonValue = {
        "name": "u_site",
        "label": "Site",
        "type": "reference",
        "mandatory": True,
        "refTable": "u_site",
        "ed": {
            "reference": "u_site",
            "searchField": "u_name",
            "defaultOperator": "STARTSWITH",
            "qualifier": qualifier,
        },
    }
    comments: JsonValue = {"name": "comments", "type": "textarea", "mandatory": True}
    return portal_page(
        {"sys_id": FORM_ID, "name": "Create an Incident", "_fields": {"a": site, "b": comments}}
    )


@pytest.mark.parametrize(
    ("qualifier", "query"),
    [
        ("", "u_name=N/A"),
        ("u_active=true", "u_active=true^u_name=N/A"),
        ("javascript:new SiteFilter().get()", "u_name=N/A"),  # a script is not repeated
    ],
)
async def test_a_lookup_searches_the_column_and_filter_the_portal_uses(
    instance: FakeServiceNow, qualifier: str, query: str
) -> None:
    instance.portal_page = u_site_form(qualifier)
    settings = with_settings(incident_defaults={"site": "N/A"})
    form = await call_with(instance, settings, "servicenow_get_incident_form", {})
    assert form["problems"] == []
    questions = form["questions"]
    assert isinstance(questions, list)
    assert questions[0]["default"] == "998d2bf1afd2d5505732526559274912"
    looked_up = [r for r in instance.requests if r.url.path == "/api/now/table/u_site"]
    assert looked_up[0].url.params["sysparm_query"] == query
    assert looked_up[0].url.params["sysparm_fields"] == "sys_id,u_name"


async def test_a_lookup_that_misses_names_the_column_it_searched(
    instance: FakeServiceNow,
) -> None:
    instance.portal_page = u_site_form("")
    settings = with_settings(incident_defaults={"site": "Mars"})
    form = await call_with(instance, settings, "servicenow_get_incident_form", {})
    problems = form["problems"]
    assert isinstance(problems, list)
    assert "No single record with u_name 'Mars' was found there" in problems[0]


def test_a_journal_is_split_at_its_headers() -> None:
    journal = (
        "2026-11-23 09:05:00 - Sam Desk (Additional comments)\n"
        "Try this:\n\n1. Restart.\n2. Sign in again.\n\n"
        "2026-11-22 16:00:00 - Pat Example (Work notes)  \n"
        "Not a header: 2026-11-01 00:00:00 - X (Y) mid-line.\n\n"
    )
    entries = parse_journal(journal)
    assert [(e.at, e.author, e.kind) for e in entries] == [
        ("2026-11-23 09:05:00", "Sam Desk", "Additional comments"),
        ("2026-11-22 16:00:00", "Pat Example", "Work notes"),
    ]
    assert entries[0].body == "Try this:\n\n1. Restart.\n2. Sign in again."
    assert entries[1].body == "Not a header: 2026-11-01 00:00:00 - X (Y) mid-line."


@pytest.mark.parametrize(
    ("journal", "bodies"),
    [
        ("", []),
        ("   \n", []),
        ("Just text, no header.", ["Just text, no header."]),
        ("Lead text\n2026-11-23 09:05:00 - A (B)\nbody", ["Lead text", "body"]),
        ("2026-11-23 09:05:00 - A (B)\n", [""]),
    ],
)
def test_a_journal_without_the_usual_shape_keeps_its_text(journal: str, bodies: list[str]) -> None:
    assert [entry.body for entry in parse_journal(journal)] == bodies


def policy(
    title: str,
    conditions: list[tuple[str, str, str, bool]],
    actions: list[tuple[str, str, str]],
    *,
    reverse: JsonValue = True,
) -> JsonValue:
    """A UI policy as the portal sends it (shape from the work instance, 2026-11-23)."""
    return {
        "short_description": title,
        "reverse": reverse,
        "conditions": [
            {"field": f"variables.{f}", "oper": op, "value": v, "or": joins, "newquery": False}
            for f, op, v, joins in conditions
        ],
        "actions": [
            {"name": f"variables.{n}", "visible": vis, "mandatory": man, "valueAction": "ignore"}
            for n, vis, man in actions
        ],
    }


def choice(name: str, *values: str, mandatory: bool = False) -> JsonValue:
    return {
        "name": name,
        "label": name.title(),
        "type": "choice",
        "mandatory": mandatory,
        "choices": [{"value": v, "label": v.title()} for v in values],
    }


def text_field(name: str, *, mandatory: bool = False) -> JsonValue:
    return {"name": name, "label": name.title(), "type": "string", "mandatory": mandatory}


# The work instance's incident form in miniature: its four policies, as sent.
RULED_ITEM: dict[str, JsonValue] = {
    "sys_id": FORM_ID,
    "name": "Create an Incident",
    "_fields": {
        "category": choice("category", "hardware", "network", "server", "software", mandatory=True),
        "subcategory": choice("subcategory", "desktop", "laptop", "printer"),
        "u_hostname": text_field("u_hostname"),
        "u_network": choice("u_network", "1", "2"),
        "comments": text_field("comments", mandatory=True),
    },
    "policy": [
        policy(
            "Hide CI Information for Software",
            [("category", "IN", "hardware,network,server", False)],
            [("ci_information", "true", "ignore")],  # not a question here: left out
        ),
        policy(
            "Make field Subcategory mandatory & visible",
            [("category", "=", "hardware", False)],
            [("subcategory", "true", "true")],
        ),
        policy(
            "Make hostname & network visible & mandatory",
            [
                ("category", "=", "hardware", False),
                ("subcategory", "=", "desktop", False),
                ("subcategory", "=", "laptop", True),
            ],
            [("u_hostname", "true", "true"), ("u_network", "true", "true")],
        ),
        policy(
            "Set CI Information not mandatory",
            [],
            [("bar_code", "ignore", "false")],  # not a question here: left out
        ),
    ],
}


def ruled_form() -> CatalogForm:
    form = parse_portal_form(portal_page(RULED_ITEM))
    assert form is not None
    return form


def test_the_forms_rules_are_read_and_described() -> None:
    assert [rule.describe() for rule in ruled_form().rules] == [
        "Make field Subcategory mandatory & visible: when category is hardware: "
        "subcategory shown and required; otherwise the opposite",
        "Make hostname & network visible & mandatory: when category is hardware and "
        "subcategory is desktop or subcategory is laptop: u_hostname shown and required; "
        "u_network shown and required; otherwise the opposite",
    ]


@pytest.mark.parametrize(
    ("answers", "required"),
    [
        ({"category": "Software", "comments": "x"}, set()),
        ({"category": "Hardware", "comments": "x"}, {"subcategory"}),
        ({"category": "Hardware", "subcategory": "Printer", "comments": "x"}, set()),
        (
            {"category": "Hardware", "subcategory": "Laptop", "comments": "x"},
            {"u_hostname", "u_network"},
        ),
        (
            {"category": "Hardware", "subcategory": "desktop", "comments": "x"},
            {"u_hostname", "u_network"},
        ),
    ],
)
def test_the_rules_decide_what_else_is_required(
    answers: dict[str, str], required: set[str]
) -> None:
    form = ruled_form()
    if not required:
        assert set(form.answers(answers)) == {k.casefold() for k in answers}
        return
    with pytest.raises(AgentToolsError, match="requires answers to") as caught:
        form.answers(answers)
    missing = {name for name in required if f"{name} (" in str(caught.value)}
    assert missing == required


def test_a_hidden_question_answered_is_refused_with_the_rule() -> None:
    with pytest.raises(
        AgentToolsError,
        match=r"does not show subcategory \(Subcategory\), so leave it out\. The form's "
        r"rules: Make field Subcategory mandatory & visible: when category is hardware",
    ):
        ruled_form().answers({"category": "software", "subcategory": "laptop", "comments": "x"})


def test_a_default_for_a_hidden_question_is_left_out() -> None:
    variables = ruled_form().answers(
        {"category": "software", "comments": "x"}, {"u_network": "2", "subcategory": "laptop"}
    )
    assert variables == {"category": "software", "comments": "x"}
    hardware = ruled_form().answers(
        {"category": "hardware", "subcategory": "laptop", "u_hostname": "box", "comments": "x"},
        {"u_network": "2"},
    )
    assert hardware["u_network"] == "2"


def test_a_question_hidden_with_no_rule_says_so_plainly() -> None:
    item: dict[str, JsonValue] = {
        "sys_id": FORM_ID,
        "name": "F",
        "_fields": {
            "u_secret": {
                "name": "u_secret",
                "label": "U_Secret",
                "type": "string",
                "visible": False,
            }
        },
    }
    form = parse_portal_form(portal_page(item))
    assert form is not None
    with pytest.raises(AgentToolsError, match=r"leave it out\. Nothing was sent\.$"):
        form.answers({"u_secret": "x"})


async def test_the_form_tool_shows_the_rules_and_the_state_with_the_defaults(
    instance: FakeServiceNow,
) -> None:
    instance.portal_page = portal_page(RULED_ITEM)
    settings = with_settings(incident_defaults={"category": "Hardware"})
    form = await call_with(instance, settings, "servicenow_get_incident_form", {})
    rules = form["rules"]
    assert isinstance(rules, list)
    assert len(rules) == 2
    questions = form["questions"]
    assert isinstance(questions, list)
    states = {q["name"]: (q["shown"], q["required"]) for q in questions}
    assert states["subcategory"] == (True, True)
    assert states["u_hostname"] == (False, False)


def test_conditions_combine_as_an_encoded_query() -> None:
    # (a = 1 or a = 2) and b is answered, or separately c is empty
    rule = Rule(
        title="T",
        conditions=(
            Condition("a", "=", "1"),
            Condition("a", "=", "2", joins_with_or=True),
            Condition("b", "ISNOTEMPTY", ""),
            Condition("c", "ISEMPTY", "", starts_group=True),
        ),
        effects=(Effect("x", visible=True),),
    )
    assert rule.holds({"a": "2", "b": "y", "c": "z"})
    assert not rule.holds({"a": "3", "b": "y", "c": "z"})
    assert not rule.holds({"a": "1", "c": "z"})
    assert rule.holds({"a": "3"})
    assert rule.describe() == (
        "T: when a is 1 or a is 2 and b is answered or, separately, c is empty: "
        "x shown; otherwise the opposite"
    )


@pytest.mark.parametrize(
    ("operator", "wanted", "answer", "holds"),
    [
        ("!=", "a", "b", True),
        ("NOT IN", "a, b", "c", True),
        ("NOT IN", "a, b", "b", False),
        ("STARTSWITH", "ab", "abc", True),
        ("ENDSWITH", "bc", "abc", True),
        ("LIKE", "b", "abc", True),
        ("NOT LIKE", "b", "abc", False),
    ],
)
def test_each_operator(operator: str, wanted: str, answer: str, holds: bool) -> None:
    assert Condition("f", operator, wanted).holds({"f": answer}) is holds


def test_a_rule_with_an_unknown_operator_is_not_applied_and_says_so() -> None:
    rule = Rule("T", (Condition("a", "BETWEEN", "1@3"),), (Effect("x", visible=False),))
    assert not rule.understood
    assert rule.describe() == (
        "T: when a BETWEEN 1@3: x hidden; otherwise the opposite "
        "(not checked: an operator is not understood)"
    )
    states = evaluate((rule,), {"x": State(visible=True, mandatory=True)}, {"a": "2"})
    assert states["x"] == State(visible=True, mandatory=True)


def test_a_rule_that_does_not_reverse_leaves_things_when_it_fails() -> None:
    rule = Rule("T", (Condition("a", "=", "1"),), (Effect("x", mandatory=True),), reverse=False)
    base = {"x": State(visible=True, mandatory=False)}
    assert evaluate((rule,), base, {"a": "2"})["x"] == State(visible=True, mandatory=False)
    assert evaluate((rule,), base, {"a": "1"})["x"] == State(visible=True, mandatory=True)
    assert "otherwise" not in rule.describe()
    always = Rule("A", (), (Effect("x", visible=False, mandatory=False),))
    assert always.describe() == "A: always: x hidden and optional"
    assert evaluate((always,), {}, {}) == {}  # a question not on the form is left alone


def test_policy_settings_and_operators_are_read_loosely() -> None:
    policies: JsonValue = [
        {
            "reverse": "false",
            "conditions": [{"field": "a", "operator": " = ", "value": "1", "or": "true"}],
            "actions": [{"name": "x", "visible": True, "mandatory": "ignore"}],
        },
        {"actions": [{"name": "x", "visible": "ignore", "mandatory": "ignore"}]},
        "not a policy",
    ]
    (rule,) = parse_rules(policies, frozenset({"x"}))
    assert rule.title == "A form rule"
    assert rule.reverse is False
    assert rule.conditions == (Condition("a", "=", "1", joins_with_or=True),)
    assert rule.effects == (Effect("x", visible=True),)
    assert Effect("x").describe() == "x "


# --- catalog requests ---------------------------------------------------------------------

ORDER_ANSWERS: dict[str, str] = {"Business justification": "Daily editing", "platform": "macOS"}


async def test_the_catalog_is_searched_as_the_portal_searches(instance: FakeServiceNow) -> None:
    instance.catalog_hits = [
        {
            "sys_id": ITEM_ID,
            "name": "Visual Studio Code",
            "short_description": "Code editor",
            "category": {"title": "Software"},
            "sys_class_name": "sc_cat_item",
        },
        {"sys_id": FORM_ID, "name": "", "category": "", "type": "record_producer"},
        {"name": "no id"},
    ]
    items = await call(
        instance, "servicenow_search_catalog", {"query": " vscode ", "max_results": 5}
    )
    assert items == [
        {
            "sys_id": ITEM_ID,
            "name": "Visual Studio Code",
            "description": "Code editor",
            "category": "Software",
            "ordered": True,
        },
        {
            "sys_id": FORM_ID,
            "name": FORM_ID,
            "description": None,
            "category": None,
            "ordered": False,
        },
    ]
    params = instance.requests[-1].url.params
    assert (params["sysparm_text"], params["sysparm_limit"]) == ("vscode", "5")


async def test_a_blank_catalog_search_is_refused(instance: FakeServiceNow) -> None:
    with pytest.raises(ToolError, match="Say what to look for"):
        await call(instance, "servicenow_search_catalog", {"query": "  "})
    assert instance.requests == []


async def test_an_items_form_uses_the_order_defaults_its_form_has(
    instance: FakeServiceNow,
) -> None:
    settings = with_settings(
        order_defaults={"u_preferred_contact_phone_number": "555-0100", "u_site": "N/A"}
    )
    form = await call_with(instance, settings, "servicenow_get_catalog_form", {"item": ITEM_ID})
    assert form["name"] == "Visual Studio Code"
    assert form["problems"] == []  # u_site is not on this form: skipped, not a problem
    questions = form["questions"]
    assert isinstance(questions, list)
    defaults = {q["name"]: q["default"] for q in questions}
    assert defaults["u_preferred_contact_phone_number"] == "555-0100"


async def test_an_order_is_checked_then_sent_as_the_portal_sends_it(
    instance: FakeServiceNow,
) -> None:
    settings = with_settings(order_defaults={"u_preferred_contact_phone_number": "555-0100"})
    result = await call_with(
        instance,
        settings,
        "servicenow_order_item",
        {"item": ITEM_ID, "answers": ORDER_ANSWERS, "quantity": 2},
        dry_run=False,
    )
    assert result["sent"] is True
    assert result["item"] == "Visual Studio Code"
    assert result["number"] == "REQ0010009"
    assert result["url"] == f"{BASE}/sc_request.do?sys_id=r9"
    assert result["items"] == ["RITM0010009"]
    assert result["message"] == "Requested Visual Studio Code: REQ0010009."
    posted = next(r for r in instance.requests if r.method == "POST")
    assert posted.url.path == f"{CATALOG}/{ITEM_ID}/order_now"
    assert json.loads(posted.content) == {
        "sysparm_quantity": "2",
        "variables": {
            "justification": "Daily editing",
            "u_preferred_contact_phone_number": "555-0100",
            "platform": "mac",
        },
        "get_portal_messages": "true",
        "sysparm_no_validation": "true",
    }


async def test_an_order_in_dry_run_sends_nothing_and_reads_no_items(
    instance: FakeServiceNow,
) -> None:
    answers = ORDER_ANSWERS | {"Phone": "555-0100"}
    result = await call(instance, "servicenow_order_item", {"item": ITEM_ID, "answers": answers})
    assert isinstance(result, dict)
    assert result["sent"] is False
    assert result["items"] == []
    assert str(result["message"]).startswith("DRY RUN")
    assert "POST" not in [r.method for r in instance.requests]
    assert not [r for r in instance.requests if r.url.path.endswith("sc_req_item")]


async def test_an_order_missing_an_answer_is_refused(instance: FakeServiceNow) -> None:
    with pytest.raises(ToolError, match=r"requires answers to: u_preferred_contact_phone_number"):
        await call(
            instance,
            "servicenow_order_item",
            {"item": ITEM_ID, "answers": ORDER_ANSWERS},
            dry_run=False,
        )
    assert "POST" not in [r.method for r in instance.requests]


async def test_an_identical_order_is_not_sent_twice(instance: FakeServiceNow) -> None:
    arguments = {"item": ITEM_ID, "answers": ORDER_ANSWERS | {"Phone": "555-0100"}}
    async with make_api(instance, dry_run=False) as api:
        server = build_server(servicenow_settings(), {"servicenow": Connected(api, "x")})
        await server.call_tool("servicenow_order_item", arguments)
        again = await server.call_tool("servicenow_order_item", arguments)
    assert isinstance(again, CallToolResult)
    assert again.structured_content is not None
    assert again.structured_content["repeat_suppressed"] is True
    assert [r.method for r in instance.requests].count("POST") == 1


async def test_a_record_producer_item_is_submitted_not_ordered(
    instance: FakeServiceNow,
) -> None:
    instance.portal_page = portal_page({**PORTAL_ITEM, "sys_class_name": "sc_cat_item_producer"})
    answers = {"site": "N/A", "category": "network", "comments": "VPN drops"}
    result = await call(
        instance, "servicenow_order_item", {"item": FORM_ID, "answers": answers}, dry_run=False
    )
    assert isinstance(result, dict)
    assert result["number"] == "INC0010009"
    assert result["url"] == f"{BASE}/incident.do?sys_id=i9"
    assert result["items"] == []
    assert result["message"] == "Submitted Create an Incident: INC0010009."


async def test_an_order_whose_items_cannot_be_read_still_reports(
    instance: FakeServiceNow,
) -> None:
    instance.request_item_status = 403
    arguments: dict[str, object] = {
        "item": ITEM_ID,
        "answers": ORDER_ANSWERS | {"Phone": "555-0100"},
    }
    result = await call(instance, "servicenow_order_item", arguments, dry_run=False)
    assert isinstance(result, dict)
    assert result["number"] == "REQ0010009"
    assert result["items"] == []


async def test_an_order_without_a_number_in_the_answer_still_reports(
    instance: FakeServiceNow,
) -> None:
    class Bare(ServiceNowClient):
        async def write(
            self, method: str, path: str, payload: dict[str, JsonValue]
        ) -> WriteOutcome:
            return WriteOutcome(sent=True, method=method, path=path, payload=payload)

    session = BrowserSession.collected_now(GOOD, TOKEN)

    async def headers() -> dict[str, str]:
        return session.headers()

    transport = httpx.MockTransport(instance.handler)
    arguments = {"item": ITEM_ID, "answers": ORDER_ANSWERS | {"Phone": "555-0100"}}
    async with Bare(base_url=BASE, credentials=headers, transport=transport) as api:
        server = build_server(servicenow_settings(), {"servicenow": Connected(api, "x")})
        result = await server.call_tool("servicenow_order_item", arguments)
    assert isinstance(result, CallToolResult)
    assert result.structured_content is not None
    assert result.structured_content["message"] == "Requested Visual Studio Code."
    assert result.structured_content["url"] is None


async def test_requests_are_listed_for_the_user(instance: FakeServiceNow) -> None:
    rows = await call(instance, "servicenow_list_requests", {})
    assert isinstance(rows, list)
    first = rows[0]
    assert isinstance(first, dict)
    assert first == {
        "number": "RITM0010001",
        "item": "Visual Studio Code",
        "short_description": "Visual Studio Code",
        "request": "REQ0010001",
        "stage": "Waiting for Approval",
        "state": "Open",
        "approval": "Requested",
        "estimated_delivery": "2026-11-30 17:00:00",
        "opened_at": "2026-11-23 09:00:00",
        "updated_at": "2026-11-23 10:00:00",
        "url": f"{BASE}/sc_req_item.do?sys_id=ri1",
    }
    second = rows[1]
    assert isinstance(second, dict)
    assert second["url"] is None
    params = instance.requests[-1].url.params
    assert params["sysparm_query"] == (
        "active=true^requested_for=javascript:gs.getUserID()"
        "^ORopened_by=javascript:gs.getUserID()^ORDERBYDESCopened_at"
    )
    await call(instance, "servicenow_list_requests", {"include_closed": True, "max_results": 3})
    params = instance.requests[-1].url.params
    assert params["sysparm_query"].startswith("requested_for=")
    assert params["sysparm_limit"] == "3"


async def test_a_request_item_is_read_in_detail(instance: FakeServiceNow) -> None:
    item = await call(instance, "servicenow_get_request_item", {"number": "ritm0010001"})
    assert isinstance(item, dict)
    assert item["stage"] == "Waiting for Approval"
    assert item["assignment_group"] == "Software Desk"
    assert item["assigned_to"] is None
    assert item["quantity"] == "1"
    assert item["comments"] == [
        {
            "at": "2026-11-23 10:00:00",
            "author": "Sam Desk",
            "kind": "Additional comments",
            "body": "Waiting on your manager.",
        }
    ]
    with pytest.raises(ToolError, match="No request item RITM0099999"):
        await call(instance, "servicenow_get_request_item", {"number": "RITM0099999"})


def test_order_defaults_settings() -> None:
    assert parse_servicenow_settings({}).order_defaults == {}
    settings = parse_servicenow_settings({"order_defaults": {"site": "N/A"}})
    assert settings.order_defaults == {"site": "N/A"}
    with pytest.raises(ConfigError, match="order_defaults"):
        parse_servicenow_settings({"order_defaults": {"site": 1}})


LICENSE_SCRIPT = """function onLoad() {
    var ga = new GlideAjax('ToGetLicenseRequiredInfo');
    ga.getXML(function (response) {
        g_form.setMandatory("license_key_or_proof_of_purchase", true);
    });
}"""
CLASSIFIED_SCRIPT = """function onChange(control, oldValue, newValue, isLoading) {
    var classified = g_form.getValue("classified_workstation");
    if (classified == "Yes" && g_form.getValue("platform_os") == "mac") {
        alert("ARL does not currently support classified Mac systems.");
    }
}"""


def test_scripts_that_change_the_form_are_shown_once_each() -> None:
    scripts: JsonValue = {
        "onLoad": [
            {"script": LICENSE_SCRIPT},
            {"script": "function onLoad() { g_form.getValue('x'); }"},
        ],
        "onChange": [
            {"fieldName": "classified_workstation", "script": CLASSIFIED_SCRIPT},
            {"fieldName": "classified_workstation", "script": CLASSIFIED_SCRIPT},
            "not a script",
        ],
        "messages": "ignored",
    }
    found = parse_scripts(scripts)
    assert [(script.when, script.field) for script in found] == [
        ("onLoad", ""),
        ("onChange", "classified_workstation"),
    ]
    assert found[0].code == LICENSE_SCRIPT


async def test_the_catalog_form_shows_its_scripts(instance: FakeServiceNow) -> None:
    instance.portal_page = portal_page(
        {
            **PORTAL_ITEM,
            "client_script": {
                "onLoad": [{"script": LICENSE_SCRIPT}],
                "onChange": [{"fieldName": "category", "script": CLASSIFIED_SCRIPT}],
            },
        }
    )
    form = await call(instance, "servicenow_get_catalog_form", {"item": FORM_ID})
    assert isinstance(form, dict)
    assert form["scripts"] == [
        {"when": "onLoad", "field": None, "code": LICENSE_SCRIPT},
        {"when": "onChange", "field": "category", "code": CLASSIFIED_SCRIPT},
    ]


def test_anything_and_empty_conditions() -> None:
    anything = Condition("platform_oms", "ANYTHING", "")
    assert anything.holds({})
    assert anything.describe() == "platform_oms is anything"
    assert (
        Condition("classified_workstation", "=", "").describe() == "classified_workstation is empty"
    )
    assert Condition("classified_workstation", "!=", "").describe() == (
        "classified_workstation is answered"
    )
