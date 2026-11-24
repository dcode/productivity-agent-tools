"""The product object the server finds through the entry point."""

from collections.abc import AsyncIterator, Mapping

from pat_holidays.client import Holidays, NagerAdapter
from pat_holidays.settings import HolidaySettings, parse_holiday_settings
from pat_holidays.tools import register_holiday_tools
from productivity_agent_tools.credentials import Credential
from productivity_agent_tools.errors import ConfigError
from productivity_agent_tools.payload import text
from productivity_agent_tools.products import RemoteProduct
from productivity_agent_tools.tokens import TokenSources

# Added to the server's instructions when the product is on: when to use the tools.
INSTRUCTIONS: str = (
    "The holidays_ tools list public holidays. Use them when planning work across days, "
    "to avoid scheduling on a day off."
)


async def no_credential(name: str, sources: TokenSources) -> Credential:
    """Nager.Date is public: authenticate with no headers at all."""
    del name, sources

    async def headers() -> Mapping[str, str]:
        return {}

    return Credential(headers=headers, source="not needed (a public API)", label="credential")


async def doctor(holidays: Holidays) -> AsyncIterator[str]:
    """Check the API answers and knows the configured country.

    Yields:
        One line per check that passed.

    Raises:
        AgentToolsError: When a check fails.
    """
    codes = {text(row, "countryCode") for row in await holidays.countries()}
    yield f"Nager.Date answers ({len(codes)} countries)"
    if holidays.settings.country not in codes:
        raise ConfigError(f"Nager.Date does not cover country '{holidays.settings.country}'.")
    yield f"country {holidays.settings.country} is covered"


PRODUCT: RemoteProduct[HolidaySettings, Holidays] = RemoteProduct(
    name="holidays",
    title="Public holidays",
    instructions=INSTRUCTIONS,
    settings_type=HolidaySettings,
    parse_settings=parse_holiday_settings,
    adapter=NagerAdapter.from_connection,
    make_client=Holidays,
    register_tools=register_holiday_tools,
    doctor=doctor,
    resolve_credential=no_credential,
)
