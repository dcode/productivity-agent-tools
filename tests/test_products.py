"""The product and integration contract, and finding them through entry points."""

from collections.abc import AsyncIterator, Callable
from importlib.metadata import EntryPoint
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer

from productivity_agent_tools import products
from productivity_agent_tools.api import ApiClient
from productivity_agent_tools.bitbucket.product import PRODUCT as BITBUCKET
from productivity_agent_tools.bitbucket.settings import BitbucketSettings
from productivity_agent_tools.capabilities import IssueTracker
from productivity_agent_tools.config import ProductSettings, Settings, parse_settings
from productivity_agent_tools.confluence.product import PRODUCT as CONFLUENCE
from productivity_agent_tools.errors import ConfigError
from productivity_agent_tools.http import AtlassianClient
from productivity_agent_tools.jira.client import JiraClient
from productivity_agent_tools.jira.product import PRODUCT as JIRA
from productivity_agent_tools.jira.settings import JiraSettings
from productivity_agent_tools.links import INTEGRATION
from productivity_agent_tools.microsoft.calendar_product import PRODUCT as OUTLOOK_CALENDAR
from productivity_agent_tools.microsoft.mail_product import PRODUCT as OUTLOOK_MAIL
from productivity_agent_tools.products import (
    Integration,
    Product,
    ToolContext,
    client_of,
    installed_integrations,
    installed_products,
)
from productivity_agent_tools.server import Connected, build_server
from productivity_agent_tools.servicenow.product import PRODUCT as SERVICENOW


def test_the_built_in_products_and_integration_are_installed() -> None:
    assert dict(installed_products()) == {
        "bitbucket": BITBUCKET,
        "confluence": CONFLUENCE,
        "jira": JIRA,
        "outlook_calendar": OUTLOOK_CALENDAR,
        "outlook_mail": OUTLOOK_MAIL,
        "servicenow": SERVICENOW,
    }
    assert installed_integrations() == (INTEGRATION,)
    assert (OUTLOOK_CALENDAR.token, OUTLOOK_MAIL.token) == ("microsoft", "microsoft")
    assert (JIRA.token, BITBUCKET.token) == ("jira", "bitbucket")


def test_a_shared_token_name_overrides_the_product_name() -> None:
    wiki = Product(
        name="gitlab_wiki",
        title="GitLab wiki",
        instructions="",
        settings_type=ProductSettings,
        adapter=AtlassianClient.from_connection,
        parse_settings=lambda raw: ProductSettings(),
        make_client=lambda api, settings: api,
        register_tools=lambda server, client, context: None,
        doctor=_no_checks,
        token_name="gitlab",
    )
    assert wiki.token == "gitlab"


async def _no_checks(client: object) -> AsyncIterator[str]:
    for line in ():
        yield line


def test_activate_rejects_another_products_settings(make_core: Callable[..., ApiClient]) -> None:
    with pytest.raises(TypeError, match="jira was given BitbucketSettings"):
        JIRA.activate(make_core(), BitbucketSettings())


def test_peer_finds_a_capability_without_knowing_the_product(
    make_jira: Callable[..., JiraClient],
) -> None:
    jira = make_jira()
    context = ToolContext(dry_run=True, token_source="", peers={"other": object(), "jira": jira})
    assert context.peer(IssueTracker) is jira
    assert ToolContext(dry_run=True, token_source="").peer(IssueTracker) is None


def test_client_of_checks_the_client_type(make_jira: Callable[..., JiraClient]) -> None:
    jira = make_jira()
    assert client_of({"jira": jira}, "jira", JiraClient) is jira
    with pytest.raises(TypeError, match="'jira' has no JiraClient"):
        client_of({"jira": object()}, "jira", JiraClient)
    with pytest.raises(TypeError, match="'jira' has no JiraClient"):
        client_of({}, "jira", JiraClient)


def test_a_table_naming_no_installed_product_is_an_error() -> None:
    with pytest.raises(ConfigError, match=r"'gitlab' names no installed product.*jira"):
        parse_settings({"base_url": "https://h.example", "gitlab": {}})
    with pytest.raises(ConfigError, match=r"Products: none"):
        parse_settings({"base_url": "https://h.example", "jira": {}}, parsers={})
    with pytest.raises(ConfigError, match="'jira' must be a table"):
        parse_settings({"base_url": "https://h.example", "jira": "yes"})


def test_parse_settings_takes_parsers_explicitly() -> None:
    settings = parse_settings(
        {"base_url": "https://h.example", "custom": {"x": 1}},
        parsers={"custom": lambda raw: ProductSettings(timeout_seconds=float(raw["x"]))},
    )
    assert settings.products == {"custom": ProductSettings(timeout_seconds=1.0)}


def test_typed_settings_lookup_checks_the_class() -> None:
    settings = Settings(products={"jira": BitbucketSettings()})
    assert settings.product("bitbucket", BitbucketSettings) is None
    with pytest.raises(ConfigError, match="'jira' are not JiraSettings"):
        settings.product("jira", JiraSettings)


async def test_a_product_from_outside_the_package_is_served(
    make_core: Callable[..., AtlassianClient],
) -> None:
    def register(server: MCPServer, client: ApiClient, context: ToolContext) -> None:
        @server.tool()
        async def wiki_ping() -> str:
            """Say where the wiki is."""
            return f"{client.base_url} dry_run={context.dry_run} peers={sorted(context.peers)}"

    wiki: Product[ProductSettings, ApiClient] = Product(
        name="wiki",
        title="Wiki",
        instructions="The wiki_ tools.",
        settings_type=ProductSettings,
        adapter=AtlassianClient.from_connection,
        parse_settings=lambda raw: ProductSettings(),
        make_client=lambda api, settings: api,
        register_tools=register,
        doctor=_no_checks,
    )
    joined: list[set[str]] = []
    both = Integration(
        name="jira_wiki",
        requires=("jira", "wiki"),
        instructions="Both.",
        register_tools=lambda server, clients: joined.append(set(clients)),
    )
    never = Integration(
        name="never",
        requires=("wiki", "absent"),
        instructions="Never.",
        register_tools=lambda server, clients: joined.append({"never"}),
    )
    known: dict[str, Product[Any, Any]] = {"jira": JIRA, "wiki": wiki}
    settings = parse_settings(
        {"base_url": "https://h.example", "wiki": {}, "jira": {}},
        parsers={name: p.parse_settings for name, p in known.items()},
    )
    server = build_server(
        settings,
        {"wiki": Connected(make_core()), "jira": Connected(make_core())},
        products=known,
        integrations=[both, never],
    )
    assert server.instructions == f"The wiki_ tools. {JIRA.instructions} Both."
    assert joined == [{"jira", "wiki"}]
    result = await server.call_tool("wiki_ping", {})
    assert "dry_run=True peers=['jira']" in str(getattr(result, "structured_content", result))


def _entry_point(name: str, target: str) -> EntryPoint:
    return EntryPoint(name=name, value=target, group=products.PRODUCT_GROUP)


def test_entry_points_must_name_matching_products(monkeypatch: pytest.MonkeyPatch) -> None:
    points = [_entry_point("jira", "productivity_agent_tools.bitbucket.product:PRODUCT")]
    monkeypatch.setattr(products, "entry_points", lambda group: points)
    products.installed_products.cache_clear()
    try:
        with pytest.raises(TypeError, match="'jira' names the product 'bitbucket'"):
            installed_products()
        points[:] = [_entry_point("jira", "productivity_agent_tools.jira.settings:JiraSettings")]
        with pytest.raises(TypeError, match=r"'jira' in .* is not a Product"):
            installed_products()
    finally:
        products.installed_products.cache_clear()
