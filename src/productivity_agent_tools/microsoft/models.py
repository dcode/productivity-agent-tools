"""Result models returned by the Outlook calendar and mail tools.

Outlook REST v2.0 answers in PascalCase (``Subject``, ``ReceivedDateTime``,
``EmailAddress``); the clients normalize that into the snake_case shapes here
so the agent sees one consistent vocabulary.
"""

from pydantic import BaseModel, Field


class OutlookStatus(BaseModel):
    """Connection status reported by the outlook_*_status tools."""

    base_url: str = Field(description="Address of the Outlook REST API in use.")
    token_source: str = Field(
        description="Where the OAuth token comes from, read when this tool is called: when "
        "the user last signed in, until about when that sign-in can be renewed (if the "
        "window is configured), when the refresh token was last renewed, and when the "
        "access token in use expires. Use it to answer 'when does my sign-in expire'."
    )
    user_name: str | None = Field(default=None, description="Signed-in user's display name.")
    email: str | None = Field(default=None, description="Signed-in user's primary email address.")
    dry_run: bool = Field(
        description="True if writes are blocked. These products are read-only, so no tool "
        "writes regardless."
    )


class EmailRef(BaseModel):
    """A person as Outlook names them on an event or message."""

    name: str | None = Field(default=None, description="Display name.")
    address: str | None = Field(default=None, description="Email address.")


class TimeSlot(BaseModel):
    """One end of an event, as a date-time and its time zone."""

    date_time: str | None = Field(
        default=None, description="Local date and time, ISO 8601 (normalized to UTC)."
    )
    time_zone: str | None = Field(default=None, description="Time zone the date-time is in.")


# --- calendar ---------------------------------------------------------------------------


class Calendar(BaseModel):
    """One of the user's calendars."""

    id: str = Field(description="Calendar id, used by outlook_calendar_list_events.")
    name: str | None = Field(default=None, description="Calendar name.")
    owner: EmailRef | None = Field(default=None, description="Who owns the calendar.")
    can_edit: bool | None = Field(
        default=None, description="Whether the user may edit items in it."
    )
    can_share: bool | None = Field(default=None, description="Whether the user may share it.")


class CalendarList(BaseModel):
    """The calendars the user can see."""

    returned: int = Field(description="Number of calendars in this result.")
    more_available: bool = Field(description="True if more calendars exist than were returned.")
    calendars: list[Calendar] = Field(description="The calendars.")


class Attendee(BaseModel):
    """One invitee on an event."""

    name: str | None = Field(default=None, description="Display name.")
    address: str | None = Field(default=None, description="Email address.")
    type: str | None = Field(default=None, description="required, optional or resource.")
    response: str | None = Field(
        default=None, description="Their response: none, accepted, declined, tentative."
    )


class EventSummary(BaseModel):
    """One calendar event, without its body."""

    id: str = Field(description="Event id, used by outlook_calendar_get_event.")
    subject: str | None = Field(default=None, description="Event subject.")
    start: TimeSlot | None = Field(default=None, description="When it starts.")
    end: TimeSlot | None = Field(default=None, description="When it ends.")
    is_all_day: bool | None = Field(default=None, description="True for an all-day event.")
    location: str | None = Field(default=None, description="Location display name.")
    organizer: EmailRef | None = Field(default=None, description="Who organized it.")
    show_as: str | None = Field(
        default=None, description="free, tentative, busy, oof, workingElsewhere."
    )
    is_cancelled: bool | None = Field(default=None, description="True if the event was cancelled.")
    web_link: str | None = Field(default=None, description="Browser URL to open the event.")


class Event(EventSummary):
    """A calendar event with its body and attendees."""

    importance: str | None = Field(default=None, description="low, normal or high.")
    sensitivity: str | None = Field(
        default=None, description="normal, personal, private or confidential."
    )
    online_meeting_url: str | None = Field(default=None, description="Join URL, if any.")
    categories: list[str] = Field(description="Categories on the event.")
    attendees: list[Attendee] = Field(description="The invitees and their responses.")
    last_modified: str | None = Field(default=None, description="When the event last changed.")
    body: str = Field(description="Event body as plain text.")
    body_chars: int = Field(description="Length of the full body in characters.")
    truncated: bool = Field(description="True if the body was cut at max_chars.")


class EventList(BaseModel):
    """Events found in a date range."""

    returned: int = Field(description="Number of events in this result.")
    more_available: bool = Field(description="True if more events fall in the range than returned.")
    events: list[EventSummary] = Field(description="The events, earliest first.")


# --- mail -------------------------------------------------------------------------------


class MessageSummary(BaseModel):
    """One mail message, without its body."""

    id: str = Field(description="Message id, used by outlook_mail_get_message.")
    subject: str | None = Field(default=None, description="Subject line.")
    sender: EmailRef | None = Field(default=None, description="Who the message is from.")
    to: list[EmailRef] = Field(description="Direct recipients.")
    received: str | None = Field(default=None, description="When it was received, ISO 8601.")
    is_read: bool | None = Field(default=None, description="True if the user has read it.")
    has_attachments: bool | None = Field(default=None, description="True if it has attachments.")
    importance: str | None = Field(default=None, description="low, normal or high.")
    preview: str | None = Field(default=None, description="Short preview of the body.")
    web_link: str | None = Field(default=None, description="Browser URL to open the message.")


class Message(MessageSummary):
    """A mail message with its body."""

    cc: list[EmailRef] = Field(description="Carbon-copy recipients.")
    conversation_id: str | None = Field(default=None, description="Id of the conversation thread.")
    body: str = Field(description="Message body as plain text.")
    body_chars: int = Field(description="Length of the full body in characters.")
    truncated: bool = Field(description="True if the body was cut at max_chars.")


class MessageHeader(BaseModel):
    """One internet (MIME) header of a message."""

    name: str = Field(description="Header name, such as Authentication-Results or Received.")
    value: str = Field(description="Header value, verbatim.")


class MessageHeaders(BaseModel):
    """A message's internet headers, in the order the sending servers wrote them."""

    id: str = Field(description="Id of the message these headers belong to.")
    count: int = Field(description="Number of headers returned.")
    headers: list[MessageHeader] = Field(
        description="The headers in order (duplicates such as multiple Received lines are kept). "
        "Useful ones for triage: Authentication-Results (SPF/DKIM/DMARC results), Received "
        "(the relay chain), Return-Path, Reply-To, List-Id and List-Unsubscribe (mailing "
        "lists), and X-* anti-spam scores."
    )


class MessageList(BaseModel):
    """Messages found by a list or search."""

    returned: int = Field(description="Number of messages in this result.")
    more_available: bool = Field(description="True if more matched than were returned.")
    messages: list[MessageSummary] = Field(description="The messages, newest first.")
