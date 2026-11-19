"""Local configuration: a TOML file plus command-line overrides.

The configuration file lives outside the repository (by default under
``~/.config/productivity-agent-tools/``) because it names the internal host.
Command-line flags, when given, win over the file.
"""

import asyncio
import os
import re
import shlex
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from productivity_agent_tools.errors import ConfigError
from productivity_agent_tools.logs import DEFAULT_LOG_LEVEL, LOG_LEVELS
from productivity_agent_tools.tokens import TokenSources

DEFAULT_CONFIG_PATH: Path = Path("~/.config/productivity-agent-tools/config.toml")

_COOKIE_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_COOKIE_VALUE = re.compile(r"[\x21\x23-\x2B\x2D-\x3A\x3C-\x5B\x5D-\x7E]*")
_UNEXPANDED = re.compile(r"\$(\w+|\{[^}]*\})")
# Tables at the top of the config that are not products.
_TOP_LEVEL_TABLES: frozenset[str] = frozenset({"cookies", "group"})
# Tables that configure a sign-in rather than a served product, so the server
# skips them rather than rejecting them. ``[microsoft]`` holds the OAuth client
# ``login microsoft`` signs in with; the Outlook data is served by the
# ``outlook_calendar`` and ``outlook_mail`` products, which have their own tables
# and share the ``microsoft`` keychain token.
_SIGN_IN_TABLES: frozenset[str] = frozenset({"microsoft"})


@dataclass(frozen=True, slots=True)
class Connection:
    """Where and how to reach one product, after defaults are applied.

    Attributes:
        base_url: Scheme and host, plus any path prefix in front of the product's own path.
        ca_bundle: PEM file of trusted roots, or None for the operating system's trust store.
        cookies: Extra cookies sent with every request, by name.
        timeout_seconds: Per-request timeout.
    """

    base_url: str
    ca_bundle: Path | None = None
    cookies: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 30.0


@dataclass(frozen=True, slots=True, kw_only=True)
class GroupSettings:
    """Connection values shared by the products that name a ``[group.<name>]`` table.

    A group sits between the top-level defaults and a product: a product's own
    value wins, then its group's, then the top level. It lets products on one
    host (several Atlassian products, the two Outlook products) share a base URL
    and cookies without the top level having to belong to any one of them.

    Each is None when the group's table does not set it.

    Attributes:
        base_url: The group's scheme and host, or None to fall through to the top level.
        ca_bundle: The group's PEM file of trusted roots, or None.
        cookies: The group's extra cookies, replacing the top-level ones, or None.
        timeout_seconds: The group's per-request timeout, or None.
        token_env_file: The group's env file of ``<PRODUCT>_TOKEN`` lines, or None.
        token_cmd: The group's command that prints a token, or None.
    """

    base_url: str | None = None
    ca_bundle: Path | None = None
    cookies: dict[str, str] | None = None
    timeout_seconds: float | None = None
    token_env_file: Path | None = None
    token_cmd: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ProductSettings:
    """Connection values a product's own table may set for itself.

    Each connection value is None when the product's table does not set it, in
    which case the product's group (if it names one) applies, then the top level.

    Attributes:
        base_url: The product's own scheme and host, or None to fall through to group/top level.
        ca_bundle: The product's own PEM file of trusted roots, or None.
        cookies: The product's own extra cookies, replacing the inherited ones, or None.
        timeout_seconds: The product's own per-request timeout, or None.
        token_env_file: The product's own env file defining its ``<PRODUCT>_TOKEN``, or None.
        token_cmd: The product's own command that prints its token, or None.
        group: Name of the ``[group.<name>]`` table this product draws shared defaults from,
            or None for the top-level defaults only.
    """

    base_url: str | None = None
    ca_bundle: Path | None = None
    cookies: dict[str, str] | None = None
    timeout_seconds: float | None = None
    token_env_file: Path | None = None
    token_cmd: tuple[str, ...] | None = None
    group: str | None = None


type ProductParser = Callable[[dict[str, Any]], ProductSettings]
"""Builds one product's settings from its config table."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved settings for one run of the server or a setup command.

    ``base_url``, ``ca_bundle``, ``cookies`` and ``timeout_seconds`` are the
    defaults for every product; a product's own table can override each one.
    Use ``connection_for`` to get the values that apply to a product.

    Attributes:
        base_url: Default scheme and host, plus any path prefix shared by the products.
            None when every product names its own.
        ca_bundle: PEM file of trusted roots, or None for the operating system's trust store.
        dry_run: When True, write tools describe the request but send nothing.
        cookies: Extra cookies sent with every request, by name. Empty by default.
        token_env_file: Default env file holding ``<PRODUCT>_TOKEN=...`` lines, or None.
        token_cmd: Default command that prints a product's token (``{product}`` in it is
            replaced by the token name), or None.
        timeout_seconds: Per-request timeout.
        log_level: Least severe log level written to stderr.
        products: Settings of each configured product, and so each one switched on, by name,
            in the order of their tables in the config file.
        groups: Shared connection defaults by group name, from the ``[group.<name>]`` tables.
    """

    base_url: str | None = None
    ca_bundle: Path | None = None
    dry_run: bool = True
    cookies: dict[str, str] = field(default_factory=dict)
    token_env_file: Path | None = None
    token_cmd: tuple[str, ...] | None = None
    timeout_seconds: float = 30.0
    log_level: str = DEFAULT_LOG_LEVEL
    products: Mapping[str, ProductSettings] = field(default_factory=dict)
    groups: Mapping[str, GroupSettings] = field(default_factory=dict)

    def product[S: ProductSettings](self, name: str, kind: type[S]) -> S | None:
        """Get one product's settings, typed as its own settings class.

        Args:
            name: The product's name.
            kind: The product's settings class.

        Returns:
            The settings, or None when the product is not configured.

        Raises:
            ConfigError: If the product's settings are of another class.
        """
        found = self.products.get(name)
        if found is None:
            return None
        if not isinstance(found, kind):
            raise ConfigError(f"The settings of '{name}' are not {kind.__name__}.")
        return found

    def token_sources_for(self, product: ProductSettings) -> TokenSources:
        """Resolve where a product's token may be found: its own table, its group, the top.

        ``token_cmd`` and ``token_env_file`` are resolved separately, each from the
        most specific level that sets it.

        Args:
            product: The product's settings.

        Returns:
            The command and env file that apply to the product.
        """
        group = self.groups.get(product.group) if product.group else None
        command = product.token_cmd or (group.token_cmd if group else None) or self.token_cmd
        env_file = (
            product.token_env_file
            or (group.token_env_file if group else None)
            or self.token_env_file
        )
        return TokenSources(env_file=env_file, command=command)

    def connection_for(self, product: ProductSettings) -> Connection:
        """Resolve one product's connection: its own values, then its group's, then the defaults.

        A ``cookies`` table at any level replaces the inherited one rather than
        adding to it, so cookies meant for one host are not sent to another: the
        product's table wins, then its group's, then the top-level ``[cookies]``.

        Args:
            product: The product's settings.

        Returns:
            The connection to use for that product.

        Raises:
            ConfigError: If none of the product, its group or the top level names a base URL.
        """
        group = self.groups.get(product.group) if product.group else None
        group_base = group.base_url if group else None
        group_ca = group.ca_bundle if group else None
        group_cookies = group.cookies if group else None
        group_timeout = group.timeout_seconds if group else None
        base_url = product.base_url or group_base or self.base_url
        if not base_url:
            raise ConfigError(
                "No base_url is set. Add base_url at the top of the config, under the "
                "product's group, or under the product's own table, e.g. "
                'base_url = "https://host".'
            )
        if product.cookies is not None:
            cookies = product.cookies
        elif group_cookies is not None:
            cookies = group_cookies
        else:
            cookies = self.cookies
        timeout = product.timeout_seconds
        if timeout is None:
            timeout = self.timeout_seconds if group_timeout is None else group_timeout
        return Connection(
            base_url=base_url,
            ca_bundle=product.ca_bundle or group_ca or self.ca_bundle,
            cookies=dict(cookies),
            timeout_seconds=timeout,
        )


def as_bool(raw: dict[str, Any], key: str, default: bool) -> bool:
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


def as_str(raw: dict[str, Any], key: str, default: str | None) -> str | None:
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


def expand(value: str, label: str) -> str:
    """Expand ``~`` and environment variables (``$HOME``, ``${HOME}``) in a config value.

    Args:
        value: The value as written.
        label: Name of the key as the user would write it, for error messages.

    Returns:
        The expanded value.

    Raises:
        ConfigError: If it names an environment variable that is not set.
    """
    expanded = os.path.expanduser(os.path.expandvars(value))
    if missing := _UNEXPANDED.search(expanded):
        raise ConfigError(
            f"Config key '{label}' uses {missing.group(0)}, which is not set in the environment."
        )
    return expanded


def _as_path(raw: dict[str, Any], key: str, label: str | None = None) -> Path | None:
    """Read an optional path value, expanding ``~`` and environment variables.

    Args:
        raw: Parsed TOML table.
        key: Key to read.
        label: Name of the key as the user would write it; defaults to ``key``.

    Returns:
        The expanded path, or None when absent.

    Raises:
        ConfigError: If the value names an environment variable that is not set.
    """
    value = as_str(raw, key, None)
    return Path(expand(value, label or key)) if value else None


def _token_cmd(raw: dict[str, Any], label: str) -> tuple[str, ...] | None:
    """Read an optional ``token_cmd``: a command line, or a list of program and arguments.

    A string is split the way a shell splits words (quotes group, nothing else is
    interpreted: no pipes, no globs). Each word has ``~`` and environment variables
    expanded. The command is run without a shell, so ``sh -c '...'`` is the way to
    use a pipe.

    Args:
        raw: Parsed TOML table.
        label: Name of the key as the user would write it, for error messages.

    Returns:
        The program and its arguments, or None when absent.

    Raises:
        ConfigError: If the value is not a non-empty string or list of strings, or
            names an environment variable that is not set.
    """
    value = raw.get("token_cmd")
    if value is None:
        return None
    if isinstance(value, str):
        try:
            words = shlex.split(value)
        except ValueError as exc:
            raise ConfigError(f"Config key '{label}' is not a valid command line: {exc}.") from exc
    elif isinstance(value, list) and all(isinstance(word, str) for word in value):
        words = list(value)
    else:
        raise ConfigError(f"Config key '{label}' must be a command line or a list of strings.")
    if not words or not words[0].strip():
        raise ConfigError(f"Config key '{label}' must name a program.")
    return tuple(expand(word, label) for word in words)


def log_level_name(value: str) -> str:
    """Validate a log level name.

    Args:
        value: The level as configured, in any letter case.

    Returns:
        The lower-cased level name.

    Raises:
        ConfigError: If the value is not a known level.
    """
    cleaned = value.strip().lower()
    if cleaned not in LOG_LEVELS:
        raise ConfigError(f"Log level must be one of {', '.join(LOG_LEVELS)}, got '{value}'.")
    return cleaned


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


def _parse_cookies(raw: object, label: str = "cookies") -> dict[str, str]:
    """Build the extra-cookie mapping from a ``[cookies]`` table.

    Args:
        raw: The parsed table, or an empty mapping when absent.
        label: Name of the table as the user would write it, for error messages.

    Returns:
        Cookie values by name.

    Raises:
        ConfigError: If the table is malformed or a cookie is invalid.
    """
    if not isinstance(raw, dict) or not all(isinstance(v, str) for v in raw.values()):
        raise ConfigError(f"Config table '{label}' must map cookie names to string values.")
    return {_cookie_name(str(name)): _cookie_value(str(name), value) for name, value in raw.items()}


def _base_url(raw: dict[str, Any], label: str) -> str | None:
    """Read an optional base URL, requiring https.

    Args:
        raw: Parsed TOML table.
        label: Name of the key as the user would write it, for error messages.

    Returns:
        The URL without a trailing slash, or None when absent or empty.

    Raises:
        ConfigError: If the value is not a string or is not an https URL.
    """
    try:
        value = as_str(raw, "base_url", None)
    except ConfigError as exc:
        raise ConfigError(f"Config key '{label}' must be a string.") from exc
    if not value:
        return None
    if not value.startswith("https://"):
        raise ConfigError(f"Config key '{label}' must start with https://.")
    return value.rstrip("/")


def _timeout(raw: dict[str, Any], label: str) -> float | None:
    """Read an optional per-request timeout.

    Args:
        raw: Parsed TOML table.
        label: Name of the key as the user would write it, for error messages.

    Returns:
        The timeout in seconds, or None when absent.

    Raises:
        ConfigError: If the value is not a positive number.
    """
    value = raw.get("timeout_seconds")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ConfigError(f"Config key '{label}' must be a positive number.")
    return float(value)


def sub_path(raw: dict[str, Any], default: str) -> str:
    """Read the sub-path a product is served under.

    Args:
        raw: The product's parsed table.
        default: Path used when the key is absent.

    Returns:
        The path with one leading slash and no trailing one, or an empty string for the root.
    """
    path = as_str(raw, "path", default)
    segment = default.strip("/") if path is None else path.strip("/")
    return f"/{segment}" if segment else ""


def max_results(raw: dict[str, Any], table: str, default: int) -> int:
    """Read a product's default row cap.

    Args:
        raw: The product's parsed table.
        table: Name of the table, for error messages.
        default: Value used when the key is absent.

    Returns:
        The row cap.

    Raises:
        ConfigError: If the value is not a positive integer.
    """
    value = raw.get("default_max_results", default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError(f"Config key '{table}.default_max_results' must be a positive integer.")
    return value


def login_timeout(raw: dict[str, Any], table: str, default: float) -> float:
    """Read how long a browser sign-in waits for the user.

    Args:
        raw: The table's parsed contents.
        table: Name of the table, for error messages.
        default: Seconds used when the key is absent.

    Returns:
        The wait in seconds.

    Raises:
        ConfigError: If the value is not a positive number.
    """
    value = raw.get("login_timeout_seconds", default)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ConfigError(f"Config key '{table}.login_timeout_seconds' must be a positive number.")
    return float(value)


def _connection_overrides(raw: dict[str, Any], table: str) -> dict[str, Any]:
    """Read the connection values a product or group table sets for itself.

    Args:
        raw: The parsed table.
        table: Name of the table, for error messages.

    Returns:
        Keyword arguments for the connection fields (``base_url``, ``ca_bundle``,
        ``cookies``, ``timeout_seconds``) and the token sources (``token_env_file``,
        ``token_cmd``).

    Raises:
        ConfigError: If a value is invalid.
    """
    return {
        "base_url": _base_url(raw, f"{table}.base_url"),
        "ca_bundle": _as_path(raw, "ca_bundle", f"{table}.ca_bundle"),
        "cookies": (
            _parse_cookies(raw["cookies"], f"{table}.cookies") if "cookies" in raw else None
        ),
        "timeout_seconds": _timeout(raw, f"{table}.timeout_seconds"),
        "token_env_file": _as_path(raw, "token_env_file", f"{table}.token_env_file"),
        "token_cmd": _token_cmd(raw, f"{table}.token_cmd"),
    }


def product_overrides(raw: dict[str, Any], table: str) -> dict[str, Any]:
    """Read the connection values and group a product's table sets for itself.

    Args:
        raw: The product's parsed table.
        table: Name of the table, for error messages.

    Returns:
        Keyword arguments for the ``ProductSettings`` connection fields and ``group``.

    Raises:
        ConfigError: If a value is invalid.
    """
    group = as_str(raw, "group", None)
    return {
        **_connection_overrides(raw, table),
        "group": group.strip() if group and group.strip() else None,
    }


def parse_group(raw: object, name: str) -> GroupSettings:
    """Build one group's shared connection defaults from its ``[group.<name>]`` table.

    Args:
        raw: The parsed group table.
        name: The group's name, for error messages.

    Returns:
        The group settings.

    Raises:
        ConfigError: If the table is not a table or a value is invalid.
    """
    if not isinstance(raw, dict):
        raise ConfigError(f"Config table 'group.{name}' must be a table.")
    return GroupSettings(**_connection_overrides(raw, f"group.{name}"))


def parse_settings(
    raw: dict[str, Any], parsers: Mapping[str, ProductParser] | None = None
) -> Settings:
    """Build settings from a parsed TOML document.

    Every table other than ``[cookies]`` configures a product, and so switches
    it on; the product's own parser reads it.

    Args:
        raw: The parsed configuration document.
        parsers: Settings parser of each known product, by name. None uses the
            installed products.

    Returns:
        The settings described by the document.

    Raises:
        ConfigError: If a required key is missing, a value is invalid, or a
            table names no installed product.
    """
    if parsers is None:
        from productivity_agent_tools.products import installed_products

        parsers = {name: p.parse_settings for name, p in installed_products().items()}
    timeout = _timeout(raw, "timeout_seconds")
    groups = _parse_groups(raw.get("group", {}))
    products: dict[str, ProductSettings] = {}
    for name, table in raw.items():
        if name in _TOP_LEVEL_TABLES or name in _SIGN_IN_TABLES:
            continue
        if name in parsers:
            if not isinstance(table, dict):
                raise ConfigError(f"Config key '{name}' must be a table.")
            products[name] = parsers[name](table)
        elif isinstance(table, dict):
            known = ", ".join(sorted(parsers)) or "none"
            raise ConfigError(
                f"Config table '{name}' names no installed product. Products: {known}."
            )
    for name, product in products.items():
        if product.group is not None and product.group not in groups:
            known = ", ".join(sorted(groups)) or "none"
            raise ConfigError(
                f"Product '{name}' names group '{product.group}', but there is no "
                f"[group.{product.group}] table. Groups: {known}."
            )
    settings = Settings(
        base_url=_base_url(raw, "base_url"),
        ca_bundle=_as_path(raw, "ca_bundle"),
        dry_run=as_bool(raw, "dry_run", True),
        cookies=_parse_cookies(raw.get("cookies", {})),
        token_env_file=_as_path(raw, "token_env_file"),
        token_cmd=_token_cmd(raw, "token_cmd"),
        timeout_seconds=30.0 if timeout is None else timeout,
        log_level=log_level_name(as_str(raw, "log_level", None) or DEFAULT_LOG_LEVEL),
        products=products,
        groups=groups,
    )
    for product in products.values():
        settings.connection_for(product)
    return settings


def _parse_groups(raw: object) -> dict[str, GroupSettings]:
    """Build the group settings from the ``[group]`` table.

    Args:
        raw: The parsed ``[group]`` table, or an empty mapping when absent.

    Returns:
        The shared connection defaults by group name.

    Raises:
        ConfigError: If ``[group]`` or one of its entries is malformed.
    """
    if not isinstance(raw, dict):
        raise ConfigError("Config table 'group' must hold named [group.<name>] tables.")
    return {name: parse_group(table, name) for name, table in raw.items()}


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
    log_level: str | None = None,
) -> Settings:
    """Layer command-line overrides on top of file settings.

    Args:
        settings: Settings loaded from the file.
        dry_run: Override for ``dry_run``; None keeps the file value.
        cookies: Extra ``name=value`` cookies; these are added to the file's cookies,
            including a product's own table, and replace any with the same name.
        token_env_file: Override for ``token_env_file`` at every level (top, groups and
            products); None keeps the file's values.
        log_level: Override for ``log_level``; None keeps the file value.

    Returns:
        A new settings object with the overrides applied.

    Raises:
        ConfigError: If a cookie is not a valid ``name=value`` pair or the log level is unknown.
    """
    extra: dict[str, str] = {}
    for pair in cookies or []:
        name, separator, value = pair.partition("=")
        if not separator:
            raise ConfigError(f"Cookie '{pair}' must be written as name=value.")
        extra[_cookie_name(name)] = _cookie_value(name, value)

    def with_extra[P: ProductSettings | GroupSettings](level: P) -> P:
        if token_env_file is not None:
            level = replace(level, token_env_file=None)
        if level.cookies is None:
            return level
        return replace(level, cookies={**level.cookies, **extra})

    return replace(
        settings,
        dry_run=settings.dry_run if dry_run is None else dry_run,
        cookies={**settings.cookies, **extra},
        products={name: with_extra(product) for name, product in settings.products.items()},
        groups={name: with_extra(group) for name, group in settings.groups.items()},
        log_level=settings.log_level if log_level is None else log_level_name(log_level),
        token_env_file=(
            settings.token_env_file if token_env_file is None else token_env_file.expanduser()
        ),
    )
