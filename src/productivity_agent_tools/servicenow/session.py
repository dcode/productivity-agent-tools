"""The browser session ServiceNow requests are authenticated with, and where it is kept.

ServiceNow sits behind single sign-on, so there is no token to issue. What
authenticates an API call is what the browser holds after signing in: the
session cookies, plus the page's cross-site request token sent back as
``X-UserToken``. ``login servicenow`` collects the pair and stores it in the
system keychain; requests read it from there every time, so signing in again
is picked up by a server that is already running.
"""

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import keyring
import keyring.errors

from productivity_agent_tools.credentials import Credential
from productivity_agent_tools.errors import AuthenticationError
from productivity_agent_tools.tokens import KEYRING_SERVICE

SIGN_IN_ADVICE: str = "Run 'productivity-agent-tools login servicenow' in a terminal."


@dataclass(frozen=True, slots=True)
class BrowserSession:
    """What a signed-in browser sends: its cookies and the page's request token.

    Attributes:
        cookie: Value of the ``Cookie`` header. Excluded from ``repr``.
        user_token: Value of the ``X-UserToken`` header. Excluded from ``repr``.
        saved_at: When the session was collected, as an ISO 8601 time in UTC.
    """

    cookie: str = field(repr=False)
    user_token: str = field(repr=False)
    saved_at: str

    @classmethod
    def collected_now(cls, cookie: str, user_token: str) -> BrowserSession:
        """Create a session stamped with the current time.

        Args:
            cookie: Value of the ``Cookie`` header.
            user_token: Value of the ``X-UserToken`` header.

        Returns:
            The session.
        """
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        return cls(cookie=cookie, user_token=user_token, saved_at=stamp)

    def headers(self) -> dict[str, str]:
        """Return the headers that authenticate a request with this session."""
        return {"Cookie": self.cookie, "X-UserToken": self.user_token}

    def dumps(self) -> str:
        """Serialize the session for the keychain."""
        return json.dumps(
            {"cookie": self.cookie, "user_token": self.user_token, "saved_at": self.saved_at}
        )

    @classmethod
    def loads(cls, stored: str) -> BrowserSession | None:
        """Read a session back from its keychain form.

        Args:
            stored: The stored text.

        Returns:
            The session, or None if the text is not one (a token stored by mistake, say).
        """
        try:
            data = json.loads(stored)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        values = [data.get(key) for key in ("cookie", "user_token", "saved_at")]
        if not all(isinstance(value, str) and value for value in values):
            return None
        return cls(cookie=values[0], user_token=values[1], saved_at=values[2])


async def load_session(name: str) -> BrowserSession | None:
    """Read the stored session from the system keychain.

    Args:
        name: Name the session is stored under, such as ``"servicenow"``.

    Returns:
        The session, or None if none is stored or the keychain is unavailable.
    """
    try:
        stored = await asyncio.to_thread(keyring.get_password, KEYRING_SERVICE, name)
    except keyring.errors.KeyringError:
        return None
    return BrowserSession.loads(stored) if stored else None


async def store_session(name: str, session: BrowserSession) -> None:
    """Save a session in the system keychain, replacing any earlier one.

    Args:
        name: Name to store it under, such as ``"servicenow"``.
        session: The session to store.
    """
    await asyncio.to_thread(keyring.set_password, KEYRING_SERVICE, name, session.dumps())


async def _required_session(name: str) -> BrowserSession:
    """Read the stored session, or explain how to get one.

    Args:
        name: Name the session is stored under.

    Returns:
        The session.

    Raises:
        AuthenticationError: If no session is stored.
    """
    session = await load_session(name)
    if session is None:
        raise AuthenticationError(f"No ServiceNow browser session is saved. {SIGN_IN_ADVICE}")
    return session


async def browser_session(name: str, env_file: Path | None) -> Credential:
    """Resolve the stored browser session as a product's credential.

    The provider reads the keychain before every request rather than keeping
    the session it first found, so a new sign-in takes effect at once.

    Args:
        name: Name the session is stored under, such as ``"servicenow"``.
        env_file: Unused; a browser session is never read from an env file.

    Returns:
        The credential, described by when the session was saved.

    Raises:
        AuthenticationError: If no session is stored yet.
    """
    del env_file
    found = await _required_session(name)

    async def provide() -> Mapping[str, str]:
        return (await _required_session(name)).headers()

    return Credential(
        headers=provide,
        source=f"the system keychain, signed in at {found.saved_at}",
        label="browser session",
    )
