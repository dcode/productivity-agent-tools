"""Measure what the server's tools cost the model on every turn.

An agent host sends each tool's name, description and input schema to the model
with every request, plus the server's instructions. Some hosts also send each
tool's output schema. This module builds the server for a set of products, with
nothing connected, and measures those parts per product, so that growth shows up
in review (see ``tests/test_budget.py``) and on the command line (``budget``).

Sizes are bytes of compact JSON. Tokens are estimated as bytes divided by
``BYTES_PER_TOKEN``, which is close for English text and JSON schemas; a model's
own tokenizer will differ a little.
"""

import contextlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from mcp_types import Tool
from pydantic import JsonValue

from productivity_agent_tools.api import WriteOutcome
from productivity_agent_tools.config import Settings
from productivity_agent_tools.errors import AgentToolsError
from productivity_agent_tools.products import (
    Integration,
    LocalProduct,
    Product,
    installed_integrations,
    installed_products,
)
from productivity_agent_tools.server import Connected, Opened, build_server
from productivity_agent_tools.toolsets import CORE, NO_OWNER, owner_of, toolset_of

BYTES_PER_TOKEN = 4
"""Rough bytes per token, for estimates only."""

OTHER = NO_OWNER
"""Owner of a tool whose name starts with no measured product's name."""


class _Offline:
    """An ``ApiClient`` that sends nothing: tools are only listed, never called."""

    def __init__(self, base_url: str) -> None:
        """Stand in for a product's adapter.

        Args:
            base_url: The product's address, for tools that show it in a description.
        """
        self.base_url = base_url
        self.dry_run = True
        self.username: str | None = None

    def _refuse(self) -> AgentToolsError:
        """Explain why a request cannot be sent.

        Returns:
            The error to raise.
        """
        return AgentToolsError("This server only measures its tools; it sends no request.")

    async def get(
        self,
        path: str,
        params: dict[str, str | int] | None = None,
        headers: dict[str, str] | None = None,
    ) -> JsonValue:
        """Refuse a read.

        Args:
            path: Ignored.
            params: Ignored.
            headers: Ignored.

        Raises:
            AgentToolsError: Always.
        """
        raise self._refuse()

    async def get_text(self, path: str, params: dict[str, str | int] | None = None) -> str:
        """Refuse a read.

        Args:
            path: Ignored.
            params: Ignored.

        Raises:
            AgentToolsError: Always.
        """
        raise self._refuse()

    async def query(self, path: str, payload: dict[str, JsonValue]) -> JsonValue:
        """Refuse a read.

        Args:
            path: Ignored.
            payload: Ignored.

        Raises:
            AgentToolsError: Always.
        """
        raise self._refuse()

    async def write(self, method: str, path: str, payload: dict[str, JsonValue]) -> WriteOutcome:
        """Refuse a write.

        Args:
            method: Ignored.
            path: Ignored.
            payload: Ignored.

        Raises:
            AgentToolsError: Always.
        """
        raise self._refuse()

    async def aclose(self) -> None:
        """Nothing to release."""


def _size(value: object) -> int:
    """Measure a value as compact JSON.

    Args:
        value: Anything JSON can encode.

    Returns:
        Its length in bytes of UTF-8.
    """
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


@dataclass(frozen=True, slots=True)
class ToolCost:
    """What one tool costs the model.

    Attributes:
        name: The tool's name.
        definition: Bytes of its name, description and input schema, sent every turn.
        output_schema: Bytes of its output schema, sent only by hosts that forward it;
            0 when it has none.
        toolset: Its toolset within its product.
    """

    name: str
    definition: int
    output_schema: int
    toolset: str = CORE

    @classmethod
    def of(cls, tool: Tool) -> ToolCost:
        """Measure a listed tool.

        Args:
            tool: The tool as the server lists it.

        Returns:
            Its cost.
        """
        return cls(
            name=tool.name,
            definition=_size(
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.input_schema,
                }
            ),
            output_schema=_size(tool.output_schema) if tool.output_schema else 0,
            toolset=toolset_of(tool.meta),
        )


@dataclass(frozen=True, slots=True)
class ProductCost:
    """What one product's tools and instructions cost the model.

    Attributes:
        name: The product's name, or ``OTHER`` for tools no product's name prefixes.
        instructions: Bytes it adds to the server's instructions, its integrations' included.
        tools: Its tools, largest definition first.
    """

    name: str
    instructions: int
    tools: tuple[ToolCost, ...]

    @property
    def definitions(self) -> int:
        """Bytes of its tool definitions."""
        return sum(tool.definition for tool in self.tools)

    @property
    def output_schemas(self) -> int:
        """Bytes of its tools' output schemas."""
        return sum(tool.output_schema for tool in self.tools)

    @property
    def every_turn(self) -> int:
        """Bytes the model is sent on every turn: definitions and instructions."""
        return self.definitions + self.instructions


def tokens(size: int) -> int:
    """Estimate the tokens in a number of bytes.

    Args:
        size: Bytes.

    Returns:
        Estimated tokens, rounded up.
    """
    return -(-size // BYTES_PER_TOKEN)


async def measure(
    settings: Settings,
    *,
    products: Mapping[str, Product[Any, Any]] | None = None,
    integrations: Iterable[Integration] | None = None,
    all_toolsets: bool = False,
) -> list[ProductCost]:
    """Build the server for the configured products, with nothing connected, and measure it.

    Remote products get an adapter that sends nothing. Local products are opened with
    writes blocked, which for the products in this package reads nothing either.
    Products in the config that are not installed are left out, as the server does.

    Args:
        settings: Settings naming the products to measure.
        products: The known products; None uses the installed ones.
        integrations: The known integrations; None uses the installed ones.
        all_toolsets: Measure every toolset, as if no product's table chose any.

    Returns:
        One entry per product, in config order, then ``OTHER`` if any tool fit no product.
    """
    known = installed_products() if products is None else products
    if all_toolsets:
        settings = replace(
            settings,
            products={
                name: replace(item, toolsets=None) for name, item in settings.products.items()
            },
        )
    joined = tuple(installed_integrations() if integrations is None else integrations)
    names = [name for name in settings.products if name in known]
    async with contextlib.AsyncExitStack() as stack:
        connected: dict[str, Connected | Opened] = {}
        for name in names:
            product, product_settings = known[name], settings.products[name]
            if isinstance(product, LocalProduct):
                client = await stack.enter_async_context(product.open(product_settings, True))
                connected[name] = Opened(client)
            else:
                base_url = settings.connection_for(product_settings).base_url
                connected[name] = Connected(_Offline(base_url))
        server = build_server(settings, connected, products=known, integrations=joined)
        listed = await server.list_tools()
    instructions = {name: len(known[name].instructions.encode()) for name in names}
    for integration in joined:
        if all(name in instructions for name in integration.requires):
            first = integration.requires[0]
            instructions[first] += len(integration.instructions.encode())
    grouped: dict[str, list[ToolCost]] = {name: [] for name in names}
    for tool in listed:
        grouped.setdefault(owner_of(tool.name, names), []).append(ToolCost.of(tool))
    return [
        ProductCost(
            name=name,
            instructions=instructions.get(name, 0),
            tools=tuple(sorted(costs, key=lambda cost: (-cost.definition, cost.name))),
        )
        for name, costs in grouped.items()
    ]


def _row(label: str, count: int | str, every_turn: int, output: int) -> str:
    """Format one line of the report.

    Args:
        label: Product or tool name.
        count: Number of tools, or empty for a tool's own line.
        every_turn: Bytes sent every turn.
        output: Bytes of output schemas.

    Returns:
        The line.
    """
    return (
        f"{label:<44}{count!s:>6}{every_turn:>12,}{tokens(every_turn):>9,}{output:>16,}"
    ).rstrip()


def report(costs: Iterable[ProductCost], *, per_tool: bool = False) -> str:
    """Describe the costs as a table, one line per product and a total.

    Args:
        costs: What ``measure`` returned.
        per_tool: Also list each product's toolsets under it, ``core`` first, each with
            its tools, largest first.

    Returns:
        The table and a note on what is counted.
    """
    costs = list(costs)
    lines = [f"{'Product':<44}{'Tools':>6}{'Every turn':>12}{'~Tokens':>9}{'Output schemas':>16}"]
    for cost in costs:
        label = cost.name or "(no product)"
        lines.append(_row(label, len(cost.tools), cost.every_turn, cost.output_schemas))
        if per_tool:
            groups = sorted({tool.toolset for tool in cost.tools}, key=lambda g: (g != CORE, g))
            for group in groups:
                members = [tool for tool in cost.tools if tool.toolset == group]
                lines.append(
                    _row(
                        f"  {group}",
                        len(members),
                        sum(tool.definition for tool in members),
                        sum(tool.output_schema for tool in members),
                    )
                )
                lines.extend(
                    _row(f"    {tool.name}", "", tool.definition, tool.output_schema)
                    for tool in members
                )
            if cost.instructions:
                lines.append(_row("  (instructions)", "", cost.instructions, 0))
    lines.append(
        _row(
            "Total",
            sum(len(cost.tools) for cost in costs),
            sum(cost.every_turn for cost in costs),
            sum(cost.output_schemas for cost in costs),
        )
    )
    lines.append("")
    lines.append(
        "Sizes are bytes of JSON. Every turn: tool names, descriptions and input schemas,\n"
        f"plus the server's instructions. ~Tokens is bytes / {BYTES_PER_TOKEN}.\n"
        "Output schemas reach the model only if the agent forwards them. Goose does not,\n"
        "except when code mode's get_function_details asks for a tool by name."
    )
    return "\n".join(lines)
