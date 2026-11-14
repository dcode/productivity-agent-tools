"""Redeeming a refresh token for an access token at the gov-cloud token endpoint.

The client is a Single-Page App, so AAD only redeems its refresh token for a
request that looks like a browser cross-origin call: an ``Origin`` header
matching a registered redirect URI, which is why that header is sent. The login
host has a public certificate, so this uses the system trust store, not the
organization's internal CA bundle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
import structlog

from productivity_agent_tools.errors import ApiError, AuthenticationError, ConnectivityError
from productivity_agent_tools.transport import build_ssl_context

_log = structlog.get_logger(__name__)

RESEED_ADVICE: str = (
    "Sign in to webmail again and run 'productivity-agent-tools login-microsoft' to "
    "re-seed the refresh token."
)


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
    """

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: datetime
    scope: str

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
    return TokenGrant(
        access_token=access,
        refresh_token=refresh,
        expires_at=now + timedelta(seconds=seconds),
        scope=scope if isinstance(scope, str) else "",
    )


def _raise_for_error(response: httpx.Response) -> None:
    """Turn a non-200 token response into a plain-language error.

    Args:
        response: The token endpoint's response.

    Raises:
        AuthenticationError: If the refresh token is no longer usable (re-seed needed).
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
            f"The Microsoft refresh token is expired or no longer valid. {RESEED_ADVICE}"
        )
    raise ApiError(response.status_code, f"The token endpoint refused the refresh: {first_line}")


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
    moment = now or datetime.now(UTC)
    has_offline = "offline_access" in config.scope.split()
    scope = config.scope if has_offline else f"offline_access {config.scope}"
    form = {
        "grant_type": "refresh_token",
        "client_id": config.client_id,
        "scope": scope,
        "refresh_token": refresh_token,
        "redirect_uri": config.redirect_uri,
    }
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
        _raise_for_error(response)
    try:
        data = response.json()
    except ValueError as exc:
        raise ApiError(
            response.status_code, "The token endpoint returned a non-JSON body."
        ) from exc
    if not isinstance(data, dict):
        raise ApiError(response.status_code, "The token endpoint returned an unexpected body.")
    return _grant_from_response(data, refresh_token, moment)
