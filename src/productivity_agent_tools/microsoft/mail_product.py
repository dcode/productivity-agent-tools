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
)
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
        return status(mail.base_url, await mail.me(), context.token_source, dry_run=context.dry_run)

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
