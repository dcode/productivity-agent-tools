"""The read-only ``outlook_mail`` product: Microsoft 365 mail reads.

One of the two Outlook products of the Microsoft stopgap (see docs/ROADMAP.md).
It shares the ``microsoft`` keychain token with ``outlook_calendar`` and reads
the Outlook REST API v2.0. Every tool here is a read; there are no writes.
"""

from collections.abc import AsyncIterator

from mcp.server.mcpserver import MCPServer

from productivity_agent_tools.microsoft.credentials import microsoft_token
from productivity_agent_tools.microsoft.mail_client import OutlookMailClient
from productivity_agent_tools.microsoft.models import (
    Message,
    MessageHeaders,
    MessageList,
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
    "The outlook_mail_ tools read the user's Microsoft 365 mailbox (list, search and read "
    "messages) read-only over the Outlook REST API. The sign-in is a refresh token seeded "
    "once per sign-in window (about 18 hours); when a tool reports that the token can no "
    "longer be redeemed, do not retry: ask the user to run "
    "'productivity-agent-tools login microsoft' in a terminal, then continue."
)


def register_mail_tools(server: MCPServer, mail: OutlookMailClient, context: ToolContext) -> None:
    """Add the Outlook mail tools to a server.

    Args:
        server: The MCP server to add tools to.
        mail: The mail client the tools call.
        context: Dry-run and token source for the status tool.
    """

    @server.tool(annotations=READ)
    @anticipated
    async def outlook_mail_status() -> OutlookStatus:
        """Check the Outlook mail connection: who is signed in, and the token source.

        Call this first if another outlook_mail tool fails. If it says the token
        can no longer be redeemed, only the user can fix that: ask them to run
        'productivity-agent-tools login microsoft'.
        """
        return status(
            mail.base_url, await mail.me(), await context.current_source(), dry_run=context.dry_run
        )

    @server.tool(annotations=READ)
    @anticipated
    async def outlook_mail_list_messages(
        unread_only: bool = False, max_results: int | None = None
    ) -> MessageList:
        """List mailbox messages, newest first. Use outlook_mail_get_message to read one.

        Args:
            unread_only: True to return only unread messages.
            max_results: Maximum rows to return (default from config, at most 100).
        """
        return await mail.list_messages(unread_only, max_results)

    @server.tool(annotations=READ)
    @anticipated
    async def outlook_mail_search_messages(
        query: str, max_results: int | None = None
    ) -> MessageList:
        """Search the mailbox with Outlook's own relevance search (subject, sender, body).

        Args:
            query: The text to search for.
            max_results: Maximum rows to return (default from config, at most 100).
        """
        return await mail.search_messages(query, max_results)

    @server.tool(annotations=READ)
    @anticipated
    async def outlook_mail_query_messages(
        filter: str | None = None,
        select: str | None = None,
        orderby: str | None = None,
        expand: str | None = None,
        top: int | None = None,
        skip: int | None = None,
        count: bool = False,
        folder: str | None = None,
        max_chars: int | None = None,
    ) -> QueryResult:
        """Query messages with OData v4 options, for what the list and search tools cannot do.

        The options go to the Outlook REST API as written. Property names are
        PascalCase (Subject, From, ReceivedDateTime, InternetMessageId, IsRead,
        HasAttachments, Importance, Categories, ConversationId), as results name
        them; Graph-style camelCase is accepted too. String values go in single
        quotes, with a quote inside doubled. Examples:

        - One message by its Message-ID header:
          filter="InternetMessageId eq '<abc123@mail.example.com>'"
        - From one sender since a date: filter="From/EmailAddress/Address eq
          'pat@example.mil' and ReceivedDateTime ge 2026-11-01T00:00:00Z"
        - Subject starts with: filter="startswith(Subject, 'Invoice')"
        - How many unread in the inbox: folder="inbox", filter="IsRead eq false",
          count=true, top=1
        - Internet headers for a whole page of messages in one request:
          select="Subject,InternetMessageHeaders", top=25

        Rules the API enforces: with both filter and orderby, every orderby
        property must also appear in the filter, first and in the same order (an
        "InefficientFilter" error means this). Header contents
        (InternetMessageHeaders) cannot be filtered on: to find mailing-list mail
        (List-Unsubscribe) or failed sender checks (Authentication-Results), page
        through messages with the headers selected, narrowing first with any
        filter you can (a folder, a date, a sender), and read them. Headers are
        long, so keep top small. For free-text search use
        outlook_mail_search_messages instead.

        Args:
            filter: OData $filter expression.
            select: Comma-separated properties to return; leave out for a compact set
                (no body, no headers). Add Body or UniqueBody to read bodies.
            orderby: OData $orderby, such as "ReceivedDateTime desc".
            expand: OData $expand, such as "Attachments($select=Name,Size)".
            top: Rows per page (default from config, at most 100).
            skip: Rows to skip, for the next page: pass next_skip from the last result.
            count: True to have the API report the total number of matches.
            folder: Folder id or well-known name (inbox, junkemail, sentitems,
                deleteditems, archive, drafts); leave out to search every folder.
            max_chars: Cut any long text value (a body, a header) here (default 20000,
                at most 500000).
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
        return await mail.query_messages(query, folder, max_chars)

    @server.tool(annotations=READ)
    @anticipated
    async def outlook_mail_get_message(message_id: str, max_chars: int | None = None) -> Message:
        """Read one message: its recipients and its body as plain text.

        Args:
            message_id: Message id, from outlook_mail_list_messages or _search_messages.
            max_chars: Cut the body here (default 20000, at most 500000).
        """
        return await mail.get_message(message_id, max_chars)

    @server.tool(annotations=READ)
    @anticipated
    async def outlook_mail_get_message_headers(message_id: str) -> MessageHeaders:
        """Read a message's internet (MIME) headers, for triage and automation.

        Use this to tell genuine mail from spam, phishing or a mailing list:
        the headers carry Authentication-Results (SPF, DKIM and DMARC pass or
        fail), the Received relay chain, Return-Path and Reply-To, and List-Id /
        List-Unsubscribe when it came from a list. They are not part of a normal
        message read, so this is a separate call.

        Args:
            message_id: Message id, from outlook_mail_list_messages or _search_messages.
        """
        return await mail.get_message_headers(message_id)


async def doctor(mail: OutlookMailClient) -> AsyncIterator[str]:
    """Check that the Outlook token reads the mailbox account.

    Args:
        mail: The mail client.

    Yields:
        One line per check that passed.

    Raises:
        AgentToolsError: When a check fails.
    """
    me = await mail.me()
    who = text(me, "DisplayName") or text(me, "EmailAddress")
    yield f"authenticated to {mail.base_url} as {who} (mail)"


PRODUCT: Product[OutlookSettings, OutlookMailClient] = Product(
    name="outlook_mail",
    title="Outlook Mail",
    instructions=INSTRUCTIONS,
    settings_type=OutlookSettings,
    adapter=OutlookClient.from_connection,
    parse_settings=lambda raw: parse_outlook_settings(raw, "outlook_mail"),
    make_client=OutlookMailClient,
    register_tools=register_mail_tools,
    doctor=doctor,
    token_name="microsoft",
    resolve_credential=microsoft_token,
)
