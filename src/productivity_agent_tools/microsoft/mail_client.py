"""Read-only mail operations on the Outlook REST API v2.0.

Messages are listed newest first, optionally filtered to the unread ones, and
searched with Outlook's own ``$search`` (the same relevance search the webmail
box uses). Bodies come back as plain text thanks to the adapter's ``Prefer``
header, so a message read for the agent carries no HTML markup.
"""

from urllib.parse import quote

from pydantic import JsonValue

from productivity_agent_tools.api import ApiClient
from productivity_agent_tools.errors import AgentToolsError
from productivity_agent_tools.microsoft.models import (
    Message,
    MessageHeader,
    MessageHeaders,
    MessageList,
    MessageSummary,
)
from productivity_agent_tools.microsoft.outlook_settings import OutlookSettings
from productivity_agent_tools.microsoft.reads import (
    account,
    body_text,
    clamp_chars,
    clamp_limit,
    email_ref,
    email_refs,
    flag,
    has_next,
    odata_items,
)
from productivity_agent_tools.payload import JsonObject, as_object, text

_ORDER_BY_RECEIVED: str = "ReceivedDateTime desc"
_UNREAD_FILTER: str = "IsRead eq false"


class OutlookMailClient:
    """Reads mail messages from one mailbox through an ``ApiClient``."""

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

    async def list_messages(
        self, unread_only: bool = False, max_results: int | None = None
    ) -> MessageList:
        """List messages, newest first, optionally only the unread ones.

        Args:
            unread_only: True to return only unread messages.
            max_results: Row cap.

        Returns:
            The messages, without bodies.

        Raises:
            AgentToolsError: If the request fails.
        """
        params: dict[str, str | int] = {
            "$top": clamp_limit(max_results, self.settings.default_max_results),
            "$orderby": _ORDER_BY_RECEIVED,
        }
        if unread_only:
            params["$filter"] = _UNREAD_FILTER
        body = await self._api.get("/me/messages", params)
        return self._message_list(body)

    async def search_messages(self, query: str, max_results: int | None = None) -> MessageList:
        """Search messages with Outlook's relevance search.

        Args:
            query: The search text, matched across subject, sender and body.
            max_results: Row cap.

        Returns:
            The matching messages, without bodies.

        Raises:
            AgentToolsError: If the query is empty or the request fails.
        """
        cleaned = query.strip()
        if not cleaned:
            raise AgentToolsError("The search query must not be empty.")
        # $search cannot be combined with $orderby on Outlook REST; relevance decides order.
        params: dict[str, str | int] = {
            "$search": f'"{cleaned}"',
            "$top": clamp_limit(max_results, self.settings.default_max_results),
        }
        body = await self._api.get("/me/messages", params)
        return self._message_list(body)

    async def get_message(self, message_id: str, max_chars: int | None = None) -> Message:
        """Read one message in full, with its body as text.

        Args:
            message_id: The message id, from a list or search.
            max_chars: Cut the body here (default 20000, at most 500000).

        Returns:
            The message.

        Raises:
            AgentToolsError: If the id is empty or the request fails.
        """
        cleaned = message_id.strip()
        if not cleaned:
            raise AgentToolsError("Give a message id; outlook_mail_list_messages shows them.")
        found = await self._api.get(f"/me/messages/{quote(cleaned, safe='')}")
        return self._message(as_object(found, "message"), clamp_chars(max_chars))

    async def get_message_headers(self, message_id: str) -> MessageHeaders:
        """Read a message's internet (MIME) headers.

        The headers are not part of a normal message read: Outlook only returns
        ``InternetMessageHeaders`` when it is asked for by itself, so this is a
        request of its own. The headers carry the sender-authentication results
        (SPF/DKIM/DMARC), the relay chain and any mailing-list markers, which is
        what spam and phishing triage needs.

        Args:
            message_id: The message id, from a list or search.

        Returns:
            The headers, in order, with duplicates kept.

        Raises:
            AgentToolsError: If the id is empty or the request fails.
        """
        cleaned = message_id.strip()
        if not cleaned:
            raise AgentToolsError("Give a message id; outlook_mail_list_messages shows them.")
        found = await self._api.get(
            f"/me/messages/{quote(cleaned, safe='')}", {"$select": "InternetMessageHeaders"}
        )
        item = as_object(found, "message")
        headers = [
            MessageHeader(name=name, value=text(entry, "Value") or "")
            for entry in _header_items(item)
            if (name := text(entry, "Name"))
        ]
        return MessageHeaders(id=text(item, "Id") or cleaned, count=len(headers), headers=headers)

    def _message_list(self, body: JsonValue) -> MessageList:
        """Build a message list from a collection response.

        Args:
            body: The decoded response.

        Returns:
            The messages.
        """
        messages = [self._summary(item) for item in odata_items(body)]
        return MessageList(returned=len(messages), more_available=has_next(body), messages=messages)

    def _summary(self, item: JsonObject) -> MessageSummary:
        """Build the compact view of a message.

        Args:
            item: One decoded message object.

        Returns:
            The message summary.
        """
        return MessageSummary(
            id=text(item, "Id") or "",
            subject=text(item, "Subject"),
            sender=email_ref(item, "From"),
            to=email_refs(item, "ToRecipients"),
            received=text(item, "ReceivedDateTime"),
            is_read=flag(item, "IsRead"),
            has_attachments=flag(item, "HasAttachments"),
            importance=text(item, "Importance"),
            preview=text(item, "BodyPreview"),
            web_link=text(item, "WebLink"),
        )

    def _message(self, item: JsonObject, max_chars: int) -> Message:
        """Build the full view of a message.

        Args:
            item: One decoded message object.
            max_chars: Cut the body at this many characters.

        Returns:
            The message.
        """
        body = body_text(item)
        return Message(
            **self._summary(item).model_dump(),
            cc=email_refs(item, "CcRecipients"),
            conversation_id=text(item, "ConversationId"),
            body=body[:max_chars],
            body_chars=len(body),
            truncated=len(body) > max_chars,
        )


def _header_items(item: JsonObject) -> list[JsonObject]:
    """Return a message's internet-header objects.

    Args:
        item: One decoded message object.

    Returns:
        The header objects (``{"Name", "Value"}``); empty when there are none.
    """
    headers = item.get("InternetMessageHeaders")
    if not isinstance(headers, list):
        return []
    return [entry for entry in headers if isinstance(entry, dict)]
