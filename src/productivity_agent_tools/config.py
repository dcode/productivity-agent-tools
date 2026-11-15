"""Local configuration: a TOML file plus command-line overrides.

The configuration file lives outside the repository (by default under
``~/.config/productivity-agent-tools/``) because it names the internal host.
Command-line flags, when given, win over the file.
"""

import asyncio
import re
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from productivity_agent_tools.errors import ConfigError
from productivity_agent_tools.logs import DEFAULT_LOG_LEVEL, LOG_LEVELS

DEFAULT_CONFIG_PATH: Path = Path("~/.config/productivity-agent-tools/config.toml")

_COOKIE_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_COOKIE_VALUE = re.compile(r"[\x21\x23-\x2B\x2D-\x3A\x3C-\x5B\x5D-\x7E]*")
# Tables at the top of the config that are not products.
_TOP_LEVEL_TABLES: frozenset[str] = frozenset({"cookies"})
# Tables that configure a module but are not themselves served products, so the
# server ignores them rather than rejecting them. ``[microsoft]`` holds the OAuth
# parameters ``login-microsoft`` seeds the refresh token from; the Outlook data
# itself is served by the ``outlook_calendar`` and ``outlook_mail`` products,
# which have their own tables and share the ``microsoft`` keychain token.
_PENDING_TABLES: frozenset[str] = frozenset({"microsoft"})


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
class ProductSettings:
    """Connection values a product's own table may set for itself.

    Each is None when the product's table does not set it, in which case the
    top-level value applies.

    Attributes:
        base_url: The product's own scheme and host, or None to use the top-level one.
        ca_bundle: The product's own PEM file of trusted roots, or None for the top-level setting.
        cookies: The product's own extra cookies, replacing the top-level ones, or None.
        timeout_seconds: The product's own per-request timeout, or None for the top-level one.
    """

    base_url: str | None = None
    ca_bundle: Path | None = None
    cookies: dict[str, str] | None = None
    timeout_seconds: float | None = None


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
        token_env_file: Optional env file holding ``<PRODUCT>_TOKEN=...``.
        timeout_seconds: Per-request timeout.
        log_level: Least severe log level written to stderr.
        products: Settings of each configured product, and so each one switched on, by name,
            in the order of their tables in the config file.
    """

    base_url: str | None = None
    ca_bundle: Path | None = None
    dry_run: bool = True
    cookies: dict[str, str] = field(default_factory=dict)
    token_env_file: Path | None = None
    timeout_seconds: float = 30.0
    log_level: str = DEFAULT_LOG_LEVEL
    products: Mapping[str, ProductSettings] = field(default_factory=dict)

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

    def connection_for(self, product: ProductSettings) -> Connection:
        """Resolve one product's connection: its own values over the defaults.

        A product's ``cookies`` table replaces the default one rather than
        adding to it, so cookies meant for one host are not sent to another.

        Args:
            product: The product's settings.

        Returns:
            The connection to use for that product.

        Raises:
            ConfigError: If neither the product nor the top level names a base URL.
        """
        base_url = product.base_url or self.base_url
        if not base_url:
            raise ConfigError(
                "No base_url is set. Add base_url at the top of the config, or under the "
                'product\'s own table, e.g. base_url = "https://host".'
            )
        timeout = product.timeout_seconds
        return Connection(
            base_url=base_url,
            ca_bundle=product.ca_bundle or self.ca_bundle,
            cookies=dict(self.cookies if product.cookies is None else product.cookies),
            timeout_seconds=self.timeout_seconds if timeout is None else timeout,
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


def _as_path(raw: dict[str, Any], key: str) -> Path | None:
    """Read an optional path value, expanding ``~``.

    Args:
        raw: Parsed TOML table.
        key: Key to read.

    Returns:
        The expanded path, or None when absent.
    """
    value = as_str(raw, key, None)
    return Path(value).expanduser() if value else None


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


def product_overrides(raw: dict[str, Any], table: str) -> dict[str, Any]:
    """Read the connection values a product's table sets for itself.

    Args:
        raw: The product's parsed table.
        table: Name of the table, for error messages.

    Returns:
        Keyword arguments for the ``ProductSettings`` fields.

    Raises:
        ConfigError: If a value is invalid.
    """
    return {
        "base_url": _base_url(raw, f"{table}.base_url"),
        "ca_bundle": _as_path(raw, "ca_bundle"),
        "cookies": (
            _parse_cookies(raw["cookies"], f"{table}.cookies") if "cookies" in raw else None
        ),
        "timeout_seconds": _timeout(raw, f"{table}.timeout_seconds"),
    }


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
    products: dict[str, ProductSettings] = {}
    for name, table in raw.items():
        if name in _TOP_LEVEL_TABLES or name in _PENDING_TABLES:
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
    settings = Settings(
        base_url=_base_url(raw, "base_url"),
        ca_bundle=_as_path(raw, "ca_bundle"),
        dry_run=as_bool(raw, "dry_run", True),
        cookies=_parse_cookies(raw.get("cookies", {})),
        token_env_file=_as_path(raw, "token_env_file"),
        timeout_seconds=30.0 if timeout is None else timeout,
        log_level=log_level_name(as_str(raw, "log_level", None) or DEFAULT_LOG_LEVEL),
        products=products,
    )
    for product in products.values():
        settings.connection_for(product)
    return settings


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
        token_env_file: Override for ``token_env_file``; None keeps the file value.
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

    def with_extra[P: ProductSettings](product: P) -> P:
        if product.cookies is None:
            return product
        return replace(product, cookies={**product.cookies, **extra})

    return replace(
        settings,
        dry_run=settings.dry_run if dry_run is None else dry_run,
        cookies={**settings.cookies, **extra},
        products={name: with_extra(product) for name, product in settings.products.items()},
        log_level=settings.log_level if log_level is None else log_level_name(log_level),
        token_env_file=(
            settings.token_env_file if token_env_file is None else token_env_file.expanduser()
        ),
    )
