"""Local configuration: a TOML file plus command-line overrides.

The configuration file lives outside the repository (by default under
``~/.config/productivity-agent-tools/``) because it names the internal host.
Command-line flags, when given, win over the file.
"""

import asyncio
import re
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from productivity_agent_tools.errors import ConfigError

DEFAULT_CONFIG_PATH: Path = Path("~/.config/productivity-agent-tools/config.toml")

_COOKIE_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_COOKIE_VALUE = re.compile(r"[\x21\x23-\x2B\x2D-\x3A\x3C-\x5B\x5D-\x7E]*")

DEFAULT_PRESETS: dict[str, str] = {
    "mine-open": ("assignee = currentUser() AND resolution = Unresolved ORDER BY updated DESC"),
    "updated-since-yesterday": (
        "assignee = currentUser() AND updated >= startOfDay(-1d) ORDER BY updated DESC"
    ),
    "current-sprint": (
        "assignee = currentUser() AND sprint in openSprints() ORDER BY status ASC, updated DESC"
    ),
}


@dataclass(frozen=True, slots=True)
class JiraSettings:
    """Settings for the Jira module.

    Attributes:
        path: Sub-path Jira is served under, or an empty string when it is at the site root.
        default_max_results: Row cap for searches when the caller gives none.
        presets: Named JQL queries the agent can run by name.
    """

    path: str = "/jira"
    default_max_results: int = 25
    presets: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_PRESETS))


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved settings for one run of the server or a setup command.

    Attributes:
        base_url: Scheme and host of the server, without a sub-path.
        ca_bundle: PEM file of trusted roots, or None for the operating system's trust store.
        dry_run: When True, write tools describe the request but send nothing.
        cookies: Extra cookies sent with every request, by name. Empty by default.
        token_env_file: Optional env file holding ``<PRODUCT>_TOKEN=...``.
        timeout_seconds: Per-request timeout.
        jira: Jira module settings.
    """

    base_url: str
    ca_bundle: Path | None = None
    dry_run: bool = True
    cookies: dict[str, str] = field(default_factory=dict)
    token_env_file: Path | None = None
    timeout_seconds: float = 30.0
    jira: JiraSettings = field(default_factory=JiraSettings)


def _as_bool(raw: dict[str, Any], key: str, default: bool) -> bool:
    """Read a boolean that may be written as true/false or 1/0.

    Args:
        raw: Parsed TOML table.
        key: Key to read.
        default: Value used when the key is absent.

    Returns:
        The boolean value.

    Raises:
        ConfigError: If the value is neither a boolean nor 0 or 1.
    """
    value = raw.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise ConfigError(f"Config key '{key}' must be true/false or 1/0, got {value!r}.")


def _as_str(raw: dict[str, Any], key: str, default: str | None) -> str | None:
    """Read an optional string value.

    Args:
        raw: Parsed TOML table.
        key: Key to read.
        default: Value used when the key is absent.

    Returns:
        The string, or the default.

    Raises:
        ConfigError: If the value is present but not a string.
    """
    value = raw.get(key, default)
    if value is None or isinstance(value, str):
        return value
    raise ConfigError(f"Config key '{key}' must be a string, got {value!r}.")


def _as_path(raw: dict[str, Any], key: str) -> Path | None:
    """Read an optional path value, expanding ``~``.

    Args:
        raw: Parsed TOML table.
        key: Key to read.

    Returns:
        The expanded path, or None when absent.
    """
    value = _as_str(raw, key, None)
    return Path(value).expanduser() if value else None


def _cookie_name(name: str) -> str:
    """Validate a cookie name.

    Args:
        name: The cookie name as configured.

    Returns:
        The name with surrounding whitespace removed.

    Raises:
        ConfigError: If the name is empty or contains characters a cookie name cannot.
    """
    cleaned = name.strip()
    if not cleaned or not _COOKIE_NAME.fullmatch(cleaned):
        raise ConfigError(f"'{name}' is not a valid cookie name.")
    return cleaned


def _cookie_value(name: str, value: str) -> str:
    """Validate a cookie value.

    Args:
        name: The cookie's name, used in the error message only.
        value: The cookie value as configured.

    Returns:
        The value with surrounding whitespace removed.

    Raises:
        ConfigError: If the value contains characters that would break the header.
    """
    cleaned = value.strip()
    if not _COOKIE_VALUE.fullmatch(cleaned):
        raise ConfigError(
            f"The value of cookie '{name.strip()}' contains characters a cookie cannot hold."
        )
    return cleaned


def _parse_cookies(raw: object) -> dict[str, str]:
    """Build the extra-cookie mapping from the ``[cookies]`` table.

    Args:
        raw: The parsed ``[cookies]`` table, or an empty mapping when absent.

    Returns:
        Cookie values by name.

    Raises:
        ConfigError: If the table is malformed or a cookie is invalid.
    """
    if not isinstance(raw, dict) or not all(isinstance(v, str) for v in raw.values()):
        raise ConfigError("Config table 'cookies' must map cookie names to string values.")
    return {_cookie_name(str(name)): _cookie_value(str(name), value) for name, value in raw.items()}


def _parse_jira(raw: dict[str, Any]) -> JiraSettings:
    """Build Jira settings from the ``[jira]`` table.

    Args:
        raw: Parsed ``[jira]`` table (possibly empty).

    Returns:
        The Jira settings.

    Raises:
        ConfigError: If a value has the wrong type.
    """
    defaults = JiraSettings()
    path = _as_str(raw, "path", defaults.path)
    segment = defaults.path.strip("/") if path is None else path.strip("/")
    max_results = raw.get("default_max_results", defaults.default_max_results)
    if isinstance(max_results, bool) or not isinstance(max_results, int) or max_results < 1:
        raise ConfigError("Config key 'jira.default_max_results' must be a positive integer.")
    presets_raw = raw.get("presets")
    if presets_raw is None:
        presets = dict(DEFAULT_PRESETS)
    elif isinstance(presets_raw, dict) and all(isinstance(v, str) for v in presets_raw.values()):
        presets = {str(name): str(jql) for name, jql in presets_raw.items()}
    else:
        raise ConfigError("Config table 'jira.presets' must map preset names to JQL strings.")
    return JiraSettings(
        path=f"/{segment}" if segment else "",
        default_max_results=max_results,
        presets=presets,
    )


def parse_settings(raw: dict[str, Any]) -> Settings:
    """Build settings from a parsed TOML document.

    Args:
        raw: The parsed configuration document.

    Returns:
        The settings described by the document.

    Raises:
        ConfigError: If a required key is missing or a value is invalid.
    """
    base_url = _as_str(raw, "base_url", None)
    if not base_url:
        raise ConfigError("Config key 'base_url' is required, e.g. base_url = \"https://host\".")
    if not base_url.startswith("https://"):
        raise ConfigError("Config key 'base_url' must start with https://.")
    timeout = raw.get("timeout_seconds", 30.0)
    if isinstance(timeout, bool) or not isinstance(timeout, int | float) or timeout <= 0:
        raise ConfigError("Config key 'timeout_seconds' must be a positive number.")
    jira_raw = raw.get("jira", {})
    if not isinstance(jira_raw, dict):
        raise ConfigError("Config key 'jira' must be a table.")
    return Settings(
        base_url=base_url.rstrip("/"),
        ca_bundle=_as_path(raw, "ca_bundle"),
        dry_run=_as_bool(raw, "dry_run", True),
        cookies=_parse_cookies(raw.get("cookies", {})),
        token_env_file=_as_path(raw, "token_env_file"),
        timeout_seconds=float(timeout),
        jira=_parse_jira(jira_raw),
    )


async def load_settings(path: Path) -> Settings:
    """Read and parse the configuration file.

    Args:
        path: Location of the TOML file; ``~`` is expanded.

    Returns:
        The settings from the file.

    Raises:
        ConfigError: If the file is missing, unreadable, or invalid.
    """
    resolved = path.expanduser()  # noqa: ASYNC240 - reads $HOME only, no file I/O
    try:
        data = await asyncio.to_thread(resolved.read_bytes)
    except FileNotFoundError as exc:
        raise ConfigError(
            f"Config file not found at {resolved}. Copy config.example.toml there and edit it."
        ) from exc
    except OSError as exc:
        raise ConfigError(f"Could not read config file {resolved}: {exc.strerror}.") from exc
    try:
        raw = tomllib.loads(data.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"Config file {resolved} is not valid TOML: {exc}.") from exc
    return parse_settings(raw)


def apply_overrides(
    settings: Settings,
    *,
    dry_run: bool | None = None,
    cookies: list[str] | None = None,
    token_env_file: Path | None = None,
) -> Settings:
    """Layer command-line overrides on top of file settings.

    Args:
        settings: Settings loaded from the file.
        dry_run: Override for ``dry_run``; None keeps the file value.
        cookies: Extra ``name=value`` cookies; these are added to the file's cookies
            and replace any with the same name.
        token_env_file: Override for ``token_env_file``; None keeps the file value.

    Returns:
        A new settings object with the overrides applied.

    Raises:
        ConfigError: If a cookie is not a valid ``name=value`` pair.
    """
    merged = dict(settings.cookies)
    for pair in cookies or []:
        name, separator, value = pair.partition("=")
        if not separator:
            raise ConfigError(f"Cookie '{pair}' must be written as name=value.")
        merged[_cookie_name(name)] = _cookie_value(name, value)
    return replace(
        settings,
        dry_run=settings.dry_run if dry_run is None else dry_run,
        cookies=merged,
        token_env_file=(
            settings.token_env_file if token_env_file is None else token_env_file.expanduser()
        ),
    )
