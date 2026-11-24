"""The contract a product module fulfils, and the lookup that finds the installed ones.

A *product* is one service the server can talk to, such as Jira or Bitbucket.
Each is a package that exposes a ``Product`` object and names it under the
``productivity_agent_tools.products`` entry-point group in its
``pyproject.toml``. The shared code (configuration, server assembly, the CLI)
only ever goes through these objects, so adding a product never means editing
it. A product may live in this repository or in a separately installed package.

Products come in two kinds. A ``RemoteProduct`` is served by an API: it has a
connection (``base_url`` and the rest), a credential and an adapter. A
``LocalProduct`` works on local data, such as a folder of notes: it opens its
client straight from its settings, with no URL or credential. ``Product`` is
either.

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
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any

from mcp.server.mcpserver import MCPServer

from productivity_agent_tools.api import Adapter, ApiClient
from productivity_agent_tools.config import Connection, LocalProductSettings, ProductSettings
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

    def peers_of[T](self, kind: type[T]) -> list[T]:
        """Find every other product's client that offers a capability.

        Args:
            kind: The capability, usually a runtime-checkable protocol from ``capabilities``.

        Returns:
            The peer clients that are instances of ``kind``, in product order.
        """
        return [client for client in self.peers.values() if isinstance(client, kind)]


@dataclass(frozen=True, slots=True)
class ActiveProduct:
    """A product bound to a connected client, with the client's type erased.

    Attributes:
        product: The product.
        client: The product's own client object.
        register_tools: Adds the product's tools to a server.
        doctor: Runs the product's connectivity checks.
    """

    product: _ProductBase[Any, Any]
    client: object
    register_tools: Callable[[MCPServer, ToolContext], None]
    doctor: Callable[[], AsyncIterator[str]]


@dataclass(frozen=True, slots=True, kw_only=True)
class _ProductBase[S: ProductSettings, C]:
    """What every kind of product has: its config table, its tools and its checks.

    ``S`` is the product's settings class and ``C`` its client class.

    Attributes:
        name: The product's config table name, also the prefix of its tool names.
        title: The product's name as people write it, such as ``"Jira"``.
        instructions: Text added to the MCP server's instructions when the product is on.
        settings_type: The product's settings class.
        parse_settings: Builds the settings from the product's config table.
        register_tools: Adds the product's tools to a server.
        doctor: Runs read-only checks, yielding a line per check that passed. A failure is
            raised as an ``AgentToolsError``.
        toolsets: Its toolsets beyond ``core``, each with a sentence saying what its tools
            are for. A tool names its toolset with ``meta=toolset(name)``.
    """

    name: str
    title: str
    instructions: str
    settings_type: type[S]
    parse_settings: Callable[[dict[str, Any]], S]
    register_tools: Callable[[MCPServer, C, ToolContext], None]
    doctor: Callable[[C], AsyncIterator[str]]
    toolsets: Mapping[str, str] = field(default_factory=dict)

    def _bind(self, client: C, settings: ProductSettings) -> ActiveProduct:
        """Pair the product with its client, once the settings are known to be its own.

        Args:
            client: The product's client.
            settings: The product's settings.

        Returns:
            The product with its client.

        Raises:
            TypeError: If the settings belong to another product.
        """
        if not isinstance(settings, self.settings_type):
            raise TypeError(f"{self.name} was given {type(settings).__name__} settings.")
        register = self.register_tools
        doctor = self.doctor
        return ActiveProduct(
            product=self,
            client=client,
            register_tools=lambda server, context: register(server, client, context),
            doctor=lambda: doctor(client),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class RemoteProduct[S: ProductSettings, C](_ProductBase[S, C]):
    """A product served by a remote API: a connection, a credential and an adapter.

    Attributes:
        adapter: Opens the API adapter for the product's kind of server, such as
            ``AtlassianClient.from_connection``.
        make_client: Wraps a connected API adapter in the product's client.
        token_name: Name its access token is stored under; defaults to ``name``. Products
            served by one host with one token (two GitLab modules, say) share a name.
        resolve_credential: Finds the product's credential under that name when it is
            connected. Defaults to a personal access token; a product that signs in
            another way names its own.
        login: Signs the user in interactively and stores the result, returning a sentence
            to show them. None for a product whose credential is a token the user pastes.
    """

    adapter: Adapter
    make_client: Callable[[ApiClient, S], C]
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
        return self._bind(self.make_client(api, settings), settings)


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalProduct[S: LocalProductSettings, C](_ProductBase[S, C]):
    """A product that works on local data, such as a folder of notes: no server, no credential.

    Attributes:
        open: Opens the product's client from its settings and whether writes are blocked;
            the client is closed when the block exits. Fails with an ``AgentToolsError``
            when the local data cannot be used.
    """

    open: Callable[[S, bool], AbstractAsyncContextManager[C]]

    def activate(self, client: C, settings: ProductSettings) -> ActiveProduct:
        """Bind the product to its opened client and its settings.

        Args:
            client: The client ``open`` gave.
            settings: The product's settings, as its ``parse_settings`` built them.

        Returns:
            The product with its client.

        Raises:
            TypeError: If the settings belong to another product.
        """
        return self._bind(client, settings)


type Product[S: ProductSettings, C] = RemoteProduct[S, C] | LocalProduct[Any, C]
"""Any kind of product."""

_PRODUCT_KINDS: tuple[type, ...] = (RemoteProduct, LocalProduct)


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


def _load[T](group: str, kind: type[T] | tuple[type, ...]) -> dict[str, T]:
    """Load every object named under an entry-point group.

    Args:
        group: The entry-point group.
        kind: The type each object must have, or a tuple of types it may have.

    Returns:
        The objects by entry-point name, in name order.

    Raises:
        TypeError: If an entry point names something else.
    """
    label = kind.__name__ if isinstance(kind, type) else " or ".join(k.__name__ for k in kind)
    found: dict[str, T] = {}
    for point in sorted(entry_points(group=group), key=lambda p: p.name):
        loaded = point.load()
        if not isinstance(loaded, kind):
            raise TypeError(f"Entry point '{point.name}' in {group} is not a {label}.")
        found[point.name] = loaded
    return found


@functools.cache
def installed_products() -> Mapping[str, Product[Any, Any]]:
    """Find the installed products.

    Returns:
        The products by name.

    Raises:
        TypeError: If an entry point is not a product or its name differs from the product's.
    """
    products: dict[str, Product[Any, Any]] = _load(PRODUCT_GROUP, _PRODUCT_KINDS)
    for name, product in products.items():
        if product.name != name:
            raise TypeError(f"Entry point '{name}' names the product '{product.name}'.")
    return products


def remote_products() -> Mapping[str, RemoteProduct[Any, Any]]:
    """Find the installed products that reach a server, the ones with a credential.

    Returns:
        The remote products by name.
    """
    return {
        name: product
        for name, product in installed_products().items()
        if isinstance(product, RemoteProduct)
    }


@functools.cache
def installed_integrations() -> tuple[Integration, ...]:
    """Find the installed integrations.

    Returns:
        The integrations, in entry-point name order.
    """
    return tuple(_load(INTEGRATION_GROUP, Integration).values())
