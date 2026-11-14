"""The Microsoft refresh token and how to renew it, and where both are kept.

The only secret is the refresh token; the redemption parameters beside it are
not, but they are stored together so the stored credential is self-contained:
the resolver that builds the runtime provider is handed only a name, so it reads
everything it needs to renew from here. The refresh token rotates on each use,
so the provider writes the session back after every redemption.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

import keyring
import keyring.errors

from productivity_agent_tools.microsoft.oauth import RESEED_ADVICE, RedeemConfig
from productivity_agent_tools.tokens import KEYRING_SERVICE

TOKEN_NAME: str = "microsoft"
SEED_ADVICE: str = "Run 'productivity-agent-tools login-microsoft' to seed it."

_CONFIG_FIELDS = ("client_id", "tenant", "authority", "origin", "redirect_uri", "scope")


@dataclass(frozen=True, slots=True)
class MicrosoftSession:
    """A refresh token, when it was obtained, and the parameters to renew it with.

    Attributes:
        refresh_token: The current refresh token. Excluded from ``repr``.
        obtained_at: When this refresh token was seeded or last rotated, ISO 8601 UTC.
        config: The non-secret parameters for redeeming it.
    """

    refresh_token: str = field(repr=False)
    obtained_at: str
    config: RedeemConfig

    @classmethod
    def seeded_now(cls, refresh_token: str, config: RedeemConfig) -> MicrosoftSession:
        """Create a session stamped with the current time.

        Args:
            refresh_token: The refresh token to store.
            config: The redemption parameters.

        Returns:
            The session.
        """
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        return cls(refresh_token=refresh_token, obtained_at=stamp, config=config)

    def rotated(self, refresh_token: str) -> MicrosoftSession:
        """Return a copy holding a freshly rotated refresh token, stamped now.

        Args:
            refresh_token: The rotated refresh token from the latest redemption.

        Returns:
            The updated session.
        """
        return MicrosoftSession.seeded_now(refresh_token, self.config)

    def dumps(self) -> str:
        """Serialize the session for the keychain."""
        config = {name: getattr(self.config, name) for name in _CONFIG_FIELDS}
        return json.dumps(
            {"refresh_token": self.refresh_token, "obtained_at": self.obtained_at, "config": config}
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
        return cls(
            refresh_token=refresh,
            obtained_at=obtained,
            config=RedeemConfig(**values),  # type: ignore[arg-type]
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
