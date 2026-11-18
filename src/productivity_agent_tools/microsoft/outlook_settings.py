"""Settings for the Outlook products, read from their own config tables.

The two read-only Outlook products (``outlook_calendar`` and ``outlook_mail``)
share one settings shape and one keychain token (``microsoft``), but each is
switched on by its own table so a user can enable just one. The table carries
only the connection values every product has (``base_url`` for the Outlook REST
API, and the usual overrides) plus a row cap; the OAuth parameters live in the
separate ``[microsoft]`` table that ``login microsoft`` reads, not here.
"""

from dataclasses import dataclass
from typing import Any

from productivity_agent_tools.config import ProductSettings, max_results, product_overrides

DEFAULT_MAX_RESULTS: int = 25


@dataclass(frozen=True, slots=True, kw_only=True)
class OutlookSettings(ProductSettings):
    """Settings shared by the Outlook products.

    Attributes:
        default_max_results: Rows a list tool returns when the call names no limit.
    """

    default_max_results: int = DEFAULT_MAX_RESULTS


def parse_outlook_settings(raw: dict[str, Any], table: str) -> OutlookSettings:
    """Build Outlook product settings from one of the Outlook tables.

    Args:
        raw: The parsed product table (possibly empty).
        table: Name of the table, for error messages, such as ``"outlook_calendar"``.

    Returns:
        The settings.

    Raises:
        ConfigError: If a value is invalid.
    """
    return OutlookSettings(
        default_max_results=max_results(raw, table, DEFAULT_MAX_RESULTS),
        **product_overrides(raw, table),
    )
