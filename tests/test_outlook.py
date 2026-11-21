"""Tests for the Outlook REST adapter and the read-only calendar and mail products."""

import dataclasses
from pathlib import Path

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

from productivity_agent_tools import cli
from productivity_agent_tools.config import Settings, parse_settings
from productivity_agent_tools.credentials import Credential, bearer_token
from productivity_agent_tools.errors import (
    AgentToolsError,
    ApiError,
    AuthenticationError,
    ConfigError,
    NotFoundError,
    PermissionDeniedError,
)
from productivity_agent_tools.microsoft import calendar_product, mail_product
from productivity_agent_tools.microsoft.calendar_client import OutlookCalendarClient, _bound
from productivity_agent_tools.microsoft.mail_client import OutlookMailClient
from productivity_agent_tools.microsoft.outlook import OutlookClient
from productivity_agent_tools.microsoft.outlook_settings import (
    OutlookSettings,
    parse_outlook_settings,
)
from productivity_agent_tools.server import Connected, build_server

BASE = "https://outlook.example.us/api/v2.0"

ME = {"Id": "u1", "DisplayName": "Pat Example", "EmailAddress": "pat@x.mil", "Alias": "pat"}
CAL = {
    "Id": "cal1",
    "Name": "Calendar",
    "Owner": {"Name": "Pat", "Address": "pat@x.mil"},
    "CanEdit": True,
    "CanShare": False,
}
EVENT = {
    "Id": "ev1",
    "Subject": "Standup",
    "Start": {"DateTime": "2026-11-16T14:00:00.0000000", "TimeZone": "UTC"},
    "End": {"DateTime": "2026-11-16T14:30:00.0000000", "TimeZone": "UTC"},
    "IsAllDay": False,
    "Location": {"DisplayName": "Room A"},
    "Organizer": {"EmailAddress": {"Name": "Sam", "Address": "sam@x.mil"}},
    "ShowAs": "Busy",
    "IsCancelled": False,
    "WebLink": "https://owa/ev1",
    "Importance": "Normal",
    "Sensitivity": "Normal",
    "OnlineMeetingUrl": None,
    "OnlineMeeting": {"JoinUrl": "https://teams/join/1"},
    "iCalUId": "040000008200E001",
    "SeriesMasterId": "series1",
    "Type": "Occurrence",
    "IsOrganizer": False,
    "ResponseStatus": {"Response": "Accepted", "Time": "2026-11-10T00:00:00Z"},
    "Categories": ["Team"],
    "Attendees": [
        {
            "Type": "Required",
            "Status": {"Response": "Accepted"},
            "EmailAddress": {"Name": "Kim", "Address": "kim@x.mil"},
        },
        "not-an-object",
    ],
    "LastModifiedDateTime": "2026-11-15T00:00:00Z",
    "Body": {"ContentType": "Text", "Content": "Daily sync"},
}
MSG = {
    "Id": "m1",
    "Subject": "Hello",
    "From": {"EmailAddress": {"Name": "Sam", "Address": "sam@x.mil"}},
    "ToRecipients": [{"EmailAddress": {"Name": "Pat", "Address": "pat@x.mil"}}],
    "CcRecipients": [{"EmailAddress": {"Address": "cc@x.mil"}}],
    "ReceivedDateTime": "2026-11-15T09:00:00Z",
    "IsRead": False,
    "HasAttachments": True,
    "Importance": "High",
    "BodyPreview": "Hello there",
    "WebLink": "https://owa/m1",
    "ConversationId": "c1",
    "Body": {"ContentType": "Text", "Content": "Full body here"},
}
HEADERS = {
    "Id": "m1",
    "InternetMessageHeaders": [
        {"Name": "Received", "Value": "from mx.x.mil by y.x.mil"},
        {"Name": "Received", "Value": "from edge.x.mil by mx.x.mil"},
        {"Name": "Authentication-Results", "Value": "spf=pass dkim=pass dmarc=pass"},
        {"Name": "List-Unsubscribe", "Value": "<https://list.x.mil/u>"},
        {"Value": "no name, dropped"},
    ],
}


class FakeOutlook:
    """An Outlook REST instance that records requests and answers the probe paths."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.valid_token = "good-token"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("authorization") != f"Bearer {self.valid_token}":
            return httpx.Response(401, json={"error": {"code": "InvalidToken", "message": "no"}})
        path = request.url.path.removeprefix("/api/v2.0")
        if path == "/me":
            return httpx.Response(200, json=ME)
        if path == "/me/calendars":
            return httpx.Response(200, json={"value": [CAL], "@odata.nextLink": "more"})
        if path == "/me/calendarview" or path.endswith("/calendarview"):
            return httpx.Response(200, json={"value": [EVENT]})
        if path == "/me/events/ev1":
            return httpx.Response(200, json=EVENT)
        if path == "/me/events/bare":
            return httpx.Response(200, json={"Id": "bare"})
        if path == "/me/messages":
            return httpx.Response(200, json={"value": [MSG]})
        if path == "/me/messages/m1":
            if request.url.params.get("$select") == "InternetMessageHeaders":
                return httpx.Response(200, json=HEADERS)
            return httpx.Response(200, json=MSG)
        if path == "/me/messages/noheaders":
            return httpx.Response(200, json={"Id": "noheaders"})
        # Error-shape and content-type probes.
        if path == "/err/forbidden":
            return httpx.Response(403, json={"error": {"code": "ErrorAccessDenied"}})
        if path == "/err/missing":
            return httpx.Response(404, json={"error": "ItemNotFound"})
        if path == "/err/throttled":
            return httpx.Response(429, json={"error": {"message": "Too many"}})
        if path == "/err/server":
            return httpx.Response(500, text="boom")
        if path == "/err/list-body":
            return httpx.Response(400, json=["not", "an", "object"])
        if path == "/err/weird":
            return httpx.Response(400, json={"error": 7})
        if path == "/err/moved":
            return httpx.Response(302, headers={"location": "https://login.example/?x=1"})
        if path == "/ok/empty":
            return httpx.Response(204)
        if path == "/ok/text":
            return httpx.Response(200, headers={"content-type": "text/plain"}, text="plain")
        if path == "/ok/untyped":
            return httpx.Response(200, content=b"??")
        return httpx.Response(404, json={"error": {"message": "unknown path"}})


@pytest.fixture
def instance() -> FakeOutlook:
    return FakeOutlook()


def make_api(
    instance: FakeOutlook, *, token: str = "good-token", dry_run: bool = True
) -> OutlookClient:
    return OutlookClient(
        base_url=BASE,
        credentials=bearer_token(token),
        dry_run=dry_run,
        transport=httpx.MockTransport(instance.handler),
    )


def make_calendar(instance: FakeOutlook) -> OutlookCalendarClient:
    return OutlookCalendarClient(make_api(instance), OutlookSettings())


def make_mail(instance: FakeOutlook) -> OutlookMailClient:
    return OutlookMailClient(make_api(instance), OutlookSettings())


# --- settings ---------------------------------------------------------------------------


def test_settings_defaults_and_overrides() -> None:
    assert parse_outlook_settings({}, "outlook_mail") == OutlookSettings()
    parsed = parse_outlook_settings(
        {"base_url": BASE, "default_max_results": 10, "timeout_seconds": 15}, "outlook_mail"
    )
    assert (parsed.base_url, parsed.default_max_results, parsed.timeout_seconds) == (BASE, 10, 15.0)


@pytest.mark.parametrize("bad", [0, -1, "many", True])
def test_settings_refuse_a_bad_max_results(bad: object) -> None:
    with pytest.raises(ConfigError, match="default_max_results"):
        parse_outlook_settings({"default_max_results": bad}, "outlook_calendar")


def test_each_product_is_configured_by_its_own_table() -> None:
    # A Microsoft-only setup: one top-level base_url, each product's table empty.
    settings = parse_settings({"base_url": BASE, "outlook_calendar": {}, "outlook_mail": {}})
    assert isinstance(settings.products["outlook_calendar"], OutlookSettings)
    assert isinstance(settings.products["outlook_mail"], OutlookSettings)
    assert settings.connection_for(settings.products["outlook_mail"]).base_url == BASE


def test_the_microsoft_table_is_not_a_served_product() -> None:
    settings = parse_settings(
        {
            "outlook_mail": {"base_url": BASE},
            "microsoft": {
                "client_id": "c",
                "tenant": "t",
                "authority": "login.microsoftonline.us",
                "origin": "https://x",
                "redirect_uri": "https://x/r",
                "scope": "s",
            },
        }
    )
    assert set(settings.products) == {"outlook_mail"}


# --- adapter ----------------------------------------------------------------------------


async def test_the_adapter_sends_a_bearer_token_and_prefer_header(instance: FakeOutlook) -> None:
    async with make_api(instance) as api:
        assert await api.get("/me") == ME
        assert await api.get("/ok/empty") is None
        assert await api.get_text("/ok/text") == "plain"
    prefer = instance.requests[0].headers["prefer"]
    assert 'outlook.body-content-type="text"' in prefer
    assert 'outlook.timezone="UTC"' in prefer
    assert api.username is None


@pytest.mark.parametrize(
    ("path", "error", "message"),
    [
        ("/err/forbidden", PermissionDeniedError, r"HTTP 403\).*ErrorAccessDenied"),
        ("/err/missing", NotFoundError, r"/err/missing\. \(ItemNotFound\)"),
        ("/err/throttled", ApiError, r"rate-limiting.*\(Too many\)"),
        ("/err/server", ApiError, r"HTTP 500\.$"),
        ("/err/list-body", ApiError, r"HTTP 400\.$"),
        ("/err/weird", ApiError, r"HTTP 400\.$"),
        ("/err/moved", ApiError, r"redirected the API call to https://login.example/ "),
        ("/ok/untyped", ApiError, r"returned 'unknown'"),
    ],
)
async def test_failures_are_explained(
    instance: FakeOutlook, path: str, error: type[Exception], message: str
) -> None:
    async with make_api(instance) as api:
        with pytest.raises(error, match=message) as info:
            await api.get(path)
    assert "x=1" not in str(info.value)  # redirect query string is stripped


async def test_a_rejected_token_says_to_reseed(instance: FakeOutlook) -> None:
    async with make_api(instance, token="stale") as api:
        with pytest.raises(AuthenticationError) as info:
            await api.get("/me")
    assert "HTTP 401" in str(info.value)
    assert "login microsoft" in str(info.value)
    assert "(no)" in str(info.value)


async def test_a_text_content_type_on_json_reads_is_refused(instance: FakeOutlook) -> None:
    async with make_api(instance) as api:
        with pytest.raises(ApiError, match="Expected JSON but Outlook returned 'text/plain'"):
            await api.get("/ok/text")


# --- calendar ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "end_of_day", "expected"),
    [
        ("2026-11-20", False, "2026-11-20T00:00:00"),
        ("2026-11-20", True, "2026-11-20T23:59:59"),
        ("2026-11-20T09:30:00Z", False, "2026-11-20T09:30:00Z"),
        ("  ", False, "DEFAULT"),
    ],
)
def test_range_bound_normalization(value: str, end_of_day: bool, expected: str) -> None:
    from datetime import UTC, datetime

    default = datetime(2026, 1, 1, tzinfo=UTC)
    result = _bound(value, default, end_of_day=end_of_day)
    assert result == (default.isoformat(timespec="seconds") if expected == "DEFAULT" else expected)


async def test_calendar_me_and_list_calendars(instance: FakeOutlook) -> None:
    calendar = make_calendar(instance)
    assert (await calendar.me())["DisplayName"] == "Pat Example"
    listed = await calendar.list_calendars()
    assert (listed.returned, listed.more_available) == (1, True)
    assert listed.calendars[0].id == "cal1"
    assert listed.calendars[0].owner is not None
    assert listed.calendars[0].can_edit is True


async def test_list_events_defaults_the_range_and_orders_by_start(instance: FakeOutlook) -> None:
    calendar = make_calendar(instance)
    events = await calendar.list_events()
    assert events.returned == 1
    event = events.events[0]
    assert event.subject == "Standup"
    assert event.start is not None and event.start.time_zone == "UTC"
    assert event.location == "Room A"
    params = instance.requests[-1].url.params
    assert params["$orderby"] == "Start/DateTime"
    assert "startDateTime" in params and "endDateTime" in params
    assert instance.requests[-1].url.path.endswith("/me/calendarview")


async def test_list_events_scopes_to_a_calendar_and_passes_the_range(
    instance: FakeOutlook,
) -> None:
    calendar = make_calendar(instance)
    await calendar.list_events(start="2026-11-20", end="2026-11-21", calendar_id="cal1")
    sent = instance.requests[-1].url
    assert sent.path.endswith("/me/calendars/cal1/calendarview")
    assert sent.params["startDateTime"] == "2026-11-20T00:00:00"
    assert sent.params["endDateTime"] == "2026-11-21T23:59:59"


async def test_get_event_reads_body_attendees_and_truncates(instance: FakeOutlook) -> None:
    calendar = make_calendar(instance)
    event = await calendar.get_event("ev1", max_chars=5)
    assert event.body == "Daily" and event.truncated is True and event.body_chars == 10
    assert event.categories == ["Team"]
    assert len(event.attendees) == 1
    assert event.attendees[0].name == "Kim" and event.attendees[0].response == "Accepted"
    assert event.online_meeting_url == "https://teams/join/1"
    assert (event.ical_uid, event.series_master_id, event.type) == (
        "040000008200E001",
        "series1",
        "Occurrence",
    )
    assert (event.is_organizer, event.response) == (False, "Accepted")


async def test_get_event_tolerates_a_sparse_event(instance: FakeOutlook) -> None:
    calendar = make_calendar(instance)
    event = await calendar.get_event("bare")
    assert event.id == "bare"
    assert event.start is None and event.organizer is None and event.attendees == []
    assert event.body == "" and event.truncated is False


async def test_get_event_needs_an_id(instance: FakeOutlook) -> None:
    with pytest.raises(AgentToolsError, match="Give an event id"):
        await make_calendar(instance).get_event("   ")


# --- mail -------------------------------------------------------------------------------


async def test_list_messages_newest_first_and_unread_filter(instance: FakeOutlook) -> None:
    mail = make_mail(instance)
    listed = await mail.list_messages()
    assert listed.returned == 1 and listed.more_available is False
    assert "$filter" not in instance.requests[-1].url.params
    message = listed.messages[0]
    assert message.sender is not None and message.sender.name == "Sam"
    assert message.is_read is False and message.has_attachments is True
    await mail.list_messages(unread_only=True)
    assert instance.requests[-1].url.params["$filter"] == "IsRead eq false"
    assert instance.requests[-1].url.params["$orderby"] == "ReceivedDateTime desc"


async def test_search_messages_uses_odata_search(instance: FakeOutlook) -> None:
    mail = make_mail(instance)
    await mail.search_messages("budget review", max_results=5)
    params = instance.requests[-1].url.params
    assert params["$search"] == '"budget review"'
    assert params["$top"] == "5"
    assert "$orderby" not in params


async def test_search_messages_needs_a_query(instance: FakeOutlook) -> None:
    with pytest.raises(AgentToolsError, match="must not be empty"):
        await make_mail(instance).search_messages("  ")


async def test_get_message_reads_body_and_recipients(instance: FakeOutlook) -> None:
    mail = make_mail(instance)
    message = await mail.get_message("m1", max_chars=4)
    assert message.body == "Full" and message.truncated is True and message.body_chars == 14
    assert message.cc[0].address == "cc@x.mil" and message.cc[0].name is None
    assert message.conversation_id == "c1"


async def test_get_message_needs_an_id(instance: FakeOutlook) -> None:
    with pytest.raises(AgentToolsError, match="Give a message id"):
        await make_mail(instance).get_message("")


async def test_get_message_headers_reads_internet_headers(instance: FakeOutlook) -> None:
    mail = make_mail(instance)
    result = await mail.get_message_headers("m1")
    assert instance.requests[-1].url.params["$select"] == "InternetMessageHeaders"
    assert result.id == "m1" and result.count == 4  # the nameless header is dropped
    names = [h.name for h in result.headers]
    assert names == ["Received", "Received", "Authentication-Results", "List-Unsubscribe"]
    auth = next(h for h in result.headers if h.name == "Authentication-Results")
    assert "dmarc=pass" in auth.value


async def test_get_message_headers_tolerates_none_and_needs_an_id(instance: FakeOutlook) -> None:
    mail = make_mail(instance)
    empty = await mail.get_message_headers("noheaders")
    assert empty.count == 0 and empty.headers == []
    with pytest.raises(AgentToolsError, match="Give a message id"):
        await mail.get_message_headers("   ")


# --- doctor -----------------------------------------------------------------------------


async def test_calendar_and_mail_doctors_name_who_is_signed_in(instance: FakeOutlook) -> None:
    cal_lines = [line async for line in calendar_product.doctor(make_calendar(instance))]
    mail_lines = [line async for line in mail_product.doctor(make_mail(instance))]
    assert cal_lines == [f"authenticated to {BASE} as Pat Example (calendar)"]
    assert mail_lines == [f"authenticated to {BASE} as Pat Example (mail)"]


# --- tools through a built server -------------------------------------------------------


def outlook_settings() -> Settings:
    return parse_settings(
        {"outlook_calendar": {"base_url": BASE}, "outlook_mail": {"base_url": BASE}}
    )


def structured(result: object) -> dict[str, object]:
    """Return a tool result's structured content, asserting it is a successful call."""
    assert isinstance(result, CallToolResult)
    assert result.structured_content is not None
    return result.structured_content


async def test_status_tools_report_the_account_and_instructions(instance: FakeOutlook) -> None:
    async def live() -> str:
        return "the keychain, signed in just now"

    async with make_api(instance) as cal_api, make_api(instance) as mail_api:
        server = build_server(
            outlook_settings(),
            {
                "outlook_calendar": Connected(cal_api, "the keychain, signed in at X"),
                "outlook_mail": Connected(mail_api, "at start", describe_source=live),
            },
        )
        cal_status = structured(await server.call_tool("outlook_calendar_status", {}))
        mail_status = structured(await server.call_tool("outlook_mail_status", {}))
        events = structured(
            await server.call_tool("outlook_mail_list_messages", {"unread_only": True})
        )
    assert cal_status == {
        "base_url": BASE,
        "token_source": "the keychain, signed in at X",
        "user_name": "Pat Example",
        "email": "pat@x.mil",
        "dry_run": True,
    }
    assert mail_status["email"] == "pat@x.mil"
    assert mail_status["token_source"] == "the keychain, signed in just now"
    assert events["returned"] == 1
    assert "outlook_calendar_" in (server.instructions or "")
    assert "outlook_mail_" in (server.instructions or "")


async def test_every_calendar_and_mail_tool_runs_through_the_server(instance: FakeOutlook) -> None:
    async with make_api(instance) as cal_api, make_api(instance) as mail_api:
        server = build_server(
            outlook_settings(),
            {
                "outlook_calendar": Connected(cal_api, "x"),
                "outlook_mail": Connected(mail_api, "x"),
            },
        )
        cals = structured(await server.call_tool("outlook_calendar_list_calendars", {}))
        events = structured(
            await server.call_tool("outlook_calendar_list_events", {"start": "2026-11-20"})
        )
        event = structured(
            await server.call_tool("outlook_calendar_get_event", {"event_id": "ev1"})
        )
        search = structured(await server.call_tool("outlook_mail_search_messages", {"query": "hi"}))
        message = structured(
            await server.call_tool("outlook_mail_get_message", {"message_id": "m1"})
        )
        headers = structured(
            await server.call_tool("outlook_mail_get_message_headers", {"message_id": "m1"})
        )
    assert cals["returned"] == 1 and search["returned"] == 1 and events["returned"] == 1
    assert event["subject"] == "Standup"
    assert message["body_chars"] == 14
    assert headers["count"] == 4


def test_odata_helpers_tolerate_non_collection_shapes() -> None:
    from productivity_agent_tools.microsoft import reads

    assert reads.odata_items({"value": "not-a-list"}) == []
    assert reads.odata_items({}) == []
    assert reads.has_next({"value": []}) is False
    assert reads.email_refs({"ToRecipients": "nope"}, "ToRecipients") == []
    assert reads.email_ref({"From": {}}, "From") is None
    assert reads.flag({"IsRead": "yes"}) is None
    assert reads.str_list({"Categories": [1, "a", 2]}, "Categories") == ["a"]


async def test_a_tool_passes_the_reseed_advice_to_the_agent(instance: FakeOutlook) -> None:
    async with make_api(instance, token="stale") as api:
        settings = parse_settings({"outlook_calendar": {"base_url": BASE}})
        server = build_server(settings, {"outlook_calendar": Connected(api, "x")})
        with pytest.raises(ToolError, match="login microsoft"):
            await server.call_tool("outlook_calendar_status", {})


# --- shared credential ------------------------------------------------------------------


async def test_the_two_products_share_one_microsoft_credential(
    instance: FakeOutlook, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def fake_resolve(name: str, env_file: Path | None) -> Credential:
        calls.append(name)
        return Credential(headers=bearer_token("good-token"), source="shared", label="OAuth token")

    products = dict(cli.installed_products())
    products["outlook_calendar"] = dataclasses.replace(
        calendar_product.PRODUCT, resolve_credential=fake_resolve
    )
    products["outlook_mail"] = dataclasses.replace(
        mail_product.PRODUCT, resolve_credential=fake_resolve
    )
    monkeypatch.setattr(cli, "installed_products", lambda: products)

    settings = outlook_settings()
    transport = httpx.MockTransport(instance.handler)
    async with cli._open_server(settings, transport) as server:
        tools = {tool.name for tool in await server.list_tools()}
    # Resolved once for the shared "microsoft" token, not once per product.
    assert calls == ["microsoft"]
    assert {"outlook_calendar_status", "outlook_mail_status"} <= tools


async def test_doctor_reports_both_outlook_products(
    instance: FakeOutlook, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_resolve(name: str, env_file: Path | None) -> Credential:
        return Credential(
            headers=bearer_token("good-token"), source="the keychain", label="OAuth token"
        )

    products = dict(cli.installed_products())
    for name, product in (
        ("outlook_calendar", calendar_product.PRODUCT),
        ("outlook_mail", mail_product.PRODUCT),
    ):
        products[name] = dataclasses.replace(product, resolve_credential=fake_resolve)
    monkeypatch.setattr(cli, "installed_products", lambda: products)

    transport = httpx.MockTransport(instance.handler)
    assert await cli._doctor(Path("/cfg.toml"), outlook_settings(), transport) is True
    output = capsys.readouterr().out
    assert f"authenticated to {BASE} as Pat Example (calendar)" in output
    assert f"authenticated to {BASE} as Pat Example (mail)" in output
