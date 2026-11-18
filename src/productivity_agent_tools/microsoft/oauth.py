"""Redeeming codes and refresh tokens for access tokens at the gov-cloud token endpoint.

The client is a Single-Page App, so AAD only redeems its authorization codes and
refresh tokens for a request that looks like a browser cross-origin call: an
``Origin`` header matching a registered redirect URI, which is why that header
is sent. The login host has a public certificate, so this uses the system trust
store, not the organization's internal CA bundle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
import structlog

from productivity_agent_tools.errors import ApiError, AuthenticationError, ConnectivityError
from productivity_agent_tools.transport import build_ssl_context

_log = structlog.get_logger(__name__)

RESEED_ADVICE: str = "Run 'productivity-agent-tools login microsoft' to sign in again."
SIGN_IN_SCOPES: tuple[str, ...] = ("openid", "profile", "offline_access")
"""Added to the configured scope: a refresh token, and an ID token naming the user."""


@dataclass(frozen=True, slots=True, kw_only=True)
class RedeemConfig:
    """The non-secret parameters needed to redeem a refresh token.

    Attributes:
        client_id: The OAuth client the token belongs to.
        tenant: Directory the token endpoint is scoped to (GUID or ``organizations``).
        authority: Login host, such as ``login.microsoftonline.us``.
        origin: ``Origin`` header required for the SPA cross-origin redemption.
        redirect_uri: Redirect URI sent in the body, as MSAL.js does.
        scope: Space-separated scope requested.
    """

    client_id: str
    tenant: str
    authority: str
    origin: str
    redirect_uri: str
    scope: str

    @property
    def token_endpoint(self) -> str:
        """The OAuth v2 token endpoint URL."""
        return f"https://{self.authority}/{self.tenant}/oauth2/v2.0/token"


@dataclass(frozen=True, slots=True, kw_only=True)
class TokenGrant:
    """An access token and the (rotated) refresh token that came back with it.

    Attributes:
        access_token: The bearer access token. Excluded from ``repr``.
        refresh_token: The refresh token to use next time; AAD rotates it on each use.
            Excluded from ``repr``.
        expires_at: When the access token stops being valid, in UTC.
        scope: The scope string the token was granted.
        id_token: The OpenID Connect ID token, when the sign-in asked for one; empty
            otherwise. Excluded from ``repr``.
    """

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: datetime
    scope: str
    id_token: str = field(default="", repr=False)

    def authorization(self) -> str:
        """Return the value for the ``Authorization`` header."""
        return f"Bearer {self.access_token}"

    def expired(self, now: datetime, *, skew_seconds: float = 60.0) -> bool:
        """Tell whether the access token is expired, or within ``skew_seconds`` of it.

        Args:
            now: The current time, in UTC.
            skew_seconds: Treat the token as expired this many seconds early, to
                cover clock skew and the time a request takes.

        Returns:
            True if the token should be refreshed before use.
        """
        return now >= self.expires_at - timedelta(seconds=skew_seconds)


def _grant_from_response(
    data: dict[str, object], fallback_refresh: str, now: datetime
) -> TokenGrant:
    """Build a grant from the token endpoint's JSON response.

    Args:
        data: The parsed JSON body.
        fallback_refresh: The refresh token just used, kept if the response rotates none.
        now: The current time, used to turn ``expires_in`` into an absolute time.

    Returns:
        The grant.

    Raises:
        ApiError: If the response has no access token.
    """
    access = data.get("access_token")
    if not isinstance(access, str) or not access:
        raise ApiError(200, "The token endpoint returned no access token.")
    rotated = data.get("refresh_token")
    refresh = rotated if isinstance(rotated, str) and rotated else fallback_refresh
    expires_in = data.get("expires_in")
    seconds = float(expires_in) if isinstance(expires_in, int | float) else 3600.0
    scope = data.get("scope")
    id_token = data.get("id_token")
    return TokenGrant(
        access_token=access,
        refresh_token=refresh,
        expires_at=now + timedelta(seconds=seconds),
        scope=scope if isinstance(scope, str) else "",
        id_token=id_token if isinstance(id_token, str) else "",
    )


def with_scopes(scope: str, extra: tuple[str, ...]) -> str:
    """Add scopes to a space-separated scope string, skipping any already present.

    Args:
        scope: The configured scope.
        extra: Scopes to put in front of it when missing.

    Returns:
        The combined scope string.
    """
    present = scope.split()
    return " ".join([*(name for name in extra if name not in present), *present])


def _raise_for_error(response: httpx.Response, refused: str) -> None:
    """Turn a non-200 token response into a plain-language error.

    Args:
        response: The token endpoint's response.
        refused: What was refused, for the message, such as ``"the refresh"``.

    Raises:
        AuthenticationError: If the grant is no longer usable (sign in again).
        ApiError: For any other refusal.
    """
    try:
        body = response.json()
    except ValueError:
        body = {}
    error = str(body.get("error", "")) if isinstance(body, dict) else ""
    description = str(body.get("error_description", "")) if isinstance(body, dict) else ""
    first_line = description.splitlines()[0] if description else f"HTTP {response.status_code}"
    _log.warning("ms_token_refused", error=error, status=response.status_code)
    if error == "invalid_grant" or "AADSTS70043" in description:
        raise AuthenticationError(
            f"The token endpoint refused {refused}: {first_line}. The Microsoft sign-in is "
            f"expired or no longer valid. {RESEED_ADVICE}"
        )
    raise ApiError(response.status_code, f"The token endpoint refused {refused}: {first_line}")


async def _post_token(
    config: RedeemConfig,
    form: dict[str, str],
    *,
    refused: str,
    fallback_refresh: str,
    now: datetime | None,
    timeout_seconds: float,
    transport: httpx.AsyncBaseTransport | None,
) -> TokenGrant:
    """Post one grant to the token endpoint, as the browser would, and read the answer.

    Args:
        config: The non-secret parameters (endpoint, ``Origin``).
        form: The grant's form fields.
        refused: What is being redeemed, for error messages.
        fallback_refresh: Refresh token to keep if the response rotates none.
        now: The current time in UTC; defaults to the real clock. Used for the expiry.
        timeout_seconds: Per-request timeout.
        transport: Replacement transport, used by tests.

    Returns:
        The new grant.

    Raises:
        AuthenticationError: If the grant is expired or invalid (sign in again).
        ApiError: If the endpoint refuses the request for another reason.
        ConnectivityError: If the login host cannot be reached.
    """
    moment = now or datetime.now(UTC)
    # The login host is public, so the system trust store verifies it, not the org CA.
    verify = build_ssl_context(None) if transport is None else True
    async with httpx.AsyncClient(
        timeout=timeout_seconds, verify=verify, transport=transport
    ) as http:
        try:
            response = await http.post(
                config.token_endpoint, data=form, headers={"Origin": config.origin}
            )
        except httpx.HTTPError as exc:
            raise ConnectivityError(
                f"Could not reach the Microsoft login host {config.authority} "
                f"({type(exc).__name__})."
            ) from exc
    if response.status_code != 200:
        _raise_for_error(response, refused)
    try:
        data = response.json()
    except ValueError as exc:
        raise ApiError(
            response.status_code, "The token endpoint returned a non-JSON body."
        ) from exc
    if not isinstance(data, dict):
        raise ApiError(response.status_code, "The token endpoint returned an unexpected body.")
    return _grant_from_response(data, fallback_refresh, moment)


async def redeem_refresh_token(
    config: RedeemConfig,
    refresh_token: str,
    *,
    now: datetime | None = None,
    timeout_seconds: float = 30.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> TokenGrant:
    """Redeem a refresh token for a new access token (and rotated refresh token).

    Args:
        config: The non-secret redemption parameters.
        refresh_token: The refresh token to redeem.
        now: The current time in UTC; defaults to the real clock. Used for the expiry.
        timeout_seconds: Per-request timeout.
        transport: Replacement transport, used by tests.

    Returns:
        The new grant.

    Raises:
        AuthenticationError: If the refresh token is expired or invalid (re-seed needed).
        ApiError: If the endpoint refuses the request for another reason.
        ConnectivityError: If the login host cannot be reached.
    """
    form = {
        "grant_type": "refresh_token",
        "client_id": config.client_id,
        "scope": with_scopes(config.scope, ("offline_access",)),
        "refresh_token": refresh_token,
        "redirect_uri": config.redirect_uri,
    }
    return await _post_token(
        config,
        form,
        refused="the refresh",
        fallback_refresh=refresh_token,
        now=now,
        timeout_seconds=timeout_seconds,
        transport=transport,
    )


async def redeem_authorization_code(
    config: RedeemConfig,
    code: str,
    verifier: str,
    *,
    now: datetime | None = None,
    timeout_seconds: float = 30.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> TokenGrant:
    """Redeem the authorization code a browser sign-in returned, with its PKCE verifier.

    Args:
        config: The non-secret parameters; ``redirect_uri`` must be the one the code
            was issued to.
        code: The authorization code from the redirect.
        verifier: The PKCE code verifier whose challenge the sign-in was started with.
        now: The current time in UTC; defaults to the real clock. Used for the expiry.
        timeout_seconds: Per-request timeout.
        transport: Replacement transport, used by tests.

    Returns:
        The grant, with the refresh token to store and, if asked for, an ID token.

    Raises:
        AuthenticationError: If the code is expired, used, or does not match the verifier.
        ApiError: If the endpoint refuses the request for another reason, or issues no
            refresh token.
        ConnectivityError: If the login host cannot be reached.
    """
    form = {
        "grant_type": "authorization_code",
        "client_id": config.client_id,
        "scope": with_scopes(config.scope, SIGN_IN_SCOPES),
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": config.redirect_uri,
    }
    grant = await _post_token(
        config,
        form,
        refused="the sign-in code",
        fallback_refresh="",
        now=now,
        timeout_seconds=timeout_seconds,
        transport=transport,
    )
    if not grant.refresh_token:
        raise ApiError(200, "The token endpoint issued no refresh token for the sign-in.")
    return grant
