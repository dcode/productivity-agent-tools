"""Helpers shared by the Outlook calendar and mail read clients.

Outlook REST v2.0 wraps a collection in ``{"value": [...]}`` and signals more
results with ``@odata.nextLink``; a person is ``{"EmailAddress": {"Name",
"Address"}}``; a time is ``{"DateTime", "TimeZone"}``. These turn those shapes
into the models the tools return, and apply the same row and body-size caps as
the other products.
"""

from pydantic import JsonValue

from productivity_agent_tools.api import ApiClient
from productivity_agent_tools.microsoft.models import EmailRef, OutlookStatus, TimeSlot
from productivity_agent_tools.payload import JsonObject, as_object, text, value

MAX_RESULTS_LIMIT: int = 100
DEFAULT_BODY_CHARS: int = 20_000
MAX_BODY_CHARS: int = 500_000

ME_PATH: str = "/me"


def clamp_limit(requested: int | None, default: int) -> int:
    """Clamp a requested row count to one page of results.

    Args:
        requested: Row cap from the caller, or None for the configured default.
        default: The configured default row cap.

    Returns:
        A count between 1 and ``MAX_RESULTS_LIMIT``.
    """
    return max(1, min(requested or default, MAX_RESULTS_LIMIT))


def clamp_chars(max_chars: int | None) -> int:
    """Clamp a requested body-size cap.

    Args:
        max_chars: Characters from the caller, or None for the default.

    Returns:
        A count between 1 and ``MAX_BODY_CHARS``.
    """
    if max_chars is None:
        return DEFAULT_BODY_CHARS
    return max(1, min(max_chars, MAX_BODY_CHARS))


def odata_items(body: JsonValue) -> list[JsonObject]:
    """Return the objects inside an OData collection's ``value`` array.

    Args:
        body: The decoded response.

    Returns:
        The collection's object members; empty if there are none.
    """
    items = value(body, "value")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def has_next(body: JsonValue) -> bool:
    """Tell whether an OData collection response has more pages.

    Args:
        body: The decoded response.

    Returns:
        True if the server sent an ``@odata.nextLink``.
    """
    return bool(text(body, "@odata.nextLink"))


def email_ref(item: JsonValue, *keys: str) -> EmailRef | None:
    """Read a person from an ``{"EmailAddress": {"Name", "Address"}}`` shape.

    Args:
        item: Decoded JSON to walk.
        *keys: Object keys leading to the person (before the ``EmailAddress`` wrapper).

    Returns:
        The person, or None when neither a name nor an address is present.
    """
    person = value(item, *keys, "EmailAddress")
    if not isinstance(person, dict):
        person = value(item, *keys)
    name = text(person, "Name") if isinstance(person, dict) else None
    address = text(person, "Address") if isinstance(person, dict) else None
    if name is None and address is None:
        return None
    return EmailRef(name=name, address=address)


def email_refs(item: JsonValue, *keys: str) -> list[EmailRef]:
    """Read a list of people, such as an event's attendees or a message's recipients.

    Args:
        item: Decoded JSON to walk.
        *keys: Object keys leading to the array.

    Returns:
        The people with a name or address; empty when there are none.
    """
    array = value(item, *keys)
    if not isinstance(array, list):
        return []
    found = [email_ref(entry) for entry in array]
    return [ref for ref in found if ref is not None]


def time_slot(item: JsonValue, *keys: str) -> TimeSlot | None:
    """Read a ``{"DateTime", "TimeZone"}`` shape.

    Args:
        item: Decoded JSON to walk.
        *keys: Object keys leading to the time.

    Returns:
        The time, or None when neither field is present.
    """
    slot = value(item, *keys)
    date_time = text(slot, "DateTime") if isinstance(slot, dict) else None
    time_zone = text(slot, "TimeZone") if isinstance(slot, dict) else None
    if date_time is None and time_zone is None:
        return None
    return TimeSlot(date_time=date_time, time_zone=time_zone)


def flag(item: JsonValue, *keys: str) -> bool | None:
    """Read a nested boolean without raising.

    Args:
        item: Decoded JSON to walk.
        *keys: Object keys to follow.

    Returns:
        The boolean at the end of the path, or None if it is absent or not a boolean.
    """
    found = value(item, *keys)
    return found if isinstance(found, bool) else None


def str_list(item: JsonValue, *keys: str) -> list[str]:
    """Read a nested array of strings without raising.

    Args:
        item: Decoded JSON to walk.
        *keys: Object keys leading to the array.

    Returns:
        The array's string members; empty when the path is not a list.
    """
    found = value(item, *keys)
    if not isinstance(found, list):
        return []
    return [entry for entry in found if isinstance(entry, str)]


def body_text(item: JsonObject) -> str:
    """Read an item's body as plain text.

    The ``Prefer: outlook.body-content-type="text"`` header means ``Body.Content``
    is already text; ``BodyPreview`` is the fallback when there is no body.

    Args:
        item: One decoded event or message.

    Returns:
        The body text, or an empty string.
    """
    return text(item, "Body", "Content") or text(item, "BodyPreview") or ""


def status(base_url: str, me: JsonObject, token_source: str, *, dry_run: bool) -> OutlookStatus:
    """Build the connection status from a ``/me`` response.

    Args:
        base_url: Address of the Outlook REST API in use.
        me: The decoded ``/me`` object.
        token_source: Where the OAuth token came from.
        dry_run: Whether writes are blocked.

    Returns:
        The status.
    """
    return OutlookStatus(
        base_url=base_url,
        token_source=token_source,
        user_name=text(me, "DisplayName"),
        email=text(me, "EmailAddress"),
        dry_run=dry_run,
    )


async def account(api: ApiClient) -> JsonObject:
    """Read the signed-in user's own mailbox account.

    Args:
        api: The adapter connected to Outlook.

    Returns:
        The decoded ``/me`` object, with ``DisplayName`` and ``EmailAddress``.

    Raises:
        AgentToolsError: If the request fails or the body is not an object.
    """
    return as_object(await api.get(ME_PATH), "account")
