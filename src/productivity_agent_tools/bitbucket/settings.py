"""Settings for the Bitbucket product, read from the ``[bitbucket]`` config table."""

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
class BitbucketSettings(ProductSettings):
    """Settings for the Bitbucket module, which is on only when ``[bitbucket]`` is configured.

    Attributes:
        path: Sub-path Bitbucket is served under, or an empty string when it is at the site root.
        default_max_results: Row cap for lists when the caller gives none.
        default_project: Project key used when a tool call names no project, or None.
        user_slug: The signed-in user's Bitbucket slug, or None to ask the server. Only
            needed when the server cannot say who the token belongs to.
    """

    path: str = "/bitbucket"
    default_max_results: int = 25
    default_project: str | None = None
    user_slug: str | None = None


def parse_bitbucket_settings(raw: dict[str, Any]) -> BitbucketSettings:
    """Build Bitbucket settings from the ``[bitbucket]`` table.

    Args:
        raw: Parsed ``[bitbucket]`` table (possibly empty).

    Returns:
        The Bitbucket settings.

    Raises:
        ConfigError: If a value has the wrong type.
    """
    defaults = BitbucketSettings()
    return BitbucketSettings(
        path=sub_path(raw, defaults.path),
        default_max_results=max_results(raw, "bitbucket", defaults.default_max_results),
        default_project=(as_str(raw, "default_project", None) or "").strip() or None,
        user_slug=(as_str(raw, "user_slug", None) or "").strip() or None,
        **product_overrides(raw, "bitbucket"),
    )
