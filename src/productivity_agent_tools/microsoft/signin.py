"""Signing in to Microsoft through the browser, and storing the refresh token it yields.

This follows ``login servicenow``: the user signs in exactly as they always do,
in a browser window this command opens on a temporary profile, so single
sign-on, a smart card and its PIN stay between the user and the browser. The
tool never attaches to the page; it only asks the browser where its tabs are.

What differs is what is collected. The window opens on the sign-in service's
authorization page for the configured client (the webmail single-page app),
started with a PKCE challenge whose verifier only this process holds. When the
user is in, the service sends the tab to the client's redirect address with an
authorization code in the fragment. The tool reads that address from the tab
list, redeems the code with the verifier (as the single-page app would, with its
``Origin``), and stores the refresh token that comes back, exactly as seeding a
pasted one does. The code is single use and worthless without the verifier, so
the redirect page itself, if it loads, can do nothing with it.
"""

import asyncio
import base64
import binascii
import hashlib
import json
import secrets
import time
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import structlog

from productivity_agent_tools.browser import Browser, BrowserError, find_browser
from productivity_agent_tools.errors import AgentToolsError, AuthenticationError
from productivity_agent_tools.microsoft.credentials import Clock, Storer
from productivity_agent_tools.microsoft.oauth import (
    SIGN_IN_SCOPES,
    RedeemConfig,
    TokenGrant,
    redeem_authorization_code,
    with_scopes,
)
from productivity_agent_tools.microsoft.session import (
    TOKEN_NAME,
    MicrosoftSession,
    store_session,
)
from productivity_agent_tools.microsoft.settings import MicrosoftSettings, redeem_config

_log = structlog.get_logger(__name__)

POLL_SECONDS: float = 1.0


class SignInBrowser(Protocol):
    """What the sign-in needs from a browser; ``Browser`` is the real one."""

    def version(self) -> str:
        """Return the browser's product and version."""
        ...

    def watch_targets(self) -> None:
        """Ask the browser to report each tab's change of address."""
        ...

    def page_urls(self) -> list[str]:
        """List the full address of every tab, and of any passed through since last asked."""
        ...


type BrowserLauncher = Callable[[str, str], AbstractContextManager[SignInBrowser]]
"""Starts a browser from its binary path and the address to open."""


class CodeRedeemer(Protocol):
    """Redeems an authorization code; the real one is ``redeem_authorization_code``."""

    async def __call__(
        self,
        config: RedeemConfig,
        code: str,
        verifier: str,
        *,
        now: datetime | None = None,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> TokenGrant:
        """Redeem the code for a grant."""
        ...


def _b64url(raw: bytes) -> str:
    """Encode bytes as unpadded base64url, as PKCE and JWTs use."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@dataclass(frozen=True, slots=True)
class Challenge:
    """One sign-in attempt's secrets: the PKCE verifier and the state it is matched by.

    Attributes:
        verifier: The PKCE code verifier, sent only when redeeming. Excluded from ``repr``.
        state: Random value the redirect must carry back, so a stray code is never used.
    """

    verifier: str = field(repr=False)
    state: str

    @classmethod
    def new(cls) -> Challenge:
        """Create a fresh verifier and state from the system's secure random source."""
        return cls(verifier=_b64url(secrets.token_bytes(32)), state=secrets.token_urlsafe(16))

    @property
    def code_challenge(self) -> str:
        """The S256 challenge sent to the authorization page."""
        return _b64url(hashlib.sha256(self.verifier.encode("ascii")).digest())


def authorize_url(config: RedeemConfig, challenge: Challenge, login_hint: str | None = None) -> str:
    """Build the authorization page address the browser opens on.

    Args:
        config: The client, tenant, login host, redirect address and scope.
        challenge: The attempt's PKCE challenge and state.
        login_hint: Account to pre-fill on the sign-in page, if known.

    Returns:
        The address.
    """
    params = {
        "client_id": config.client_id,
        "response_type": "code",
        "redirect_uri": config.redirect_uri,
        "response_mode": "fragment",
        "scope": with_scopes(config.scope, SIGN_IN_SCOPES),
        "state": challenge.state,
        "code_challenge": challenge.code_challenge,
        "code_challenge_method": "S256",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"https://{config.authority}/{config.tenant}/oauth2/v2.0/authorize?{urlencode(params)}"


def _same_page(url: str, redirect_uri: str) -> bool:
    """Tell whether an address is the redirect page, ignoring its query and fragment."""
    seen, wanted = urlsplit(url), urlsplit(redirect_uri)
    return (seen.scheme, seen.netloc.lower(), seen.path or "/") == (
        wanted.scheme,
        wanted.netloc.lower(),
        wanted.path or "/",
    )


def read_redirect(url: str, redirect_uri: str, state: str) -> str | None:
    """Pull the authorization code out of a tab's address, if it is our redirect.

    Args:
        url: A tab's full address.
        redirect_uri: The redirect address the sign-in was started with.
        state: The attempt's state; a redirect carrying any other is ignored.

    Returns:
        The code, or None if the address is not this attempt's redirect.

    Raises:
        AuthenticationError: If the redirect reports that the sign-in failed.
    """
    if not _same_page(url, redirect_uri):
        return None
    parts = urlsplit(url)
    # The fragment is asked for; a query is read too, in case a policy changes the mode.
    params = {**parse_qs(parts.query), **parse_qs(parts.fragment)}
    if params.get("state", [""])[0] != state:
        return None
    if error := params.get("error", [""])[0]:
        description = params.get("error_description", [""])[0].splitlines()
        reason = description[0] if description else error
        raise AuthenticationError(f"The Microsoft sign-in failed: {reason}")
    return params.get("code", [""])[0] or None


def signed_in_as(id_token: str) -> str | None:
    """Read who signed in from an ID token, for the message shown to the user.

    The token came straight from the token endpoint over TLS, and is used for
    nothing but this message, so its signature is not checked.

    Args:
        id_token: The ID token, or an empty string.

    Returns:
        The user's name and account, or None if the token says neither.
    """
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except IndexError, ValueError, binascii.Error:
        return None
    if not isinstance(claims, dict):
        return None
    name = claims.get("name")
    account = claims.get("preferred_username") or claims.get("upn")
    found = [item for item in (name, account) if isinstance(item, str) and item]
    if len(found) == 2:
        return f"{found[0]} ({found[1]})"
    return found[0] if found else None


def _find_code(urls: Iterable[str], config: RedeemConfig, state: str) -> str | None:
    """Return the first authorization code among the tabs' addresses, if any."""
    for url in urls:
        if code := read_redirect(url, config.redirect_uri, state):
            return code
    return None


async def sign_in(
    settings: MicrosoftSettings,
    *,
    name: str = TOKEN_NAME,
    login_hint: str | None = None,
    launch: BrowserLauncher = Browser,
    redeem: CodeRedeemer = redeem_authorization_code,
    store: Storer = store_session,
    now: Clock = lambda: datetime.now(UTC),
    poll_seconds: float = POLL_SECONDS,
    announce: Callable[[str], None] = print,
) -> str:
    """Open the browser, wait for the user to sign in, and store the refresh token.

    Args:
        settings: The ``[microsoft]`` settings (OAuth client, browser, how long to wait).
        name: Name to store the session under.
        login_hint: Account to pre-fill on the sign-in page, if given.
        launch: Starts the browser; replaced in tests.
        redeem: Redeems the authorization code; replaced in tests.
        store: Writes the session; replaced in tests.
        now: Returns the current time in UTC; replaced in tests.
        poll_seconds: Pause between looks at the browser.
        announce: Shows a progress line to the user.

    Returns:
        A sentence saying who signed in and where the token was stored.

    Raises:
        AgentToolsError: If the browser cannot be started or is closed, the sign-in
            fails or does not finish in time, or the code cannot be redeemed.
    """
    config = redeem_config(settings)
    challenge = Challenge.new()
    binary = find_browser(settings.browser, table="microsoft")
    announce(
        f"Opening a browser window on {config.authority}. Sign in there as usual; this waits "
        f"up to {settings.login_timeout_seconds:.0f} seconds."
    )
    deadline = time.monotonic() + settings.login_timeout_seconds
    with launch(binary, authorize_url(config, challenge, login_hint)) as browser:
        _log.debug("ms_sign_in_browser", browser=await asyncio.to_thread(browser.version))
        try:
            await asyncio.to_thread(browser.watch_targets)
        except BrowserError as exc:
            # Looking at the tabs is enough on its own; events only catch a fleeting address.
            _log.debug("ms_sign_in_no_target_events", reason=str(exc))
        while time.monotonic() < deadline:
            await asyncio.sleep(poll_seconds)
            urls = await asyncio.to_thread(browser.page_urls)
            if (code := _find_code(urls, config, challenge.state)) is None:
                continue
            _log.debug("ms_sign_in_code_received")
            grant = await redeem(
                config,
                code,
                challenge.verifier,
                now=now(),
                timeout_seconds=settings.timeout_seconds or 30.0,
            )
            session = MicrosoftSession.seeded_now(
                grant.refresh_token, config, window_hours=settings.sign_in_hours
            )
            await store(session, name)
            who = signed_in_as(grant.id_token) or "you"
            ends = session.window_ends_at()
            until = (
                f"until about {ends.isoformat(timespec='seconds')}"
                if ends is not None
                else "until the tenant's sign-in window ends"
            )
            return (
                f"Signed in to Microsoft as {who}. The refresh token is stored in the system "
                f"keychain and renews silently {until}; then run "
                "'productivity-agent-tools login microsoft' again. A running server picks it "
                "up when its current access token is next renewed (within the hour)."
            )
    raise AgentToolsError(
        f"The sign-in did not finish within {settings.login_timeout_seconds:.0f} seconds "
        "(no tab came back from the sign-in page). Nothing was stored."
    )
