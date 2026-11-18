"""Tests for the OData query tools for Outlook messages and events."""

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult
from pydantic import JsonValue

from productivity_agent_tools.config import parse_settings
from productivity_agent_tools.credentials import bearer_token
from productivity_agent_tools.errors import AgentToolsError, ApiError
from productivity_agent_tools.microsoft.calendar_client import OutlookCalendarClient
from productivity_agent_tools.microsoft.mail_client import OutlookMailClient
from productivity_agent_tools.microsoft.odata import EVENT_FIELDS, MESSAGE_FIELDS, ODataQuery
from productivity_agent_tools.microsoft.outlook import OutlookClient
from productivity_agent_tools.microsoft.outlook_settings import OutlookSettings
from productivity_agent_tools.server import Connected, build_server

BASE = "https://outlook.example.us/api/v2.0"
MESSAGE_ID = "<abc123@mail.example.com>"


class FakeOutlook:
    """Answers any collection with a set body, and records what was asked."""

    def __init__(self, body: object = None, status: int = 200) -> None:
        self.body = body if body is not None else {"value": []}
        self.status = status
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)

    @property
    def path(self) -> str:
        raw = self.requests[-1].url.raw_path.decode("ascii").split("?")[0]
        return raw.removeprefix("/api/v2.0")

    @property
    def params(self) -> dict[str, str]:
        return dict(self.requests[-1].url.params)

    def api(self) -> OutlookClient:
        return OutlookClient(
            base_url=BASE,
            credentials=bearer_token("t"),
            dry_run=True,
            transport=httpx.MockTransport(self.handler),
        )


def mail(fake: FakeOutlook) -> OutlookMailClient:
    return OutlookMailClient(fake.api(), OutlookSettings(default_max_results=25))


def calendar(fake: FakeOutlook) -> OutlookCalendarClient:
    return OutlookCalendarClient(fake.api(), OutlookSettings(default_max_results=25))


# --- the options -------------------------------------------------------------------------


def test_only_given_options_are_sent_with_a_compact_default_select() -> None:
    assert ODataQuery().params(default_top=25, default_select="Id") == {
        "$top": 25,
        "$select": "Id",
    }
    query = ODataQuery(
        filter=" IsRead eq false ",
        select="Subject,From",
        orderby="ReceivedDateTime desc",
        expand="Attachments",
        top=500,
        skip=50,
        count=True,
    )
    assert query.params(default_top=25, default_select="Id") == {
        "$top": 100,
        "$select": "Subject,From",
        "$filter": "IsRead eq false",
        "$orderby": "ReceivedDateTime desc",
        "$expand": "Attachments",
        "$skip": 50,
        "$count": "true",
    }


def test_blank_options_and_a_zero_skip_are_left_out() -> None:
    query = ODataQuery(filter="  ", select="", orderby=None, skip=0, top=0)
    assert query.params(default_top=10, default_select="Id") == {"$top": 10, "$select": "Id"}


def test_a_negative_skip_is_refused_before_anything_is_sent() -> None:
    with pytest.raises(AgentToolsError, match="skip must be zero or more"):
        ODataQuery(skip=-1).params(default_top=10, default_select="Id")


# --- messages ----------------------------------------------------------------------------


async def test_a_message_is_found_by_its_internet_message_id() -> None:
    found = {
        "@odata.id": "https://outlook/x",
        "@odata.etag": 'W/"1"',
        "Id": "m1",
        "Subject": "Hello",
        "InternetMessageId": MESSAGE_ID,
        "From": {"EmailAddress": {"Name": "Sam", "Address": "sam@x.mil"}},
    }
    fake = FakeOutlook({"@odata.context": "ctx", "value": [found, "not an object"]})
    result = await mail(fake).query_messages(
        ODataQuery(
            filter=f"InternetMessageId eq '{MESSAGE_ID}'",
            select="Subject,ReceivedDateTime,From,Id,InternetMessageId",
        )
    )
    assert fake.path == "/me/messages"
    assert fake.params == {
        "$top": "25",
        "$select": "Subject,ReceivedDateTime,From,Id,InternetMessageId",
        "$filter": f"InternetMessageId eq '{MESSAGE_ID}'",
    }
    assert fake.requests[-1].method == "GET"
    # Annotations are dropped; the API's own property names are kept for reuse.
    assert result.items == [{k: v for k, v in found.items() if not k.startswith("@")}]
    assert (result.returned, result.total, result.more_available, result.next_skip) == (
        1,
        None,
        False,
        None,
    )


async def test_one_folder_paging_and_count() -> None:
    page = [{"Id": f"m{n}"} for n in range(10)]
    fake = FakeOutlook({"@odata.count": 42, "value": page, "@odata.nextLink": "next"})
    result = await mail(fake).query_messages(
        ODataQuery(filter="IsRead eq false", top=10, skip=20, count=True), folder=" junk/email "
    )
    assert fake.path == "/me/mailfolders/junk%2Femail/messages"
    assert fake.params["$select"] == MESSAGE_FIELDS
    assert fake.params["$count"] == "true"
    assert (result.returned, result.total, result.more_available, result.next_skip) == (
        10,
        42,
        True,
        30,
    )


@pytest.mark.parametrize("count", [True, "42", None])
async def test_a_count_that_is_not_a_number_is_not_reported(count: JsonValue) -> None:
    fake = FakeOutlook({"@odata.count": count, "value": []})
    result = await mail(fake).query_messages(ODataQuery(count=True))
    assert result.total is None


async def test_long_values_are_cut_anywhere_in_an_item_and_counted() -> None:
    item = {
        "Id": "m1",
        "Body": {"Content": "x" * 50},
        "InternetMessageHeaders": [{"Name": "Received", "Value": "y" * 50}, {"Name": "To"}],
        "IsRead": True,
        "Size": 7,
    }
    fake = FakeOutlook({"value": [item]})
    result = await mail(fake).query_messages(ODataQuery(select="Body"), max_chars=10)
    (got,) = result.items
    assert got["Body"] == {"Content": "x" * 10}
    assert got["InternetMessageHeaders"] == [
        {"Name": "Received", "Value": "y" * 10},
        {"Name": "To"},
    ]
    assert (got["IsRead"], got["Size"]) == (True, 7)
    assert result.values_truncated == 2


async def test_the_apis_refusal_is_reported_as_it_gave_it() -> None:
    fake = FakeOutlook(
        {
            "error": {
                "code": "InefficientFilter",
                "message": "The restriction or sort order is too complex for this operation.",
            }
        },
        status=400,
    )
    with pytest.raises(ApiError, match=r"HTTP 400.*too complex"):
        await mail(fake).query_messages(
            ODataQuery(filter="IsRead eq false", orderby="ReceivedDateTime desc")
        )


# --- events ------------------------------------------------------------------------------


async def test_events_are_queried_on_the_default_or_one_calendar() -> None:
    fake = FakeOutlook({"value": [{"Id": "ev1", "Type": "SeriesMaster"}]})
    client = calendar(fake)
    result = await client.query_events(ODataQuery(filter="Type eq 'SeriesMaster'"))
    assert fake.path == "/me/events"
    assert fake.params["$select"] == EVENT_FIELDS
    assert result.items == [{"Id": "ev1", "Type": "SeriesMaster"}]
    await client.query_events(ODataQuery(), calendar_id="cal/1")
    assert fake.path == "/me/calendars/cal%2F1/events"


# --- through the server ------------------------------------------------------------------


def structured(result: object) -> dict[str, object]:
    assert isinstance(result, CallToolResult)
    assert result.structured_content is not None
    return result.structured_content


async def test_both_query_tools_run_through_the_server() -> None:
    fake = FakeOutlook({"@odata.count": 1, "value": [{"Id": "x", "Subject": "Hi"}]})
    settings = parse_settings(
        {"outlook_calendar": {"base_url": BASE}, "outlook_mail": {"base_url": BASE}}
    )
    async with fake.api() as cal_api, fake.api() as mail_api:
        server = build_server(
            settings,
            {"outlook_calendar": Connected(cal_api, "x"), "outlook_mail": Connected(mail_api, "x")},
        )
        messages = structured(
            await server.call_tool(
                "outlook_mail_query_messages",
                {
                    "filter": f"InternetMessageId eq '{MESSAGE_ID}'",
                    "select": "Subject,Id",
                    "count": True,
                    "folder": "inbox",
                },
            )
        )
        assert fake.path == "/me/mailfolders/inbox/messages"
        events = structured(
            await server.call_tool(
                "outlook_calendar_query_events", {"orderby": "Start/DateTime desc", "top": 5}
            )
        )
        assert fake.params["$orderby"] == "Start/DateTime desc"
        with pytest.raises(ToolError, match="skip must be zero or more"):
            await server.call_tool("outlook_mail_query_messages", {"skip": -5})
    assert messages["items"] == [{"Id": "x", "Subject": "Hi"}]
    assert messages["total"] == 1
    assert events["returned"] == 1
