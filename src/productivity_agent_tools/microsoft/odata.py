"""OData v4 query options passed through to an Outlook REST collection, read-only.

The fixed tools (list, search, get) cover the everyday questions with a stable
shape. This is the escape hatch for everything else: finding a message by its
``InternetMessageId``, filtering on any property the API can filter on, choosing
the fields that come back, paging with ``$skip`` and counting with ``$count``.

The options are handed to the API as written, so its own rules and error
messages apply. Property names are the API's PascalCase ones, and items come
back with the API's property names unchanged, so a name seen in a result can be
used in the next ``$filter`` or ``$select``. Only ``GET`` is ever sent.
"""

from dataclasses import dataclass

from pydantic import JsonValue

from productivity_agent_tools.api import ApiClient
from productivity_agent_tools.errors import AgentToolsError
from productivity_agent_tools.microsoft.models import QueryResult
from productivity_agent_tools.microsoft.reads import clamp_limit, has_next, odata_items
from productivity_agent_tools.payload import value

MESSAGE_FIELDS: str = (
    "Id,Subject,From,ToRecipients,ReceivedDateTime,IsRead,HasAttachments,"
    "InternetMessageId,ConversationId,BodyPreview,WebLink"
)
"""What a message query returns when no ``$select`` is given: no body, no headers."""

EVENT_FIELDS: str = (
    "Id,Subject,Start,End,IsAllDay,Location,Organizer,ShowAs,IsCancelled,Type,"
    "SeriesMasterId,WebLink"
)
"""What an event query returns when no ``$select`` is given: no body, no attendees."""


def _option(given: str | None) -> str | None:
    """Return an option's text, or None when it is absent or blank."""
    cleaned = (given or "").strip()
    return cleaned or None


@dataclass(frozen=True, slots=True, kw_only=True)
class ODataQuery:
    """The OData v4 query options a query tool accepts.

    Attributes:
        filter: ``$filter`` expression, such as ``InternetMessageId eq '<id@host>'``.
        select: ``$select`` property list; None for the tool's compact default.
        orderby: ``$orderby``, such as ``ReceivedDateTime desc``.
        expand: ``$expand``, such as ``Attachments`` or an extended property.
        top: ``$top`` row cap, clamped to one page.
        skip: ``$skip``, rows to pass over, for paging.
        count: ``$count=true``, to have the API report the total number of matches.
    """

    filter: str | None = None
    select: str | None = None
    orderby: str | None = None
    expand: str | None = None
    top: int | None = None
    skip: int | None = None
    count: bool = False

    def params(self, *, default_top: int, default_select: str) -> dict[str, str | int]:
        """Build the request's query parameters.

        Args:
            default_top: Row cap when ``top`` is not given.
            default_select: ``$select`` when ``select`` is not given.

        Returns:
            The parameters, keyed by their ``$`` names.

        Raises:
            AgentToolsError: If ``skip`` is negative.
        """
        if self.skip is not None and self.skip < 0:
            raise AgentToolsError("skip must be zero or more.")
        params: dict[str, str | int] = {
            "$top": clamp_limit(self.top, default_top),
            "$select": _option(self.select) or default_select,
        }
        for name, given in (
            ("$filter", self.filter),
            ("$orderby", self.orderby),
            ("$expand", self.expand),
        ):
            if cleaned := _option(given):
                params[name] = cleaned
        if self.skip:
            params["$skip"] = self.skip
        if self.count:
            params["$count"] = "true"
        return params


def _clean(item: JsonValue, max_chars: int, cut: list[int]) -> JsonValue:
    """Drop OData annotations and shorten long strings, anywhere in an item.

    Args:
        item: Part of a decoded item.
        max_chars: Longest string kept whole.
        cut: One entry is appended per string that was shortened.

    Returns:
        The cleaned copy.
    """
    if isinstance(item, str):
        if len(item) > max_chars:
            cut.append(len(item))
            return item[:max_chars]
        return item
    if isinstance(item, list):
        return [_clean(entry, max_chars, cut) for entry in item]
    if isinstance(item, dict):
        return {
            key: _clean(entry, max_chars, cut)
            for key, entry in item.items()
            if not key.startswith("@odata.")
        }
    return item


async def run_query(
    api: ApiClient,
    path: str,
    query: ODataQuery,
    *,
    default_top: int,
    default_select: str,
    max_chars: int,
) -> QueryResult:
    """Send one OData query for a collection and shape the page that comes back.

    Args:
        api: The adapter connected to Outlook.
        path: The collection, such as ``/me/messages``.
        query: The options.
        default_top: Row cap when the query gives none.
        default_select: Properties to return when the query selects none.
        max_chars: Longest string value kept whole in the items.

    Returns:
        The page of items, with the total when ``$count`` was asked for.

    Raises:
        AgentToolsError: If an option is invalid or the API refuses the query.
    """
    params = query.params(default_top=default_top, default_select=default_select)
    body = await api.get(path, params)
    cut: list[int] = []
    items = [_clean(item, max_chars, cut) for item in odata_items(body)]
    total = value(body, "@odata.count")
    more = has_next(body)
    skipped = query.skip or 0
    return QueryResult(
        returned=len(items),
        total=total if isinstance(total, int) and not isinstance(total, bool) else None,
        more_available=more,
        next_skip=skipped + len(items) if more else None,
        values_truncated=len(cut),
        items=[item for item in items if isinstance(item, dict)],
    )
