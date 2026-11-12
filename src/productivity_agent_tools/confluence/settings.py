"""Settings for the Confluence product, read from the ``[confluence]`` config table."""

from dataclasses import dataclass
from typing import Any

from productivity_agent_tools.config import (
    ProductSettings,
    as_str,
    max_results,
    product_overrides,
    sub_path,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ConfluenceSettings(ProductSettings):
    """Settings for the Confluence module, which is on only when ``[confluence]`` is configured.

    Attributes:
        path: Sub-path Confluence is served under, or an empty string when it is at the root.
        default_max_results: Row cap for searches and lists when the caller gives none.
        default_space: Space key used when a tool call names no space, or None.
    """

    path: str = "/confluence"
    default_max_results: int = 25
    default_space: str | None = None


def parse_confluence_settings(raw: dict[str, Any]) -> ConfluenceSettings:
    """Build Confluence settings from the ``[confluence]`` table.

    Args:
        raw: Parsed ``[confluence]`` table (possibly empty).

    Returns:
        The Confluence settings.

    Raises:
        ConfigError: If a value has the wrong type.
    """
    defaults = ConfluenceSettings()
    return ConfluenceSettings(
        path=sub_path(raw, defaults.path),
        default_max_results=max_results(raw, "confluence", defaults.default_max_results),
        default_space=(as_str(raw, "default_space", None) or "").strip() or None,
        **product_overrides(raw, "confluence"),
    )
