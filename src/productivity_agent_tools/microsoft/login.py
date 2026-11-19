"""Storing a Microsoft refresh token captured by hand (``set-token microsoft``).

``login microsoft`` (``signin.py``) normally collects the refresh token itself.
When it cannot run, the user signs in to webmail as usual, captures the token
response (or just its refresh token) from the browser, and stores it with
``set-token microsoft`` once per Conditional-Access sign-in window. Storing
verifies the token by redeeming it once, which also rotates it, so what is
stored is the rotated token, not the pasted one.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from productivity_agent_tools.errors import AgentToolsError
from productivity_agent_tools.microsoft.credentials import Clock, Redeemer, Storer
from productivity_agent_tools.microsoft.oauth import RedeemConfig, redeem_refresh_token
from productivity_agent_tools.microsoft.session import MicrosoftSession, store_session


def extract_refresh_token(response_text: str) -> str:
    """Pull the refresh token out of a captured token response, or a bare token.

    Args:
        response_text: Either the JSON token response the browser received, or
            the refresh token on its own.

    Returns:
        The refresh token.

    Raises:
        AgentToolsError: If the text is JSON but has no ``refresh_token`` field.
    """
    text = response_text.strip()
    if not text.startswith("{"):
        return text
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise AgentToolsError(f"The token response is not valid JSON: {exc}.") from exc
    token = data.get("refresh_token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token:
        raise AgentToolsError("The token response has no 'refresh_token' field.")
    return token


async def seed(
    config: RedeemConfig,
    refresh_token: str,
    *,
    verify: bool = True,
    window_hours: float | None = None,
    redeem: Redeemer = redeem_refresh_token,
    store: Storer = store_session,
    now: Clock = lambda: datetime.now(UTC),
) -> str:
    """Store the refresh token, by default verifying it by redeeming once first.

    Args:
        config: The redemption parameters to store alongside the token.
        refresh_token: The refresh token to seed.
        verify: When True, redeem once to confirm the token works; this rotates it,
            so the rotated token is what gets stored.
        window_hours: How long the tenant lets the sign-in be renewed, if known.
        redeem: Redeems the refresh token; replaced in tests.
        store: Writes the session; replaced in tests.
        now: Returns the current time in UTC; replaced in tests.

    Returns:
        A sentence to show the user.

    Raises:
        AuthenticationError: If verification finds the token already expired or invalid.
        ApiError, ConnectivityError: If the token endpoint refuses or cannot be reached.
    """
    if not verify:
        session = MicrosoftSession.seeded_now(refresh_token, config, window_hours=window_hours)
        await store(session, "microsoft")
        return (
            "Stored the Microsoft refresh token in the system keychain (not verified). "
            "The first request will confirm it."
        )
    grant = await redeem(config, refresh_token, now=now())
    session = MicrosoftSession.seeded_now(grant.refresh_token, config, window_hours=window_hours)
    await store(session, "microsoft")
    minutes = max(0, int((grant.expires_at - now()).total_seconds()) // 60)
    return (
        "Stored the Microsoft refresh token in the system keychain and verified it: it gave "
        f"an access token for scope '{grant.scope}', good for about {minutes} minutes. It "
        "renews silently until the tenant's sign-in window ends; then run "
        "'productivity-agent-tools login microsoft' again."
    )
