"""Tests for the example plugin in examples/holidays-plugin (docs/contributing/plugin.md).

The plugin is loaded from its source folder, not installed, and run against a
fake Nager.Date, through the same server assembly the real products use.
"""

import tomllib
from datetime import date
from pathlib import Path

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult
from pat_holidays.client import Holidays, NagerAdapter, describe
from pat_holidays.product import PRODUCT, doctor, no_credential
from pat_holidays.settings import DEFAULT_BASE_URL, parse_holiday_settings

from productivity_agent_tools.config import parse_settings
from productivity_agent_tools.errors import ApiError, ConfigError, NotFoundError
from productivity_agent_tools.server import Connected, build_server
from productivity_agent_tools.tokens import TokenSources

PLUGIN = Path(__file__).parent.parent / "examples" / "holidays-plugin"
YEAR = date.today().year
NEW_YEAR = {"date": f"{YEAR}-01-01", "localName": "New Year's Day", "name": "New Year's Day"}
UNITY = {
    "date": f"{YEAR}-10-03",
    "localName": "Tag der Deutschen Einheit",
    "name": "German Unity Day",
}


# --8<-- [start:fake]
def nager(request: httpx.Request) -> httpx.Response:
    """A Nager.Date that knows the US and Germany."""
    path = request.url.path
    if path == "/api/v3/AvailableCountries":
        return httpx.Response(200, json=[{"countryCode": "US"}, {"countryCode": "DE"}])
    if path == f"/api/v3/PublicHolidays/{YEAR}/US":
        return httpx.Response(200, json=[NEW_YEAR])
    if path == "/api/v3/NextPublicHolidays/DE":
        return httpx.Response(200, json=[UNITY, NEW_YEAR])
    if path == "/api/v3/NextPublicHolidays/XX":
        return httpx.Response(204)
    if path == "/api/v3/NextPublicHolidays/ZZ":
        return httpx.Response(500)
    return httpx.Response(404)


def adapter() -> NagerAdapter:
    async def headers() -> dict[str, str]:
        return {}

    return NagerAdapter(
        base_url=DEFAULT_BASE_URL, credentials=headers, transport=httpx.MockTransport(nager)
    )


async def call(tool: str, arguments: dict[str, object]) -> dict[str, object]:
    settings = parse_settings({"holidays": {}}, parsers={"holidays": PRODUCT.parse_settings})
    async with adapter() as api:
        server = build_server(
            settings,
            {"holidays": Connected(api, "not needed")},
            products={"holidays": PRODUCT},
            integrations=[],
        )
        result = await server.call_tool(tool, arguments)
    assert isinstance(result, CallToolResult)
    assert isinstance(result.structured_content, dict)
    return result.structured_content


# --8<-- [end:fake]


def test_the_entry_point_names_the_product() -> None:
    project = tomllib.loads((PLUGIN / "pyproject.toml").read_text())
    points = project["project"]["entry-points"]["productivity_agent_tools.products"]
    assert points == {"holidays": "pat_holidays.product:PRODUCT"}
    assert PRODUCT.name == "holidays"


def test_settings_default_to_the_public_api_and_the_us() -> None:
    settings = parse_holiday_settings({})
    assert (settings.base_url, settings.country) == (DEFAULT_BASE_URL, "US")
    assert parse_holiday_settings({"country": " de "}).country == "DE"
    # A top-level base_url meant for another service does not reach this product.
    resolved = parse_settings(
        {"base_url": "https://jira.example", "holidays": {}},
        parsers={"holidays": PRODUCT.parse_settings},
    )
    assert resolved.connection_for(resolved.products["holidays"]).base_url == DEFAULT_BASE_URL


async def test_a_year_is_listed() -> None:
    result = await call("holidays_list", {})
    assert result == {"country": "US", "holidays": [f"{YEAR}-01-01: New Year's Day"]}


async def test_the_next_holidays_are_listed_with_local_names() -> None:
    result = await call("holidays_next", {"country": "de", "max_results": 1})
    assert result == {
        "country": "DE",
        "holidays": [f"{YEAR}-10-03: German Unity Day (Tag der Deutschen Einheit)"],
    }


async def test_an_unknown_country_is_explained() -> None:
    with pytest.raises(ToolError, match="check the country code"):
        await call("holidays_next", {"country": "QQ"})


async def test_empty_and_failed_answers() -> None:
    assert await call("holidays_next", {"country": "XX"}) == {"country": "XX", "holidays": []}
    with pytest.raises(ToolError, match=r"Nager\.Date returned 500"):
        await call("holidays_next", {"country": "ZZ"})


async def test_doctor_checks_the_configured_country() -> None:
    async with adapter() as api:
        good = Holidays(api, parse_holiday_settings({}))
        assert [line async for line in doctor(good)] == [
            "Nager.Date answers (2 countries)",
            "country US is covered",
        ]
        with pytest.raises(ConfigError, match="does not cover country 'FR'"):
            [
                line
                async for line in doctor(Holidays(api, parse_holiday_settings({"country": "FR"})))
            ]


async def test_the_credential_sends_nothing() -> None:
    credential = await no_credential("holidays", TokenSources())
    assert await credential.headers() == {}
    assert credential.source == "not needed (a public API)"


async def test_the_adapter_reads_text_and_maps_errors() -> None:
    async with adapter() as api:
        assert await api.get_text(f"/api/v3/PublicHolidays/{YEAR}/US")
        with pytest.raises(NotFoundError):
            await api.get("/nowhere")
        with pytest.raises(ApiError):
            await api.get("/api/v3/NextPublicHolidays/ZZ")


def test_a_holiday_reads_as_one_line() -> None:
    assert describe({"date": "2026-12-25", "name": "Christmas Day"}) == "2026-12-25: Christmas Day"
    assert describe({}) == "None: ?"
