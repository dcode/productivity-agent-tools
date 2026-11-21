"""Read-only calendar operations on the Outlook REST API v2.0.

Events in a date range come from ``calendarview``, which expands recurring
series into their occurrences, so "what is on my calendar this week" returns
the actual meetings rather than the recurrence masters that ``/me/events``
would give.
"""

from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from productivity_agent_tools.api import ApiClient
from productivity_agent_tools.capabilities import SourcedMeeting
from productivity_agent_tools.errors import AgentToolsError
from productivity_agent_tools.microsoft.models import (
    Attendee,
    Calendar,
    CalendarList,
    Event,
    EventList,
    EventSummary,
    QueryResult,
    TimeSlot,
)
from productivity_agent_tools.microsoft.odata import EVENT_FIELDS, ODataQuery, run_query
from productivity_agent_tools.microsoft.outlook_settings import OutlookSettings
from productivity_agent_tools.microsoft.reads import (
    account,
    body_text,
    clamp_chars,
    clamp_limit,
    email_ref,
    flag,
    has_next,
    odata_items,
    str_list,
    time_slot,
)
from productivity_agent_tools.payload import JsonObject, as_object, text

DEFAULT_WINDOW_DAYS: int = 7
_EVENT_ORDER: str = "Start/DateTime"
_MEETING_PAGE: int = 100
_MEETING_PAGES: int = 10
"""Most pages ``my_meetings`` reads: 1,000 events, more than a few weeks of anyone's."""


def _bound(value: str | None, default: datetime, *, end_of_day: bool) -> str:
    """Normalize a date or date-time range bound into an ISO 8601 date-time.

    Args:
        value: The caller's value, a date (``2026-11-20``) or a date-time, or None.
        default: The date-time to use when ``value`` is None.
        end_of_day: For a bare date, use the end of the day rather than its start.

    Returns:
        An ISO 8601 date-time string.
    """
    cleaned = (value or "").strip()
    if not cleaned:
        return default.isoformat(timespec="seconds")
    if "T" in cleaned:
        return cleaned
    return f"{cleaned}T23:59:59" if end_of_day else f"{cleaned}T00:00:00"


class OutlookCalendarClient:
    """Reads calendars and events from one mailbox through an ``ApiClient``."""

    def __init__(self, api: ApiClient, settings: OutlookSettings) -> None:
        """Create the client.

        Args:
            api: The adapter connected to the Outlook REST API.
            settings: The product's settings.
        """
        self._api = api
        self.settings = settings

    @property
    def base_url(self) -> str:
        """Address of the Outlook REST API in use."""
        return self._api.base_url

    async def me(self) -> JsonObject:
        """Read the signed-in user's mailbox account.

        Returns:
            The decoded ``/me`` object.

        Raises:
            AgentToolsError: If the request fails.
        """
        return await account(self._api)

    async def list_calendars(self, max_results: int | None = None) -> CalendarList:
        """List the calendars the user can see.

        Args:
            max_results: Row cap.

        Returns:
            The calendars.

        Raises:
            AgentToolsError: If the request fails.
        """
        limit = clamp_limit(max_results, self.settings.default_max_results)
        body = await self._api.get("/me/calendars", {"$top": limit})
        calendars = [self._calendar(item) for item in odata_items(body)]
        return CalendarList(
            returned=len(calendars), more_available=has_next(body), calendars=calendars
        )

    def _calendar(self, item: JsonObject) -> Calendar:
        """Build the view of a calendar.

        Args:
            item: One decoded calendar object.

        Returns:
            The calendar.
        """
        return Calendar(
            id=text(item, "Id") or "",
            name=text(item, "Name"),
            owner=email_ref(item, "Owner"),
            can_edit=flag(item, "CanEdit"),
            can_share=flag(item, "CanShare"),
        )

    async def list_events(
        self,
        start: str | None = None,
        end: str | None = None,
        calendar_id: str | None = None,
        max_results: int | None = None,
    ) -> EventList:
        """List events that fall in a date range, recurrences expanded.

        Args:
            start: Start of the range, a date or date-time; None for now.
            end: End of the range; None for ``DEFAULT_WINDOW_DAYS`` after now.
            calendar_id: Limit to one calendar; None for the default calendar.
            max_results: Row cap.

        Returns:
            The events, earliest first.

        Raises:
            AgentToolsError: If the request fails.
        """
        now = datetime.now(UTC)
        params: dict[str, str | int] = {
            "startDateTime": _bound(start, now, end_of_day=False),
            "endDateTime": _bound(end, now + timedelta(days=DEFAULT_WINDOW_DAYS), end_of_day=True),
            "$orderby": _EVENT_ORDER,
            "$top": clamp_limit(max_results, self.settings.default_max_results),
        }
        path = "/me/calendarview"
        if calendar_id and calendar_id.strip():
            path = f"/me/calendars/{quote(calendar_id.strip(), safe='')}/calendarview"
        body = await self._api.get(path, params)
        events = [self._summary(item) for item in odata_items(body)]
        return EventList(returned=len(events), more_available=has_next(body), events=events)

    async def my_meetings(self, start: datetime, end: datetime) -> list[SourcedMeeting]:
        """The meetings starting in a time range, for the Obsidian sync.

        Events without an ``iCalUId`` cannot be tied to a note and are left out.

        Args:
            start: Start of the range, time-zone aware.
            end: End of the range, time-zone aware.

        Returns:
            The meetings, earliest first, with UTC times; pages of 100 are followed up
            to ``_MEETING_PAGES`` of them.

        Raises:
            AgentToolsError: If the request fails.
        """
        params: dict[str, str | int] = {
            "startDateTime": start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endDateTime": end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "$orderby": _EVENT_ORDER,
            "$top": _MEETING_PAGE,
        }
        items: list[JsonObject] = []
        for page in range(_MEETING_PAGES):
            params["$skip"] = page * _MEETING_PAGE
            body = await self._api.get("/me/calendarview", params)
            items.extend(odata_items(body))
            if not has_next(body):
                break
        meetings: list[SourcedMeeting] = []
        for item in items:
            event = self._summary(item)
            begins = _utc(event.start)
            finishes = _utc(event.end) or begins
            if not event.ical_uid or begins is None or finishes is None:
                continue
            meetings.append(
                SourcedMeeting(
                    ical_uid=event.ical_uid,
                    title=event.subject or "(no subject)",
                    start=begins,
                    end=finishes,
                    series_id=event.series_master_id,
                    all_day=bool(event.is_all_day),
                    cancelled=bool(event.is_cancelled),
                    response=event.response,
                    show_as=event.show_as,
                    organizer=event.organizer.name if event.organizer else None,
                    location=event.location,
                    join_url=event.online_meeting_url,
                    url=event.web_link,
                )
            )
        return meetings

    async def get_event(self, event_id: str, max_chars: int | None = None) -> Event:
        """Read one event in full, with its body and attendees.

        Args:
            event_id: The event id, from outlook_calendar_list_events.
            max_chars: Cut the body here (default 20000, at most 500000).

        Returns:
            The event.

        Raises:
            AgentToolsError: If the id is empty or the request fails.
        """
        cleaned = event_id.strip()
        if not cleaned:
            raise AgentToolsError("Give an event id; outlook_calendar_list_events shows them.")
        found = await self._api.get(f"/me/events/{quote(cleaned, safe='')}")
        return self._event(as_object(found, "event"), clamp_chars(max_chars))

    async def query_events(
        self, query: ODataQuery, calendar_id: str | None = None, max_chars: int | None = None
    ) -> QueryResult:
        """Query events with OData options, on every calendar's default or one calendar.

        This reads ``/events``, where a recurring meeting is one series master
        rather than one item per occurrence; ``list_events`` expands them.

        Args:
            query: The OData options, passed to the API as written.
            calendar_id: Limit to one calendar; None for the default calendar.
            max_chars: Cut any string value here (default 20000, at most 500000).

        Returns:
            One page of events, with the API's property names.

        Raises:
            AgentToolsError: If an option is invalid or the API refuses the query.
        """
        path = "/me/events"
        if cleaned := (calendar_id or "").strip():
            path = f"/me/calendars/{quote(cleaned, safe='')}/events"
        return await run_query(
            self._api,
            path,
            query,
            default_top=self.settings.default_max_results,
            default_select=EVENT_FIELDS,
            max_chars=clamp_chars(max_chars),
        )

    def _summary(self, item: JsonObject) -> EventSummary:
        """Build the compact view of an event.

        Args:
            item: One decoded event object.

        Returns:
            The event summary.
        """
        return EventSummary(
            id=text(item, "Id") or "",
            subject=text(item, "Subject"),
            start=time_slot(item, "Start"),
            end=time_slot(item, "End"),
            is_all_day=flag(item, "IsAllDay"),
            location=text(item, "Location", "DisplayName"),
            organizer=email_ref(item, "Organizer"),
            show_as=text(item, "ShowAs"),
            is_cancelled=flag(item, "IsCancelled"),
            web_link=text(item, "WebLink"),
            online_meeting_url=(
                text(item, "OnlineMeetingUrl") or text(item, "OnlineMeeting", "JoinUrl")
            ),
            # The one property the Outlook REST API names in camelCase (seen live 2026-11-19).
            ical_uid=text(item, "iCalUId"),
            series_master_id=text(item, "SeriesMasterId"),
            type=text(item, "Type"),
            is_organizer=flag(item, "IsOrganizer"),
            response=text(item, "ResponseStatus", "Response"),
        )

    def _event(self, item: JsonObject, max_chars: int) -> Event:
        """Build the full view of an event.

        Args:
            item: One decoded event object.
            max_chars: Cut the body at this many characters.

        Returns:
            The event.
        """
        body = body_text(item)
        return Event(
            **self._summary(item).model_dump(),
            importance=text(item, "Importance"),
            sensitivity=text(item, "Sensitivity"),
            categories=str_list(item, "Categories"),
            attendees=[self._attendee(a) for a in _attendee_items(item)],
            last_modified=text(item, "LastModifiedDateTime"),
            body=body[:max_chars],
            body_chars=len(body),
            truncated=len(body) > max_chars,
        )

    def _attendee(self, item: JsonObject) -> Attendee:
        """Build the view of one attendee.

        Args:
            item: One decoded attendee object.

        Returns:
            The attendee.
        """
        ref = email_ref(item)
        return Attendee(
            name=ref.name if ref else None,
            address=ref.address if ref else None,
            type=text(item, "Type"),
            response=text(item, "Status", "Response"),
        )


def _utc(slot: TimeSlot | None) -> datetime | None:
    """Read an event time, which the API sends in UTC (the adapter asks it to).

    Args:
        slot: The event's start or end.

    Returns:
        The time, aware and in UTC, or None when it is missing or malformed.
    """
    if slot is None or not slot.date_time:
        return None
    try:
        parsed = datetime.fromisoformat(slot.date_time)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _attendee_items(item: JsonObject) -> list[JsonObject]:
    """Return an event's attendee objects.

    Args:
        item: One decoded event object.

    Returns:
        The attendee objects; empty when there are none.
    """
    attendees = item.get("Attendees")
    if not isinstance(attendees, list):
        return []
    return [entry for entry in attendees if isinstance(entry, dict)]
