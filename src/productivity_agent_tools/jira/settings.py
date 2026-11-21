"""Settings for the Jira product, read from the ``[jira]`` config table."""

from dataclasses import dataclass, field
from typing import Any

from productivity_agent_tools.config import (
    ProductSettings,
    as_str,
    max_results,
    product_overrides,
    sub_path,
)
from productivity_agent_tools.errors import ConfigError

DEFAULT_TASKS_JQL: str = (
    "assignee = currentUser() AND resolution = Unresolved ORDER BY updated DESC"
)
"""The issues another product (the Obsidian vault) treats as the user's open tasks."""

DEFAULT_PRESETS: dict[str, str] = {
    "mine-open": ("assignee = currentUser() AND resolution = Unresolved ORDER BY updated DESC"),
    "updated-since-yesterday": (
        "assignee = currentUser() AND updated >= startOfDay(-1d) ORDER BY updated DESC"
    ),
    "current-sprint": (
        "assignee = currentUser() AND sprint in openSprints() ORDER BY status ASC, updated DESC"
    ),
}


@dataclass(frozen=True, slots=True, kw_only=True)
class JiraSettings(ProductSettings):
    """Settings for the Jira module, which is on only when ``[jira]`` is configured.

    Attributes:
        path: Sub-path Jira is served under, or an empty string when it is at the site root.
        default_max_results: Row cap for searches when the caller gives none.
        presets: Named JQL queries the agent can run by name.
        tasks_jql: The issues offered as the user's open tasks to other products (the
            Obsidian sync), at most 100.
    """

    path: str = "/jira"
    default_max_results: int = 25
    presets: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_PRESETS))
    tasks_jql: str = DEFAULT_TASKS_JQL


def parse_jira_settings(raw: dict[str, Any]) -> JiraSettings:
    """Build Jira settings from the ``[jira]`` table.

    Args:
        raw: Parsed ``[jira]`` table (possibly empty).

    Returns:
        The Jira settings.

    Raises:
        ConfigError: If a value has the wrong type.
    """
    defaults = JiraSettings()
    presets_raw = raw.get("presets")
    if presets_raw is None:
        presets = dict(DEFAULT_PRESETS)
    elif isinstance(presets_raw, dict) and all(isinstance(v, str) for v in presets_raw.values()):
        presets = {str(name): str(jql) for name, jql in presets_raw.items()}
    else:
        raise ConfigError("Config table 'jira.presets' must map preset names to JQL strings.")
    return JiraSettings(
        path=sub_path(raw, defaults.path),
        default_max_results=max_results(raw, "jira", defaults.default_max_results),
        presets=presets,
        tasks_jql=(as_str(raw, "tasks_jql", None) or "").strip() or DEFAULT_TASKS_JQL,
        **product_overrides(raw, "jira"),
    )
