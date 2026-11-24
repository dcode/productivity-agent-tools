"""Toolsets: tagging tools, choosing them in the config, and holding the ones left off."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer

from productivity_agent_tools import budget
from productivity_agent_tools.config import ProductSettings, parse_settings
from productivity_agent_tools.errors import ConfigError
from productivity_agent_tools.http import AtlassianClient
from productivity_agent_tools.jira.product import PRODUCT as JIRA
from productivity_agent_tools.products import Product, RemoteProduct, ToolContext
from productivity_agent_tools.server import Connected, build_server
from productivity_agent_tools.toolsets import (
    CORE,
    NO_OWNER,
    TOOLSET_KEY,
    ToolsetServer,
    choose,
    owner_of,
    toolset,
    toolset_of,
)


def test_a_tag_names_the_toolset_and_no_tag_means_core() -> None:
    assert toolset("worklog") == {TOOLSET_KEY: "worklog"}
    assert toolset_of(toolset("worklog")) == "worklog"
    assert toolset_of({"other": 1}) == CORE
    assert toolset_of(None) == CORE


def test_owner_is_the_longest_matching_product_name() -> None:
    names = ["outlook", "outlook_mail", "jira"]
    assert owner_of("outlook_mail_search", names) == "outlook_mail"
    assert owner_of("outlook_status", names) == "outlook"
    assert owner_of("jirax_search", names) == NO_OWNER


def test_choose_defaults_to_every_toolset_and_checks_names() -> None:
    declared = {"edit": "Edit.", "worklog": "Log."}
    assert choose("jira", "Jira", declared, None) == {"core", "edit", "worklog"}
    assert choose("jira", "Jira", declared, ("worklog",)) == {"worklog"}
    assert choose("jira", "Jira", declared, ()) == frozenset()
    with pytest.raises(ConfigError, match=r"'jira.toolsets' names 'agile', 'x'.*core, edit"):
        choose("jira", "Jira", declared, ("agile", "core", "x"))


def _server(enabled: dict[str, tuple[str, ...]] | None = None) -> ToolsetServer:
    server = ToolsetServer(
        "test", instructions="Hi.", declared={"wiki": ("write",)}, enabled=enabled
    )

    @server.tool(meta=toolset("write"))
    async def wiki_edit(text: str) -> str:
        """Edit the wiki."""
        return text

    @server.tool()
    async def wiki_read() -> str:
        """Read the wiki."""
        return "page"

    @server.tool()
    async def stray() -> str:
        """Belongs to no product."""
        return ""

    return server


async def test_a_product_left_out_of_enabled_has_every_toolset_on() -> None:
    server = _server()
    assert server.instructions == "Hi."
    assert {tool.name for tool in await server.list_tools()} == {"wiki_edit", "wiki_read", "stray"}
    assert server.held == {}


async def test_tools_of_a_toolset_that_is_off_are_held_as_registered() -> None:
    server = _server({"wiki": ("core",)})
    assert {tool.name for tool in await server.list_tools()} == {"wiki_read", "stray"}
    held = server.held["wiki_edit"]
    assert (held.product, held.toolset, held.name) == ("wiki", "write", "wiki_edit")
    assert held.meta == toolset("write")
    assert await held.fn("x") == "x"
    server.add_tool(
        held.fn,
        name="wiki_edit_again",
        title=held.title,
        description=held.description,
        annotations=held.annotations,
        icons=held.icons,
        structured_output=held.structured_output,
    )
    assert "wiki_edit_again" in {tool.name for tool in await server.list_tools()}


def test_a_tool_naming_an_undeclared_toolset_is_a_mistake_in_the_product() -> None:
    server = ToolsetServer("test", declared={"wiki": ()})

    async def wiki_admin() -> str:
        return ""

    with pytest.raises(ValueError, match="wiki_admin names toolset 'admin'"):
        server.add_tool(wiki_admin, meta=toolset("admin"))


def test_the_config_reads_toolsets_for_any_product() -> None:
    settings = parse_settings(
        {
            "base_url": "https://h.example",
            "jira": {"toolsets": [" core", "worklog", "core"]},
            "custom": {"toolsets": []},
            "plain": {},
        },
        parsers={
            "jira": JIRA.parse_settings,
            "custom": lambda raw: ProductSettings(),
            "plain": lambda raw: ProductSettings(),
        },
    )
    assert settings.products["jira"].toolsets == ("core", "worklog")
    assert settings.products["custom"].toolsets == ()
    assert settings.products["plain"].toolsets is None


@pytest.mark.parametrize("value", ["core", ["core", 3], [""], {"core": True}])
def test_the_config_refuses_toolsets_that_are_not_a_list_of_names(value: object) -> None:
    with pytest.raises(ConfigError, match=r"'jira.toolsets' must be a list of toolset names"):
        parse_settings({"base_url": "https://h.example", "jira": {"toolsets": value}})


async def _no_checks(client: object) -> AsyncIterator[str]:
    for line in ():
        yield line


def _register(server: MCPServer, client: object, context: ToolContext) -> None:
    @server.tool(meta=toolset("write"))
    async def wiki_edit() -> str:
        """Edit the wiki."""
        return ""

    @server.tool()
    async def wiki_read() -> str:
        """Read the wiki."""
        return ""


WIKI: Product[ProductSettings, object] = RemoteProduct(
    name="wiki",
    title="Wiki",
    instructions="Use the wiki_ tools.",
    settings_type=ProductSettings,
    adapter=AtlassianClient.from_connection,
    parse_settings=lambda raw: ProductSettings(),
    make_client=lambda api, settings: api,
    register_tools=_register,
    doctor=_no_checks,
    toolsets={"write": "Edit pages.", "admin": "Manage spaces."},
)


def _wiki_server(table: dict[str, Any]) -> ToolsetServer:
    settings = parse_settings(
        {"base_url": "https://h.example", "wiki": table},
        parsers={"wiki": WIKI.parse_settings},
    )
    api = budget._Offline("https://h.example")
    return build_server(
        settings, {"wiki": Connected(api)}, products={"wiki": WIKI}, integrations=()
    )


async def test_build_server_tells_the_agent_which_toolsets_are_off() -> None:
    server = _wiki_server({"toolsets": ["core"]})
    assert [tool.name for tool in await server.list_tools()] == ["wiki_read"]
    assert set(server.held) == {"wiki_edit"}
    assert server.instructions == (
        "Use the wiki_ tools. These toolsets are off in this server's config, so their tools "
        "are not offered: wiki.write (Edit pages.); wiki.admin (Manage spaces.). If a task "
        "needs one, tell the user it can be switched on with the product's toolsets key."
    )


async def test_build_server_with_every_toolset_on_adds_no_note() -> None:
    server = _wiki_server({})
    assert {tool.name for tool in await server.list_tools()} == {"wiki_read", "wiki_edit"}
    assert server.instructions == "Use the wiki_ tools."


def test_build_server_refuses_an_unknown_toolset() -> None:
    with pytest.raises(ConfigError, match=r"'wiki.toolsets' names 'agile'"):
        _wiki_server({"toolsets": ["agile"]})
