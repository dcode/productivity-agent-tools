"""Assembles the MCP server from the configured product modules."""

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver import MCPServer

from productivity_agent_tools.api import ApiClient
from productivity_agent_tools.config import Settings
from productivity_agent_tools.products import (
    Integration,
    Product,
    ToolContext,
    installed_integrations,
    installed_products,
)


@dataclass(frozen=True, slots=True)
class Connected:
    """One product's open connection.

    Attributes:
        api: API adapter connected to the product's server.
        token_source: Description of where the access token came from.
        describe_source: Describes the credential as it is now, when it can change while
            the server runs; None otherwise.
    """

    api: ApiClient
    token_source: str = ""
    describe_source: Callable[[], Awaitable[str]] | None = None


def build_server(
    settings: Settings,
    connected: Mapping[str, Connected],
    *,
    products: Mapping[str, Product[Any, Any]] | None = None,
    integrations: Iterable[Integration] | None = None,
) -> MCPServer:
    """Create the MCP server with the tools of every configured, connected product.

    A product's tools are registered only when its table is in the config and
    a connection for it is given, so an agent is never offered a tool that
    cannot work. An integration's tools are registered only when every product
    it requires is.

    Args:
        settings: Resolved settings.
        connected: Open connection of each product to offer, by product name.
        products: The known products; None uses the installed ones.
        integrations: The known integrations; None uses the installed ones.

    Returns:
        The MCP server, ready to run.
    """
    known = installed_products() if products is None else products
    active = {
        name: known[name].activate(connected[name].api, product_settings)
        for name, product_settings in settings.products.items()
        if name in connected and name in known
    }
    joined = [
        integration
        for integration in (installed_integrations() if integrations is None else integrations)
        if all(name in active for name in integration.requires)
    ]
    instructions = " ".join(
        [item.product.instructions for item in active.values()]
        + [integration.instructions for integration in joined]
    )
    server = MCPServer("productivity-agent-tools", instructions=instructions)
    clients = {name: item.client for name, item in active.items()}
    for name, item in active.items():
        connection = settings.connection_for(settings.products[name])
        context = ToolContext(
            dry_run=settings.dry_run,
            token_source=connected[name].token_source,
            describe_source=connected[name].describe_source,
            cookie_names=tuple(connection.cookies),
            peers={peer: client for peer, client in clients.items() if peer != name},
        )
        item.register_tools(server, context)
    for integration in joined:
        integration.register_tools(server, clients)
    return server
