"""The read-only ``outlook_calendar`` product: Microsoft 365 calendar reads.

One of the two Outlook products of the Microsoft stopgap (see docs/ROADMAP.md).
It shares the ``microsoft`` keychain token with ``outlook_mail`` and reads the
Outlook REST API v2.0. Every tool here is a read; there are no writes.
"""

from collections.abc import AsyncIterator

from mcp.server.mcpserver import MCPServer

from productivity_agent_tools.microsoft.calendar_client import OutlookCalendarClient
from productivity_agent_tools.microsoft.credentials import microsoft_token
from productivity_agent_tools.microsoft.models import (
    CalendarList,
    Event,
    EventList,
    OutlookStatus,
    QueryResult,
)
from productivity_agent_tools.microsoft.odata import ODataQuery
from productivity_agent_tools.microsoft.outlook import OutlookClient
from productivity_agent_tools.microsoft.outlook_settings import (
    OutlookSettings,
    parse_outlook_settings,
)
from productivity_agent_tools.microsoft.reads import status
from productivity_agent_tools.payload import text
from productivity_agent_tools.products import Product, ToolContext
from productivity_agent_tools.tooling import READ, anticipated

INSTRUCTIONS: str = (
    "The outlook_calendar_ tools read the user's Microsoft 365 calendar (events and "
    "calendars) read-only over the Outlook REST API. The sign-in is a refresh token seeded "
    "once per sign-in window (about 18 hours); when a tool reports that the token can no "
    "longer be redeemed, do not retry: ask the user to run "
    "'productivity-agent-tools login microsoft' in a terminal, then continue."
)


def register_calendar_tools(
    server: MCPServer, calendar: OutlookCalendarClient, context: ToolContext
) -> None:
    """Add the Outlook calendar tools to a server.

    Args:
        server: The MCP server to add tools to.
        calendar: The calendar client the tools call.
        context: Dry-run and token source for the status tool.
    """

    @server.tool(annotations=READ)
    @anticipated
    async def outlook_calendar_status() -> OutlookStatus:
        """Check the Outlook calendar connection: who is signed in, and the token source.

        Call this first if another outlook_calendar tool fails. If it says the
        token can no longer be redeemed, only the user can fix that: ask them to
        run 'productivity-agent-tools login microsoft'.
        """
        source = await context.current_source()
        return status(calendar.base_url, await calendar.me(), source, dry_run=context.dry_run)

    @server.tool(annotations=READ)
    @anticipated
    async def outlook_calendar_list_calendars(max_results: int | None = None) -> CalendarList:
        """List the user's calendars, with the ids outlook_calendar_list_events accepts.

        Args:
            max_results: Maximum rows to return (default from config, at most 100).
        """
        return await calendar.list_calendars(max_results)

    @server.tool(annotations=READ)
    @anticipated
    async def outlook_calendar_list_events(
        start: str | None = None,
        end: str | None = None,
        calendar_id: str | None = None,
        max_results: int | None = None,
    ) -> EventList:
        """List calendar events in a date range, with recurring events expanded.

        Times come back normalized to UTC. Use outlook_calendar_get_event with
        an id to read an event's full body and attendees.

        Args:
            start: Start of the range, a date (2026-11-20) or date-time; leave out for now.
            end: End of the range; leave out for seven days after now.
            calendar_id: Limit to one calendar, from outlook_calendar_list_calendars;
                leave out for the default calendar.
            max_results: Maximum rows to return (default from config, at most 100).
        """
        return await calendar.list_events(start, end, calendar_id, max_results)

    @server.tool(annotations=READ)
    @anticipated
    async def outlook_calendar_query_events(
        filter: str | None = None,
        select: str | None = None,
        orderby: str | None = None,
        expand: str | None = None,
        top: int | None = None,
        skip: int | None = None,
        count: bool = False,
        calendar_id: str | None = None,
        max_chars: int | None = None,
    ) -> QueryResult:
        """Query events with OData v4 options, for what outlook_calendar_list_events cannot do.

        This reads the events themselves, so a recurring meeting is one item (its
        series master, Type "SeriesMaster") rather than one per occurrence; to see
        what is on the calendar on given days, use outlook_calendar_list_events.
        The options go to the Outlook REST API as written. Property names are
        PascalCase (Subject, Start/DateTime, End/DateTime, Organizer, Location,
        IsAllDay, ShowAs, Importance, Categories, Type), as results name them;
        camelCase is accepted too. String values go in single quotes. Examples:

        - Meetings a person organized: filter="Organizer/EmailAddress/Address eq
          'pat@example.mil'"
        - By subject: filter="contains(Subject, 'standup')"
        - Recurring series only: filter="Type eq 'SeriesMaster'"
        - With attendees: select="Subject,Start,Attendees"

        If the API answers "InefficientFilter" to a filter plus an orderby, put the
        orderby property first in the filter as well.

        Args:
            filter: OData $filter expression.
            select: Comma-separated properties to return; leave out for a compact set
                (no body, no attendees).
            orderby: OData $orderby, such as "Start/DateTime desc".
            expand: OData $expand, such as "Attachments($select=Name)".
            top: Rows per page (default from config, at most 100).
            skip: Rows to skip, for the next page: pass next_skip from the last result.
            count: True to have the API report the total number of matches.
            calendar_id: Limit to one calendar, from outlook_calendar_list_calendars;
                leave out for the default calendar.
            max_chars: Cut any long text value here (default 20000, at most 500000).
        """
        query = ODataQuery(
            filter=filter,
            select=select,
            orderby=orderby,
            expand=expand,
            top=top,
            skip=skip,
            count=count,
        )
        return await calendar.query_events(query, calendar_id, max_chars)

    @server.tool(annotations=READ)
    @anticipated
    async def outlook_calendar_get_event(event_id: str, max_chars: int | None = None) -> Event:
        """Read one event: its body (as text), attendees and their responses.

        Args:
            event_id: Event id, from outlook_calendar_list_events.
            max_chars: Cut the body here (default 20000, at most 500000).
        """
        return await calendar.get_event(event_id, max_chars)


async def doctor(calendar: OutlookCalendarClient) -> AsyncIterator[str]:
    """Check that the Outlook token reads the mailbox account.

    Args:
        calendar: The calendar client.

    Yields:
        One line per check that passed.

    Raises:
        AgentToolsError: When a check fails.
    """
    me = await calendar.me()
    who = text(me, "DisplayName") or text(me, "EmailAddress")
    yield f"authenticated to {calendar.base_url} as {who} (calendar)"


PRODUCT: Product[OutlookSettings, OutlookCalendarClient] = Product(
    name="outlook_calendar",
    title="Outlook Calendar",
    instructions=INSTRUCTIONS,
    settings_type=OutlookSettings,
    adapter=OutlookClient.from_connection,
    parse_settings=lambda raw: parse_outlook_settings(raw, "outlook_calendar"),
    make_client=OutlookCalendarClient,
    register_tools=register_calendar_tools,
    doctor=doctor,
    token_name="microsoft",
    resolve_credential=microsoft_token,
)
