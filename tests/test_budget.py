"""What the tools cost the model, and the budget each product's tools must stay within."""

from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer

from productivity_agent_tools import budget, cli
from productivity_agent_tools.api import ApiClient
from productivity_agent_tools.budget import OTHER, ProductCost, ToolCost
from productivity_agent_tools.config import (
    LocalProductSettings,
    ProductSettings,
    Settings,
    parse_settings,
)
from productivity_agent_tools.errors import AgentToolsError
from productivity_agent_tools.http import AtlassianClient
from productivity_agent_tools.products import (
    Integration,
    LocalProduct,
    Product,
    RemoteProduct,
    ToolContext,
    installed_products,
)

# Bytes each product may send the model on every turn: its tool names, descriptions and
# input schemas, plus its server instructions (its integrations' included). Raise a
# number only on purpose, in the same commit as the growth, and say why in the message.
# Measured 2026-11-24 (0.12.0) and rounded up to the next thousand.
BUDGETS: dict[str, int] = {
    "jira": 19_000,
    "bitbucket": 22_000,
    "confluence": 10_000,
    "servicenow": 10_000,
    "outlook_calendar": 6_000,
    "outlook_mail": 7_000,
    "obsidian": 12_000,
}
# Output schemas reach the model only through hosts that forward them; watched as a whole.
OUTPUT_SCHEMA_BUDGET = 153_000


def _own_products() -> dict[str, Product[Any, Any]]:
    """The products this package ships, leaving out plugins installed beside it."""
    return {
        name: product
        for name, product in installed_products().items()
        if product.register_tools.__module__.startswith("productivity_agent_tools.")
    }


def _everything(vault: Path) -> Settings:
    """Settings with every product this package ships switched on."""
    raw: dict[str, Any] = {"base_url": "https://host.example"}
    raw |= {name: {} for name in _own_products()}
    raw["obsidian"] = {"vault": str(vault)}
    return parse_settings(raw)


@pytest.fixture
async def costs(tmp_path: Path) -> list[ProductCost]:
    return await budget.measure(_everything(tmp_path))


def test_every_product_has_a_budget() -> None:
    assert set(BUDGETS) == set(_own_products())


async def test_each_product_stays_within_its_budget(costs: list[ProductCost]) -> None:
    over = [
        f"{cost.name}: {cost.every_turn:,} bytes, budget {BUDGETS[cost.name]:,}; largest: "
        + ", ".join(f"{tool.name} {tool.definition:,}" for tool in cost.tools[:3])
        for cost in costs
        if cost.every_turn > BUDGETS[cost.name]
    ]
    assert not over, "Tool definitions grew past their budget:\n" + "\n".join(over)


async def test_output_schemas_stay_within_their_budget(costs: list[ProductCost]) -> None:
    assert sum(cost.output_schemas for cost in costs) <= OUTPUT_SCHEMA_BUDGET


async def test_every_tool_is_owned_by_a_product(costs: list[ProductCost]) -> None:
    assert [cost.name for cost in costs] == list(_own_products())
    assert all(cost.tools and cost.instructions for cost in costs)


async def test_the_jira_and_bitbucket_integration_counts_toward_jira(
    costs: list[ProductCost],
) -> None:
    jira = next(cost for cost in costs if cost.name == "jira")
    assert "jira_get_issue_pull_requests" in {tool.name for tool in jira.tools}
    assert jira.instructions > len(_own_products()["jira"].instructions)


async def test_every_toolset_a_product_declares_has_tools(costs: list[ProductCost]) -> None:
    products = _own_products()
    for cost in costs:
        assert {tool.toolset for tool in cost.tools} == {"core", *products[cost.name].toolsets}


async def test_tools_are_listed_largest_first(costs: list[ProductCost]) -> None:
    for cost in costs:
        sizes = [tool.definition for tool in cost.tools]
        assert sizes == sorted(sizes, reverse=True)


@pytest.mark.parametrize(("size", "expected"), [(0, 0), (1, 1), (4, 1), (5, 2)])
def test_tokens_round_up(size: int, expected: int) -> None:
    assert budget.tokens(size) == expected


async def _no_checks(client: object) -> AsyncIterator[str]:
    for line in ():
        yield line


def _register_wiki(server: MCPServer, client: ApiClient, context: ToolContext) -> None:
    @server.tool()
    async def wiki_ping(where: str) -> str:
        """Say where the wiki is."""
        return await client.get_text(where)

    @server.tool()
    async def stray() -> str:
        """A tool named after no product."""
        return ""


def _register_notes(server: MCPServer, client: object, context: ToolContext) -> None:
    @server.tool()
    async def notes_read() -> str:
        """Read the notes."""
        return ""


@asynccontextmanager
async def _open_notes(settings: LocalProductSettings, dry_run: bool) -> AsyncGenerator[str]:
    assert dry_run
    yield "notes"


WIKI: Product[ProductSettings, ApiClient] = RemoteProduct(
    name="wiki",
    title="Wiki",
    instructions="Use the wiki_ tools.",
    settings_type=ProductSettings,
    adapter=AtlassianClient.from_connection,
    parse_settings=lambda raw: ProductSettings(),
    make_client=lambda api, settings: api,
    register_tools=_register_wiki,
    doctor=_no_checks,
)
NOTES: Product[LocalProductSettings, str] = LocalProduct(
    name="notes",
    title="Notes",
    instructions="",
    settings_type=LocalProductSettings,
    parse_settings=lambda raw: LocalProductSettings(),
    open=_open_notes,
    register_tools=_register_notes,
    doctor=_no_checks,
)


async def test_measure_counts_integrations_and_strays_and_skips_unknown_products() -> None:
    settings = parse_settings(
        {"base_url": "https://h.example", "wiki": {}, "notes": {}, "gone": {}},
        parsers={
            "wiki": WIKI.parse_settings,
            "notes": NOTES.parse_settings,
            "gone": lambda raw: ProductSettings(),
        },
    )
    joined = [
        Integration(
            name="both",
            requires=("wiki", "notes"),
            instructions="Both.",
            register_tools=lambda server, clients: None,
        ),
        Integration(
            name="never",
            requires=("wiki", "gone"),
            instructions="Never.",
            register_tools=lambda server, clients: None,
        ),
    ]
    costs = await budget.measure(
        settings, products={"wiki": WIKI, "notes": NOTES}, integrations=joined
    )
    by_name = {cost.name: cost for cost in costs}
    assert list(by_name) == ["wiki", "notes", OTHER]
    assert by_name["wiki"].instructions == len("Use the wiki_ tools.Both.")
    assert [tool.name for tool in by_name[OTHER].tools] == ["stray"]
    assert by_name[OTHER].instructions == 0
    assert by_name["notes"].output_schemas == by_name["notes"].tools[0].output_schema > 0


def _offline_calls(api: ApiClient) -> Iterator[Callable[[], Any]]:
    yield lambda: api.get("/x")
    yield lambda: api.get_text("/x")
    yield lambda: api.query("/x", {})
    yield lambda: api.write("POST", "/x", {})


async def test_the_offline_adapter_sends_nothing() -> None:
    api: ApiClient = budget._Offline("https://h.example")
    assert (api.base_url, api.dry_run, api.username) == ("https://h.example", True, None)
    for call in _offline_calls(api):
        with pytest.raises(AgentToolsError, match="sends no request"):
            await call()
    await api.aclose()


def test_a_tool_without_an_output_schema_costs_nothing_for_it() -> None:
    from mcp_types import Tool

    tool = Tool(name="t", description="é", input_schema={"type": "object"})
    cost = ToolCost.of(tool)
    assert cost.output_schema == 0
    assert cost.definition == len(
        '{"name":"t","description":"é","input_schema":{"type":"object"}}'.encode()
    )


def test_report_lists_products_tools_and_a_total() -> None:
    costs = [
        ProductCost(
            "jira", 100, (ToolCost("jira_big", 3000, 40), ToolCost("jira_small", 20, 0, "edit"))
        ),
        ProductCost(OTHER, 0, (ToolCost("stray", 8, 0),)),
    ]
    short = budget.report(costs)
    assert "jira_big" not in short
    assert "(no product)" in short
    total = next(line for line in short.splitlines() if line.startswith("Total"))
    assert total.split() == ["Total", "3", "3,128", "782", "40"]
    full = budget.report(costs, per_tool=True).splitlines()
    assert full[1].split() == ["jira", "2", "3,120", "780", "40"]
    assert [line.split() for line in full[2:10]] == [
        ["core", "1", "3,000", "750", "40"],
        ["jira_big", "3,000", "750", "40"],
        ["edit", "1", "20", "5", "0"],
        ["jira_small", "20", "5", "0"],
        ["(instructions)", "100", "25", "0"],
        ["(no", "product)", "1", "8", "2", "0"],
        ["core", "1", "8", "2", "0"],
        ["stray", "8", "2", "0"],
    ]
    assert full[3].startswith("    jira_big")


def test_budget_command_prints_the_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    config.write_text('base_url = "https://h.example"\n[confluence]\n')
    cli.budget(config=config, tools=True)
    out = capsys.readouterr().out
    assert out.startswith("Product")
    assert "    confluence_search" in out
    assert "  write " in out


def test_budget_command_measures_left_out_toolsets_when_asked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    config.write_text('base_url = "https://h.example"\n[confluence]\ntoolsets = ["core"]\n')
    cli.budget(config=config, tools=True)
    assert "confluence_update_page" not in capsys.readouterr().out
    cli.budget(config=config, tools=True, all_toolsets=True)
    assert "confluence_update_page" in capsys.readouterr().out


def test_budget_command_needs_a_product(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "config.toml"
    config.write_text('base_url = "https://h.example"\n')
    with pytest.raises(SystemExit) as info:
        cli.budget(config=config)
    assert info.value.code == 1
    assert "No product is configured" in capsys.readouterr().err
