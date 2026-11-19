"""The Microsoft sign-in settings, read from the ``[microsoft]`` config table.

``[microsoft]`` is not a served product: it says how ``login microsoft`` signs
in and how the credential renews. The OAuth parameters identify the first-party
client a browser sign-in mints tokens from, and the login host. They are
environment-specific, so the core carries no defaults: every one must be set in
the config. A ready-made block for the DoD / OWA-webmail environment is in
``contrib/microsoft-dod.toml`` to copy in. The Outlook REST host the data comes
from is set on the Outlook products (usually through ``[group.microsoft]``), not
here; a ``base_url`` in this table is accepted and ignored.
"""

from __future__ import annotations

import asyncio
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from productivity_agent_tools.config import (
    ProductSettings,
    as_str,
    login_timeout,
    product_overrides,
)
from productivity_agent_tools.errors import ConfigError
from productivity_agent_tools.microsoft.oauth import RedeemConfig

CONTRIB_HINT: str = (
    "Add a [microsoft] table to the config (copy contrib/microsoft-dod.toml for the "
    "DoD / OWA-webmail values, or set client_id, tenant, authority, origin, redirect_uri "
    "and scope for your own)."
)


@dataclass(frozen=True, slots=True, kw_only=True)
class MicrosoftSettings(ProductSettings):
    """The ``[microsoft]`` table: the OAuth client and browser that ``login microsoft`` uses.

    Attributes:
        client_id: The OAuth client (the first-party app the browser signs in as).
        tenant: The directory the token endpoint is scoped to (a GUID, or ``organizations``).
        authority: Login host, such as ``login.microsoftonline.us`` (gov) or ``.com``.
        origin: ``Origin`` header sent when redeeming the refresh token; the single-page
            client type is only redeemed for a cross-origin request matching a registered URI.
        redirect_uri: Redirect URI sent in the body, as the browser's MSAL.js does.
        scope: Space-separated scope requested, usually ``<resource>/.default``.
        browser: Browser that ``login microsoft`` starts: a path or a command name. None
            looks for Chrome, then Edge.
        login_timeout_seconds: How long ``login microsoft`` waits for the sign-in to finish.
        sign_in_hours: How long the tenant lets one sign-in be renewed (its Conditional
            Access sign-in frequency), used only to tell the user when to sign in again.
            None when unknown.
    """

    client_id: str
    tenant: str
    authority: str
    origin: str
    redirect_uri: str
    scope: str
    browser: str | None = None
    login_timeout_seconds: float = 300.0
    sign_in_hours: float | None = None


def _required_str(raw: dict[str, Any], key: str) -> str:
    """Read a string setting that must be present and non-blank.

    Args:
        raw: Parsed ``[microsoft]`` table.
        key: Key to read.

    Returns:
        The value, stripped.

    Raises:
        ConfigError: If the value is absent, blank, or not a string.
    """
    value = (as_str(raw, key, None) or "").strip()
    if not value:
        raise ConfigError(f"Config key 'microsoft.{key}' is required. {CONTRIB_HINT}")
    return value


def _sign_in_hours(raw: dict[str, Any]) -> float | None:
    """Read the optional length of the tenant's sign-in window.

    Args:
        raw: Parsed ``[microsoft]`` table.

    Returns:
        The hours, or None when absent.

    Raises:
        ConfigError: If the value is not a positive number.
    """
    value = raw.get("sign_in_hours")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ConfigError("Config key 'microsoft.sign_in_hours' must be a positive number.")
    return float(value)


def redeem_config(settings: MicrosoftSettings) -> RedeemConfig:
    """Extract the refresh-redemption parameters from the Microsoft settings.

    Args:
        settings: The parsed Microsoft settings.

    Returns:
        The non-secret parameters for redeeming a refresh token.
    """
    return RedeemConfig(
        client_id=settings.client_id,
        tenant=settings.tenant,
        authority=settings.authority,
        origin=settings.origin,
        redirect_uri=settings.redirect_uri,
        scope=settings.scope,
    )


def parse_microsoft_settings(raw: dict[str, Any]) -> MicrosoftSettings:
    """Build Microsoft settings from the ``[microsoft]`` table.

    Args:
        raw: Parsed ``[microsoft]`` table.

    Returns:
        The Microsoft settings.

    Raises:
        ConfigError: If a required key is absent or a value has the wrong type.
    """
    return MicrosoftSettings(
        client_id=_required_str(raw, "client_id"),
        tenant=_required_str(raw, "tenant"),
        authority=_required_str(raw, "authority"),
        origin=_required_str(raw, "origin"),
        redirect_uri=_required_str(raw, "redirect_uri"),
        scope=_required_str(raw, "scope"),
        browser=(as_str(raw, "browser", None) or "").strip() or None,
        login_timeout_seconds=login_timeout(raw, "microsoft", 300.0),
        sign_in_hours=_sign_in_hours(raw),
        **product_overrides(raw, "microsoft"),
    )


async def load_microsoft_settings(path: Path) -> MicrosoftSettings:
    """Read just the ``[microsoft]`` table from the config file.

    Used by ``login microsoft`` and ``set-token microsoft``, which sign in before a product is
    wired, so it reads its own table rather than the whole product registry.

    Args:
        path: Location of the TOML config file; ``~`` is expanded.

    Returns:
        The Microsoft settings.

    Raises:
        ConfigError: If the file is missing or invalid, or has no ``[microsoft]`` table.
    """
    resolved = path.expanduser()  # noqa: ASYNC240 - reads $HOME only, no file I/O
    try:
        data = await asyncio.to_thread(resolved.read_bytes)
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found at {resolved}.") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read config file {resolved}: {exc.strerror}.") from exc
    try:
        raw = tomllib.loads(data.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"Config file {resolved} is not valid TOML: {exc}.") from exc
    table = raw.get("microsoft")
    if not isinstance(table, dict):
        raise ConfigError(f"No [microsoft] table in {resolved}. {CONTRIB_HINT}")
    return parse_microsoft_settings(table)
