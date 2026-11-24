"""Toolsets: named groups of a product's tools that the config switches on or off.

Every tool belongs to one toolset of the product whose name starts its own. A tool
names its toolset in its metadata, ``@server.tool(meta=toolset("worklog"))``; a tool
that names none is in ``core``. A product lists the toolsets beyond ``core`` it has,
with a sentence each, in ``Product.toolsets``.

A product's table may choose its toolsets, ``toolsets = ["core", "worklog"]``; without
the key every toolset is on. ``ToolsetServer`` keeps the tools of toolsets that are
off out of the tool list and holds them, so they can be added later without
registering the product again.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import Icon, ToolAnnotations

from productivity_agent_tools.errors import ConfigError

TOOLSET_KEY = "productivity-agent-tools/toolset"
"""Key of a tool's metadata that names its toolset."""

CORE = "core"
"""The toolset of every tool that names none."""

NO_OWNER = ""
"""Owner of a tool whose name starts with no product's name."""


def toolset(name: str) -> dict[str, Any]:
    """Tag a tool with its toolset, as ``@server.tool(meta=toolset("worklog"))``.

    Args:
        name: The toolset's name within its product, such as ``"worklog"``.

    Returns:
        The tool's metadata.
    """
    return {TOOLSET_KEY: name}


def toolset_of(meta: Mapping[str, Any] | None) -> str:
    """Read the toolset a tool's metadata names.

    Args:
        meta: The tool's metadata, or None.

    Returns:
        The toolset's name within its product; ``CORE`` when none is named.
    """
    return str((meta or {}).get(TOOLSET_KEY, CORE))


def owner_of(tool: str, products: Iterable[str]) -> str:
    """Find the product a tool belongs to, by the longest product name its name starts with.

    Args:
        tool: The tool's name.
        products: Product names.

    Returns:
        The product's name, or ``NO_OWNER``.
    """
    matches = [name for name in products if tool.startswith(f"{name}_")]
    return max(matches, key=len, default=NO_OWNER)


def choose(
    product: str, title: str, declared: Mapping[str, str], chosen: Iterable[str] | None
) -> frozenset[str]:
    """Work out which of a product's toolsets are on.

    Args:
        product: The product's name, for the error message.
        title: The product's name as people write it, for the error message.
        declared: The product's toolsets beyond ``core``, with a sentence each.
        chosen: The toolsets its table names, or None when it names none.

    Returns:
        The names of the toolsets that are on.

    Raises:
        ConfigError: If the table names a toolset the product does not have.
    """
    available = (CORE, *declared)
    if chosen is None:
        return frozenset(available)
    unknown = [name for name in chosen if name not in available]
    if unknown:
        raise ConfigError(
            f"Config key '{product}.toolsets' names {', '.join(map(repr, unknown))}, which "
            f"{title} does not have. Its toolsets: {', '.join(available)}."
        )
    return frozenset(chosen)


@dataclass(frozen=True, slots=True, kw_only=True)
class HeldTool:
    """A tool whose toolset is off, kept as it was registered.

    Attributes:
        product: The product it belongs to.
        toolset: Its toolset within that product.
        fn: The tool function.
        name: The name it was registered under.
        title: Its title, if any.
        description: Its description, if given apart from the docstring.
        annotations: Its read and write hints.
        icons: Its icons, if any.
        meta: Its metadata, the toolset tag included.
        structured_output: Whether its result is structured, or None to detect it.
    """

    product: str
    toolset: str
    fn: Callable[..., Any]
    name: str
    title: str | None
    description: str | None
    annotations: ToolAnnotations | None
    icons: list[Icon] | None
    meta: dict[str, Any] | None
    structured_output: bool | None


class ToolsetServer(MCPServer):
    """An MCP server that lists only the tools of the toolsets that are on.

    Tools of other toolsets are held in ``held``, by name. A tool of no known product
    is always listed.
    """

    def __init__(
        self,
        name: str,
        *,
        instructions: str | None = None,
        declared: Mapping[str, Iterable[str]] | None = None,
        enabled: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        """Create the server.

        Args:
            name: The server's name.
            instructions: Instructions sent to the client.
            declared: Each product's toolsets beyond ``core``, by product name.
            enabled: The toolsets that are on, by product name; a product left out
                has every toolset on.
        """
        super().__init__(name, instructions=instructions)
        self._declared = {
            product: frozenset((CORE, *names)) for product, names in (declared or {}).items()
        }
        self._enabled = {product: frozenset(names) for product, names in (enabled or {}).items()}
        self.held: dict[str, HeldTool] = {}

    def add_tool(
        self,
        fn: Callable[..., Any],
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        annotations: ToolAnnotations | None = None,
        icons: list[Icon] | None = None,
        meta: dict[str, Any] | None = None,
        structured_output: bool | None = None,
    ) -> None:
        """Add a tool when its toolset is on, otherwise hold it.

        Args:
            fn: The tool function.
            name: Its name; defaults to the function's.
            title: Its title.
            description: Its description; defaults to the docstring.
            annotations: Its read and write hints.
            icons: Its icons.
            meta: Its metadata, which names its toolset.
            structured_output: Whether its result is structured, or None to detect it.

        Raises:
            ValueError: If it names a toolset its product does not declare, which is a
                mistake in the product, not in the config.
        """
        tool_name = name or getattr(fn, "__name__", "")
        product = owner_of(tool_name, self._declared)
        group = toolset_of(meta)
        if product != NO_OWNER and group not in self._declared[product]:
            raise ValueError(
                f"{tool_name} names toolset {group!r}, which {product} does not declare."
            )
        if product == NO_OWNER or group in self._enabled.get(product, self._declared[product]):
            super().add_tool(
                fn,
                name=name,
                title=title,
                description=description,
                annotations=annotations,
                icons=icons,
                meta=meta,
                structured_output=structured_output,
            )
            return
        self.held[tool_name] = HeldTool(
            product=product,
            toolset=group,
            fn=fn,
            name=tool_name,
            title=title,
            description=description,
            annotations=annotations,
            icons=icons,
            meta=meta,
            structured_output=structured_output,
        )
