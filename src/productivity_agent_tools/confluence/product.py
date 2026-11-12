"""The Confluence product, as the server, configuration and CLI find it."""

from collections.abc import AsyncIterator

from productivity_agent_tools.confluence.client import ConfluenceClient
from productivity_agent_tools.confluence.settings import (
    ConfluenceSettings,
    parse_confluence_settings,
)
from productivity_agent_tools.confluence.tools import register_confluence_tools
from productivity_agent_tools.http import AtlassianClient
from productivity_agent_tools.payload import text
from productivity_agent_tools.products import Product

INSTRUCTIONS: str = (
    "The confluence_ tools work with the user's self-hosted Confluence. Reads are safe to "
    "call freely. confluence_create_page and confluence_update_page change pages others "
    "can see: confirm the space, title and content with the user before calling them, and "
    "treat a DRY RUN result as nothing changed. Write page bodies in Markdown unless "
    "Confluence macros are needed. Before editing, read the page with confluence_get_page "
    "and pass its version; to add a section, use mode=append rather than resending the "
    "whole page. The user's own pages go in the personal_space from confluence_status."
)


async def doctor(confluence: ConfluenceClient) -> AsyncIterator[str]:
    """Check login, the server version and search.

    Args:
        confluence: The Confluence client.

    Yields:
        One line per check that passed.

    Raises:
        AgentToolsError: When a check fails.
    """
    profile = await confluence.myself()
    yield f"authenticated to {confluence.base_url} as {text(profile, 'displayName')}"
    yield f"Confluence version {await confluence.server_version()}"
    found = await confluence.search("type = page AND contributor = currentUser()", 5)
    more = "+" if found.more_available else ""
    yield f"search works ({found.returned}{more} page(s) you have edited)"


PRODUCT: Product[ConfluenceSettings, ConfluenceClient] = Product(
    name="confluence",
    title="Confluence",
    instructions=INSTRUCTIONS,
    settings_type=ConfluenceSettings,
    adapter=AtlassianClient.from_connection,
    parse_settings=parse_confluence_settings,
    make_client=ConfluenceClient,
    register_tools=register_confluence_tools,
    doctor=doctor,
)
