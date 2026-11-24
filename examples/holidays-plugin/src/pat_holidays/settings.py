"""The ``[holidays]`` config table."""

from dataclasses import dataclass
from typing import Any

from productivity_agent_tools.config import ProductSettings, as_str, product_overrides

DEFAULT_BASE_URL: str = "https://date.nager.at"


@dataclass(frozen=True, slots=True, kw_only=True)
class HolidaySettings(ProductSettings):
    """Settings for the holidays product, on when the config has a ``[holidays]`` table.

    Attributes:
        country: ISO 3166-1 alpha-2 code used when a tool is not given one.
    """

    country: str = "US"


def parse_holiday_settings(raw: dict[str, Any]) -> HolidaySettings:
    """Build the settings from the ``[holidays]`` table.

    The table's own ``base_url`` defaults to the public API, so the product never
    inherits a top-level ``base_url`` meant for another service.

    Args:
        raw: The parsed table (possibly empty).

    Returns:
        The settings.

    Raises:
        ConfigError: If a value has the wrong type.
    """
    overrides = product_overrides(raw, "holidays")
    overrides["base_url"] = overrides.get("base_url") or DEFAULT_BASE_URL
    country = (as_str(raw, "country", None) or "US").strip().upper()
    return HolidaySettings(country=country, **overrides)
