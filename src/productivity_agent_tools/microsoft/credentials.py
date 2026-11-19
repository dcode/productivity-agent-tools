"""The runtime credential: a provider that keeps a live access token on hand.

The access token lasts about an hour, so the provider caches it in memory and
hands it out until it is nearly expired. When it needs a new one it reloads the
session from the keychain (so a sign-in done while the server runs is picked up),
redeems the refresh token, and writes the rotated refresh token straight back. A
lock makes concurrent requests share one redemption rather than racing to spend —
and so rotate — the same refresh token.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Protocol

import httpx

from productivity_agent_tools.credentials import Credential
from productivity_agent_tools.errors import AuthenticationError
from productivity_agent_tools.microsoft.oauth import (
    RedeemConfig,
    TokenGrant,
    redeem_refresh_token,
)
from productivity_agent_tools.microsoft.session import (
    SEED_ADVICE,
    TOKEN_NAME,
    MicrosoftSession,
    load_session,
    store_session,
)
from productivity_agent_tools.tokens import TokenSources

NO_CREDENTIAL = f"No Microsoft credential is stored. {SEED_ADVICE}"


class Redeemer(Protocol):
    """Redeems a refresh token; the real one is ``redeem_refresh_token``."""

    async def __call__(
        self,
        config: RedeemConfig,
        refresh_token: str,
        *,
        now: datetime | None = None,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> TokenGrant:
        """Redeem the refresh token for a new grant."""
        ...


type Loader = Callable[[str], Awaitable[MicrosoftSession | None]]
type Storer = Callable[[MicrosoftSession, str], Awaitable[None]]
type Clock = Callable[[], datetime]


class MicrosoftCredential:
    """A ``HeaderProvider`` that mints and renews the Outlook access token."""

    def __init__(
        self,
        *,
        name: str = TOKEN_NAME,
        redeem: Redeemer = redeem_refresh_token,
        load: Loader = load_session,
        store: Storer = store_session,
        now: Clock = lambda: datetime.now(UTC),
        skew_seconds: float = 60.0,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Create the provider.

        Args:
            name: Keychain name the session is stored under.
            redeem: Redeems the refresh token; replaced in tests.
            load: Reads the stored session; replaced in tests.
            store: Writes the rotated session; replaced in tests.
            now: Returns the current time in UTC; replaced in tests.
            skew_seconds: Refresh the access token this many seconds before it expires.
            timeout_seconds: Per-request timeout for the redemption.
            transport: Replacement transport for the redemption, used by tests.
        """
        self._name = name
        self._redeem = redeem
        self._load = load
        self._store = store
        self._now = now
        self._skew = skew_seconds
        self._timeout = timeout_seconds
        self._transport = transport
        self._grant: TokenGrant | None = None
        self._lock = asyncio.Lock()

    def _fresh_header(self) -> Mapping[str, str] | None:
        """Return the cached access token's header if it is still fresh, else None."""
        grant = self._grant
        if grant is not None and not grant.expired(self._now(), skew_seconds=self._skew):
            return {"Authorization": grant.authorization()}
        return None

    async def __call__(self) -> Mapping[str, str]:
        """Return the ``Authorization`` header, renewing the access token if needed.

        Returns:
            The bearer header for a request made now.

        Raises:
            AuthenticationError: If nothing is seeded, or the refresh token can no
                longer be redeemed (the user must sign in again and re-seed).
            ApiError, ConnectivityError: If the token endpoint refuses or cannot be reached.
        """
        if header := self._fresh_header():
            return header
        async with self._lock:
            # Another request may have renewed the token while we waited for the lock.
            if header := self._fresh_header():
                return header
            session = await self._load(self._name)
            if session is None:
                raise AuthenticationError(NO_CREDENTIAL)
            grant = await self._redeem(
                session.config,
                session.refresh_token,
                now=self._now(),
                timeout_seconds=self._timeout,
                transport=self._transport,
            )
            # A 'login microsoft' may have stored a new sign-in while this redemption was
            # in flight. Writing back now would replace it with the old sign-in's rotated
            # token, so keep the new one; this grant is still good until it expires.
            latest = await self._load(self._name)
            if latest is None or latest.refresh_token == session.refresh_token:
                await self._store(session.rotated(grant.refresh_token), self._name)
            self._grant = grant
            return {"Authorization": grant.authorization()}

    async def describe(self) -> str:
        """Describe the stored sign-in as it is now, for the status tools.

        Reads the keychain on every call, so a sign-in done while the server runs
        shows at once.

        Returns:
            When the user signed in, how long that should last, and when the access
            token now in use expires.
        """
        session = await self._load(self._name)
        if session is None:
            return NO_CREDENTIAL
        grant = self._grant
        return session.describe(grant.expires_at if grant is not None else None)


async def microsoft_token(name: str, sources: TokenSources) -> Credential:
    """Resolve the stored Microsoft session as a product's credential.

    Args:
        name: Name the session is stored under, such as ``"microsoft"``.
        sources: Unused; the refresh-token session is only ever in the keychain.

    Returns:
        The credential, described by when the session was seeded.

    Raises:
        AuthenticationError: If no session is seeded yet.
    """
    del sources
    session = await load_session(name)
    if session is None:
        raise AuthenticationError(NO_CREDENTIAL)
    provider = MicrosoftCredential(name=name)
    return Credential(
        headers=provider,
        source=f"the system keychain, signed in at {session.signed_in_at}",
        label="OAuth token",
        describe=provider.describe,
    )
