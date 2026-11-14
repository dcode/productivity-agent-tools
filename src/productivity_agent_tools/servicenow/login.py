"""Signing in to ServiceNow through the browser, and saving the session that results.

The user signs in exactly as they always do, in a browser window this
command opens; single sign-on, a smart card and its PIN stay between the
user and the browser. Once they are in, the command reads the instance's
cookies from the browser, fetches the page they landed on to find the
cross-site request token, proves the pair by reading the user's own record,
and stores it in the system keychain.
"""

import asyncio
import re
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from typing import Protocol

import httpx
import structlog

from productivity_agent_tools.config import Connection
from productivity_agent_tools.errors import AgentToolsError, ConnectivityError
from productivity_agent_tools.payload import objects, text, value
from productivity_agent_tools.servicenow.browser import Browser, BrowserCookie, find_browser
from productivity_agent_tools.servicenow.client import ME_PARAMS, USER_TABLE
from productivity_agent_tools.servicenow.session import BrowserSession, store_session
from productivity_agent_tools.servicenow.settings import ServiceNowSettings
from productivity_agent_tools.transport import build_ssl_context

_log = structlog.get_logger(__name__)

POLL_SECONDS: float = 2.0
TOKEN_PATTERN = re.compile(r"g_ck\s*[=:]\s*['\"]([0-9a-f]{32,})['\"]")


class SignInBrowser(Protocol):
    """What the sign-in needs from a browser; ``Browser`` is the real one."""

    def version(self) -> str:
        """Return the browser's product and version."""
        ...

    def page_paths(self, host: str) -> list[str]:
        """List the path and query of each open tab on a host."""
        ...

    def cookies(self, host: str) -> list[BrowserCookie]:
        """Read the cookies the browser would send to a host."""
        ...


type BrowserLauncher = Callable[[str, str], AbstractContextManager[SignInBrowser]]
"""Starts a browser from its binary path and the address to open."""


async def _attempt(
    http: httpx.AsyncClient, cookies: Sequence[BrowserCookie], paths: Sequence[str]
) -> tuple[BrowserSession | None, str]:
    """Try to turn the browser's current cookies into a working session.

    Args:
        http: Client for the instance; it follows no redirects and keeps no cookies.
        cookies: The cookies the browser holds for the instance.
        paths: Path and query of each tab open on the instance.

    Returns:
        The session and the signed-in user's name once it works; otherwise None and a
        note on how far the sign-in has got.
    """
    cookie = "; ".join(f"{item.name}={item.value}" for item in cookies)
    # Without the token the API refuses either way, but it says whether it knows the caller.
    knock = await http.get(USER_TABLE, params=ME_PARAMS, headers={"Cookie": cookie})
    http.cookies.clear()
    if knock.headers.get("x-is-logged-in", "").lower() != "true":
        return None, "the browser has not finished signing in"
    for path in paths:
        page = await http.get(path, headers={"Cookie": cookie, "Accept": "text/html"})
        http.cookies.clear()
        if match := TOKEN_PATTERN.search(page.text):
            session = BrowserSession.collected_now(cookie, match.group(1))
            me = await http.get(USER_TABLE, params=ME_PARAMS, headers=session.headers())
            http.cookies.clear()
            found = objects(value(me.json(), "result")) if me.status_code == 200 else []
            if found:
                who = text(found[0], "name") or text(found[0], "user_name") or "you"
                return session, who
            return None, f"the instance refused the collected session (HTTP {me.status_code})"
    return None, "signed in, but no page with a request token is open yet"


async def sign_in(
    connection: Connection,
    settings: ServiceNowSettings,
    *,
    name: str = "servicenow",
    launch: BrowserLauncher = Browser,
    transport: httpx.AsyncBaseTransport | None = None,
    poll_seconds: float = POLL_SECONDS,
    announce: Callable[[str], None] = print,
) -> str:
    """Open the browser, wait for the user to sign in, and store the session.

    Args:
        connection: Where and how to reach the instance.
        settings: The ServiceNow settings (browser and how long to wait).
        name: Name to store the session under.
        launch: Starts the browser; replaced in tests.
        transport: Replacement HTTP transport, used by tests.
        poll_seconds: Pause between looks at the browser.
        announce: Shows a progress line to the user.

    Returns:
        A sentence saying who was signed in and where the session was stored.

    Raises:
        AgentToolsError: If the browser cannot be started or is closed, the instance
            cannot be reached, or the sign-in does not finish in time.
    """
    base = httpx.URL(connection.base_url)
    host = base.netloc.decode("ascii")
    binary = find_browser(settings.browser)
    announce(
        f"Opening a browser window on {base}. Sign in there as usual; this waits up to "
        f"{settings.login_timeout_seconds:.0f} seconds."
    )
    deadline = time.monotonic() + settings.login_timeout_seconds
    progress = "no tab is on the instance yet"
    async with httpx.AsyncClient(
        base_url=connection.base_url,
        headers={"Accept": "application/json"},
        timeout=connection.timeout_seconds,
        follow_redirects=False,
        verify=build_ssl_context(connection.ca_bundle) if transport is None else True,
        transport=transport,
    ) as http:
        with launch(binary, f"{connection.base_url}/") as browser:
            _log.debug("sign_in_browser", browser=await asyncio.to_thread(browser.version))
            while time.monotonic() < deadline:
                await asyncio.sleep(poll_seconds)
                # Nothing is sent to the instance while the tab is away at the sign-in service.
                paths = await asyncio.to_thread(browser.page_paths, host)
                if not paths:
                    continue
                cookies = await asyncio.to_thread(browser.cookies, host)
                try:
                    session, progress = await _attempt(http, cookies, paths)
                except httpx.HTTPError as exc:
                    raise ConnectivityError(
                        f"Could not reach {base} to check the sign-in: {type(exc).__name__}."
                    ) from exc
                _log.debug("sign_in_progress", state=progress if session is None else "done")
                if session is not None:
                    await store_session(name, session)
                    return (
                        f"Signed in to {base} as {progress}. The session is stored in the "
                        "system keychain and lasts until ServiceNow times it out."
                    )
    raise AgentToolsError(
        f"The sign-in did not finish within {settings.login_timeout_seconds:.0f} seconds "
        f"({progress}). Nothing was stored."
    )
