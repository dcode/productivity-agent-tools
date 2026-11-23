"""The Microsoft refresh token and how to renew it, and where both are kept.

The secrets are the refresh token and the latest access token; the redemption
parameters beside them are not, but they are stored together so the stored
credential is self-contained: the resolver that builds the runtime provider is
handed only a name, so it reads everything it needs to renew from here. The
refresh token rotates on each use, so the provider writes the session back after
every redemption, with the new access token. Keeping the access token lets a
short-lived process (each ``call``, the smoke test, a sync run from a button)
reuse it for its hour instead of going to the login host every time.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import keyring
import keyring.errors

from productivity_agent_tools.microsoft.oauth import RESEED_ADVICE, RedeemConfig, TokenGrant
from productivity_agent_tools.tokens import KEYRING_SERVICE

TOKEN_NAME: str = "microsoft"
SEED_ADVICE: str = "Run 'productivity-agent-tools login microsoft' to sign in."

_CONFIG_FIELDS = ("client_id", "tenant", "authority", "origin", "redirect_uri", "scope")


@dataclass(frozen=True, slots=True)
class MicrosoftSession:
    """A refresh token, when it was obtained, and the parameters to renew it with.

    Attributes:
        refresh_token: The current refresh token. Excluded from ``repr``.
        obtained_at: When this refresh token was seeded or last rotated, ISO 8601 UTC.
        config: The non-secret parameters for redeeming it.
        signed_in_at: When the user last signed in (or seeded by hand), ISO 8601 UTC.
            Unlike ``obtained_at`` it survives rotation: the tenant's sign-in window
            counts from here.
        window_hours: How long the tenant lets a sign-in be renewed, from the
            ``[microsoft]`` ``sign_in_hours`` setting at sign-in; None when not set.
        access_token: The access token from the latest redemption, empty when there has
            been none since sign-in. Excluded from ``repr``.
        access_expires_at: When that access token expires, ISO 8601 UTC; empty with it.
    """

    refresh_token: str = field(repr=False)
    obtained_at: str
    config: RedeemConfig
    signed_in_at: str = ""
    window_hours: float | None = None
    access_token: str = field(default="", repr=False)
    access_expires_at: str = ""

    def __post_init__(self) -> None:
        """Default the sign-in time to when the token was obtained."""
        if not self.signed_in_at:
            object.__setattr__(self, "signed_in_at", self.obtained_at)

    @classmethod
    def seeded_now(
        cls, refresh_token: str, config: RedeemConfig, *, window_hours: float | None = None
    ) -> MicrosoftSession:
        """Create a session for a sign-in (or a hand seed) that happened now.

        Args:
            refresh_token: The refresh token to store.
            config: The redemption parameters.
            window_hours: How long the tenant lets the sign-in be renewed, if known.

        Returns:
            The session.
        """
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        return cls(
            refresh_token=refresh_token,
            obtained_at=stamp,
            config=config,
            signed_in_at=stamp,
            window_hours=window_hours,
        )

    def rotated(self, grant: TokenGrant) -> MicrosoftSession:
        """Return a copy holding a redemption's tokens, stamped now.

        The sign-in time and window are kept: rotating does not extend the sign-in.

        Args:
            grant: The latest redemption: its rotated refresh token and access token.

        Returns:
            The updated session.
        """
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        return dataclasses.replace(
            self,
            refresh_token=grant.refresh_token,
            obtained_at=stamp,
            access_token=grant.access_token,
            access_expires_at=grant.expires_at.isoformat(timespec="seconds"),
        )

    def cached_grant(self, now: datetime, *, skew_seconds: float) -> TokenGrant | None:
        """Return the stored access token as a grant, if it is still good to use.

        Args:
            now: The current time, in UTC.
            skew_seconds: Treat it as expired this many seconds early.

        Returns:
            The grant, or None when there is none or it is (nearly) expired.
        """
        if not (self.access_token and self.access_expires_at):
            return None
        try:
            expires_at = datetime.fromisoformat(self.access_expires_at)
        except ValueError:
            return None
        if expires_at.tzinfo is None:
            return None
        grant = TokenGrant(
            access_token=self.access_token,
            refresh_token=self.refresh_token,
            expires_at=expires_at,
            scope=self.config.scope,
        )
        return None if grant.expired(now, skew_seconds=skew_seconds) else grant

    def window_ends_at(self) -> datetime | None:
        """Estimate when the sign-in can no longer be renewed, if the window is known."""
        if self.window_hours is None:
            return None
        try:
            start = datetime.fromisoformat(self.signed_in_at)
        except ValueError:
            return None
        return start + timedelta(hours=self.window_hours)

    def describe(self, access_expires_at: datetime | None = None) -> str:
        """Say, for a status tool, when the user signed in and how long it should last.

        Args:
            access_expires_at: When the access token now in use expires, if one is.

        Returns:
            A sentence safe to show; it holds no secret.
        """
        parts = [f"the system keychain, signed in at {self.signed_in_at}"]
        if (ends := self.window_ends_at()) is not None:
            parts.append(
                f"usable until about {ends.isoformat(timespec='seconds')} "
                f"({self.window_hours:g} h sign-in window), then run "
                "'productivity-agent-tools login microsoft' again"
            )
        else:
            parts.append(
                "lasts as long as the tenant's sign-in policy allows (set sign_in_hours "
                "under [microsoft] to have it estimated)"
            )
        parts.append(f"refresh token last renewed at {self.obtained_at}")
        if access_expires_at is not None:
            stamp = access_expires_at.isoformat(timespec="seconds")
            parts.append(f"current access token valid until {stamp}")
        return "; ".join(parts)

    def dumps(self) -> str:
        """Serialize the session for the keychain."""
        config = {name: getattr(self.config, name) for name in _CONFIG_FIELDS}
        return json.dumps(
            {
                "refresh_token": self.refresh_token,
                "obtained_at": self.obtained_at,
                "signed_in_at": self.signed_in_at,
                "window_hours": self.window_hours,
                "config": config,
                "access_token": self.access_token,
                "access_expires_at": self.access_expires_at,
            }
        )

    @classmethod
    def loads(cls, stored: str) -> MicrosoftSession | None:
        """Read a session back from its keychain form.

        Args:
            stored: The stored text.

        Returns:
            The session, or None if the text is not a well-formed one.
        """
        try:
            data = json.loads(stored)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        refresh = data.get("refresh_token")
        obtained = data.get("obtained_at")
        config = data.get("config")
        if not (isinstance(refresh, str) and refresh and isinstance(obtained, str) and obtained):
            return None
        if not isinstance(config, dict):
            return None
        values = {name: config.get(name) for name in _CONFIG_FIELDS}
        if not all(isinstance(value, str) and value for value in values.values()):
            return None
        signed_in = data.get("signed_in_at")
        access = data.get("access_token")
        access_expires = data.get("access_expires_at")
        hours = data.get("window_hours")
        usable_hours = isinstance(hours, int | float) and not isinstance(hours, bool) and hours > 0
        return cls(
            refresh_token=refresh,
            obtained_at=obtained,
            config=RedeemConfig(**values),  # type: ignore[arg-type]
            # Sessions stored before these were kept have neither; fall back quietly.
            signed_in_at=signed_in if isinstance(signed_in, str) else "",
            window_hours=float(hours) if usable_hours else None,
            access_token=access if isinstance(access, str) else "",
            access_expires_at=access_expires if isinstance(access_expires, str) else "",
        )


async def load_session(name: str = TOKEN_NAME) -> MicrosoftSession | None:
    """Read the stored session from the system keychain.

    Args:
        name: Name the session is stored under.

    Returns:
        The session, or None if none is stored or the keychain is unavailable.
    """
    try:
        stored = await asyncio.to_thread(keyring.get_password, KEYRING_SERVICE, name)
    except keyring.errors.KeyringError:
        return None
    return MicrosoftSession.loads(stored) if stored else None


async def store_session(session: MicrosoftSession, name: str = TOKEN_NAME) -> None:
    """Save a session in the system keychain, replacing any earlier one.

    Args:
        session: The session to store.
        name: Name to store it under.
    """
    await asyncio.to_thread(keyring.set_password, KEYRING_SERVICE, name, session.dumps())


__all__ = [
    "RESEED_ADVICE",
    "SEED_ADVICE",
    "TOKEN_NAME",
    "MicrosoftSession",
    "load_session",
    "store_session",
]
