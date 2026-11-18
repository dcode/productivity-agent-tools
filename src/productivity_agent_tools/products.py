"""The contract a product module fulfils, and the lookup that finds the installed ones.

A *product* is one service the server can talk to, such as Jira or Bitbucket.
Each is a package that exposes a ``Product`` object and names it under the
``productivity_agent_tools.products`` entry-point group in its
``pyproject.toml``. The shared code (configuration, server assembly, the CLI)
only ever goes through these objects, so adding a product never means editing
it. A product may live in this repository or in a separately installed package.

An *integration* adds tools that need two or more products at once, such as
going from a Jira issue to its Bitbucket pull requests. It names the products
it requires and is registered only when all of them are on. Integrations are
found the same way, under ``productivity_agent_tools.integrations``.

Products never import each other. When one can use another's data without
needing it (Bitbucket showing Jira summaries, for example), it asks its
``ToolContext`` for a peer offering a capability protocol from
``capabilities``, whichever product provides it.
"""

import functools
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any

from mcp.server.mcpserver import MCPServer

from productivity_agent_tools.api import Adapter, ApiClient
from productivity_agent_tools.config import Connection, ProductSettings
from productivity_agent_tools.credentials import CredentialResolver, personal_access_token

PRODUCT_GROUP: str = "productivity_agent_tools.products"
INTEGRATION_GROUP: str = "productivity_agent_tools.integrations"


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolContext:
    """What a product's tools may know about the run they are registered in.

    Attributes:
        dry_run: When True, write tools describe the request but send nothing.
        token_source: Description of where the product's access token came from, as it
            was when the server started.
        cookie_names: Names of the extra cookies sent to the product.
        peers: The clients of the other products that are on, by product name.
        describe_source: Describes the credential as it is now, for credentials a new
            sign-in can replace while the server runs; None when ``token_source`` stays true.
    """

    dry_run: bool
    token_source: str
    cookie_names: tuple[str, ...] = ()
    peers: Mapping[str, object] = field(default_factory=dict)
    describe_source: Callable[[], Awaitable[str]] | None = None

    async def current_source(self) -> str:
        """Describe where the credential comes from now, for a status tool.

        Returns:
            The live description when the credential offers one, else ``token_source``.
        """
        if self.describe_source is None:
            return self.token_source
        return await self.describe_source()

    def peer[T](self, kind: type[T]) -> T | None:
        """Find another product's client that offers a capability.

        Args:
            kind: The capability, usually a runtime-checkable protocol from ``capabilities``.

        Returns:
            The first peer client that is an instance of ``kind``, or None.
        """
        return next((client for client in self.peers.values() if isinstance(client, kind)), None)


@dataclass(frozen=True, slots=True)
class ActiveProduct:
    """A product bound to a connected client, with the client's type erased.

    Attributes:
        product: The product.
        client: The product's own client object.
        register_tools: Adds the product's tools to a server.
        doctor: Runs the product's connectivity checks.
    """

    product: Product[Any, Any]
    client: object
    register_tools: Callable[[MCPServer, ToolContext], None]
    doctor: Callable[[], AsyncIterator[str]]


@dataclass(frozen=True, slots=True, kw_only=True)
class Product[S: ProductSettings, C]:
    """One product module: how to configure it, connect it, and offer its tools.

    ``S`` is the product's settings class and ``C`` its client class.

    Attributes:
        name: The product's config table name, also the prefix of its tool names.
        title: The product's name as people write it, such as ``"Jira"``.
        instructions: Text added to the MCP server's instructions when the product is on.
        settings_type: The product's settings class.
        adapter: Opens the API adapter for the product's kind of server, such as
            ``AtlassianClient.from_connection``.
        parse_settings: Builds the settings from the product's config table.
        make_client: Wraps a connected API adapter in the product's client.
        register_tools: Adds the product's tools to a server.
        doctor: Runs read-only checks against the server, yielding a line per check that
            passed. A failure is raised as an ``AgentToolsError``.
        token_name: Name its access token is stored under; defaults to ``name``. Products
            served by one host with one token (two GitLab modules, say) share a name.
        resolve_credential: Finds the product's credential under that name when it is
            connected. Defaults to a personal access token; a product that signs in
            another way names its own.
        login: Signs the user in interactively and stores the result, returning a sentence
            to show them. None for a product whose credential is a token the user pastes.
    """

    name: str
    title: str
    instructions: str
    settings_type: type[S]
    adapter: Adapter
    parse_settings: Callable[[dict[str, Any]], S]
    make_client: Callable[[ApiClient, S], C]
    register_tools: Callable[[MCPServer, C, ToolContext], None]
    doctor: Callable[[C], AsyncIterator[str]]
    token_name: str | None = None
    resolve_credential: CredentialResolver = personal_access_token
    login: Callable[[Connection, S], Awaitable[str]] | None = None

    @property
    def token(self) -> str:
        """Name the product's access token is stored under."""
        return self.token_name or self.name

    def activate(self, api: ApiClient, settings: ProductSettings) -> ActiveProduct:
        """Bind the product to a connected API adapter and its settings.

        Args:
            api: The adapter connected to the product's server.
            settings: The product's settings, as its ``parse_settings`` built them.

        Returns:
            The product with its client.

        Raises:
            TypeError: If the settings belong to another product.
        """
        if not isinstance(settings, self.settings_type):
            raise TypeError(f"{self.name} was given {type(settings).__name__} settings.")
        client = self.make_client(api, settings)
        register = self.register_tools
        doctor = self.doctor
        return ActiveProduct(
            product=self,
            client=client,
            register_tools=lambda server, context: register(server, client, context),
            doctor=lambda: doctor(client),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Integration:
    """Tools that need several products at once.

    Attributes:
        name: Identifier, used only in messages.
        requires: Names of the products that must all be on.
        instructions: Text added to the MCP server's instructions when it is on.
        register_tools: Adds the tools to a server, given the clients by product name.
    """

    name: str
    requires: tuple[str, ...]
    instructions: str
    register_tools: Callable[[MCPServer, Mapping[str, object]], None]


def client_of[T](clients: Mapping[str, object], name: str, kind: type[T]) -> T:
    """Fetch one product's client for an integration, checking its type.

    Args:
        clients: The clients of the products that are on, by product name.
        name: The product wanted.
        kind: The client class that product uses.

    Returns:
        The client.

    Raises:
        TypeError: If the product is off or its client is not a ``kind``.
    """
    client = clients.get(name)
    if not isinstance(client, kind):
        raise TypeError(f"Product '{name}' has no {kind.__name__}.")
    return client


def _load[T](group: str, kind: type[T]) -> dict[str, T]:
    """Load every object named under an entry-point group.

    Args:
        group: The entry-point group.
        kind: The type each object must have.

    Returns:
        The objects by entry-point name, in name order.

    Raises:
        TypeError: If an entry point names something else.
    """
    found: dict[str, T] = {}
    for point in sorted(entry_points(group=group), key=lambda p: p.name):
        loaded = point.load()
        if not isinstance(loaded, kind):
            raise TypeError(f"Entry point '{point.name}' in {group} is not a {kind.__name__}.")
        found[point.name] = loaded
    return found


@functools.cache
def installed_products() -> Mapping[str, Product[Any, Any]]:
    """Find the installed products.

    Returns:
        The products by name.

    Raises:
        TypeError: If an entry point is not a ``Product`` or its name differs from the product's.
    """
    products = _load(PRODUCT_GROUP, Product)
    for name, product in products.items():
        if product.name != name:
            raise TypeError(f"Entry point '{name}' names the product '{product.name}'.")
    return products


@functools.cache
def installed_integrations() -> tuple[Integration, ...]:
    """Find the installed integrations.

    Returns:
        The integrations, in entry-point name order.
    """
    return tuple(_load(INTEGRATION_GROUP, Integration).values())
