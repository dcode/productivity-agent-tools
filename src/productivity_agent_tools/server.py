"""Assembles the MCP server from the configured product modules."""

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from productivity_agent_tools.api import ApiClient
from productivity_agent_tools.config import ProductSettings, Settings
from productivity_agent_tools.products import (
    ActiveProduct,
    Integration,
    LocalProduct,
    Product,
    RemoteProduct,
    ToolContext,
    installed_integrations,
    installed_products,
)
from productivity_agent_tools.toolsets import ToolsetServer, choose


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


@dataclass(frozen=True, slots=True)
class Opened:
    """One local product's opened client.

    Attributes:
        client: The client the product's ``open`` gave.
        token_source: Always empty: a local product has no credential.
        describe_source: Always None, for the same reason.
    """

    client: object
    token_source: str = ""
    describe_source: Callable[[], Awaitable[str]] | None = None


def _activate(
    product: Product[Any, Any], opened: Connected | Opened, settings: ProductSettings
) -> ActiveProduct:
    """Bind a product to what was opened for it.

    Args:
        product: The product.
        opened: Its API connection when it is remote, its client when it is local.
        settings: The product's settings.

    Returns:
        The product with its client.

    Raises:
        TypeError: If a remote product was given a client, or a local one a connection.
    """
    match product, opened:
        case RemoteProduct(), Connected(api=api):
            return product.activate(api, settings)
        case LocalProduct(), Opened(client=client):
            return product.activate(client, settings)
    raise TypeError(f"{product.name} cannot use a {type(opened).__name__}.")


def _off_note(active: Mapping[str, ActiveProduct], enabled: Mapping[str, frozenset[str]]) -> str:
    """Tell the agent which toolsets the config left off, so it does not look for them.

    Args:
        active: The products that are on.
        enabled: The toolsets that are on, by product name.

    Returns:
        A sentence, or empty when every toolset is on.
    """
    off = [
        f"{name}.{group} ({description})"
        for name, item in active.items()
        for group, description in item.product.toolsets.items()
        if group not in enabled[name]
    ]
    if not off:
        return ""
    return (
        "These toolsets are off in this server's config, so their tools are not offered: "
        + "; ".join(off)
        + ". If a task needs one, tell the user it can be switched on with the product's "
        "toolsets key."
    )


def build_server(
    settings: Settings,
    connected: Mapping[str, Connected | Opened],
    *,
    products: Mapping[str, Product[Any, Any]] | None = None,
    integrations: Iterable[Integration] | None = None,
) -> ToolsetServer:
    """Create the MCP server with the tools of every configured, connected product.

    A product's tools are registered only when its table is in the config and
    a connection for it is given, so an agent is never offered a tool that
    cannot work. An integration's tools are registered only when every product
    it requires is. Of those, only the tools of each product's chosen toolsets are
    listed; the rest are held on the server (see ``ToolsetServer``).

    Args:
        settings: Resolved settings.
        connected: Open connection (remote) or client (local) of each product to offer,
            by product name.
        products: The known products; None uses the installed ones.
        integrations: The known integrations; None uses the installed ones.

    Returns:
        The MCP server, ready to run.

    Raises:
        ConfigError: If a product's ``toolsets`` names a toolset it does not have.
    """
    known = installed_products() if products is None else products
    active = {
        name: _activate(known[name], connected[name], product_settings)
        for name, product_settings in settings.products.items()
        if name in connected and name in known
    }
    joined = [
        integration
        for integration in (installed_integrations() if integrations is None else integrations)
        if all(name in active for name in integration.requires)
    ]
    enabled = {
        name: choose(
            name,
            item.product.title,
            item.product.toolsets,
            settings.products[name].toolsets,
        )
        for name, item in active.items()
    }
    instructions = " ".join(
        part
        for part in [
            *(item.product.instructions for item in active.values()),
            *(integration.instructions for integration in joined),
            _off_note(active, enabled),
        ]
        if part
    )
    server = ToolsetServer(
        "productivity-agent-tools",
        instructions=instructions,
        declared={name: tuple(item.product.toolsets) for name, item in active.items()},
        enabled=enabled,
    )
    clients = {name: item.client for name, item in active.items()}
    for name, item in active.items():
        product_settings = settings.products[name]
        cookies = (
            settings.connection_for(product_settings).cookies if product_settings.remote else {}
        )
        context = ToolContext(
            dry_run=settings.dry_run_for(product_settings),
            token_source=connected[name].token_source,
            describe_source=connected[name].describe_source,
            cookie_names=tuple(cookies),
            peers={peer: client for peer, client in clients.items() if peer != name},
        )
        item.register_tools(server, context)
    for integration in joined:
        integration.register_tools(server, clients)
    return server
