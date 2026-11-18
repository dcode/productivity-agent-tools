"""Tests for signing in to Microsoft through the browser (``login microsoft``)."""

import base64
import dataclasses
import hashlib
import json
import sys
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from productivity_agent_tools.browser import BrowserError
from productivity_agent_tools.errors import AgentToolsError, AuthenticationError, ConfigError
from productivity_agent_tools.microsoft.oauth import RedeemConfig, TokenGrant
from productivity_agent_tools.microsoft.session import MicrosoftSession
from productivity_agent_tools.microsoft.settings import MicrosoftSettings, redeem_config
from productivity_agent_tools.microsoft.signin import (
    Challenge,
    authorize_url,
    read_redirect,
    sign_in,
    signed_in_as,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
REDIRECT = "https://webmail.example.mil/mail/oauthRedirect.html"


def ms_settings(timeout: float = 5.0, browser: str | None = sys.executable) -> MicrosoftSettings:
    return MicrosoftSettings(
        client_id="client-123",
        tenant="tenant-abc",
        authority="login.microsoftonline.us",
        origin="https://webmail.example.mil",
        redirect_uri=REDIRECT,
        scope="https://outlook.example.us/.default",
        browser=browser,
        login_timeout_seconds=timeout,
        timeout_seconds=12.0,
    )


CONFIG = redeem_config(ms_settings())


def id_token(claims: dict[str, object]) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"eyJhbGciOiJub25lIn0.{payload}.sig"


# --- the pieces --------------------------------------------------------------------------


def test_the_challenge_is_s256_of_the_verifier() -> None:
    verifier = "dBjftJeZ4CVP-mJ92ZqWwHGyNo0gC2CmZbcF4u6M-wJ"
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    challenge = Challenge(verifier=verifier, state="s")
    assert challenge.code_challenge == expected
    assert "=" not in challenge.code_challenge
    assert verifier not in repr(challenge)


def test_each_challenge_is_fresh() -> None:
    first, second = Challenge.new(), Challenge.new()
    assert first.verifier != second.verifier
    assert first.state != second.state
    assert 43 <= len(first.verifier) <= 128


def test_the_authorize_address_asks_for_a_code_for_the_configured_client() -> None:
    challenge = Challenge(verifier="v" * 43, state="st")
    url = urlsplit(authorize_url(CONFIG, challenge))
    assert (url.scheme, url.netloc) == ("https", "login.microsoftonline.us")
    assert url.path == "/tenant-abc/oauth2/v2.0/authorize"
    params = {key: values[0] for key, values in parse_qs(url.query).items()}
    assert params == {
        "client_id": "client-123",
        "response_type": "code",
        "redirect_uri": REDIRECT,
        "response_mode": "fragment",
        "scope": "openid profile offline_access https://outlook.example.us/.default",
        "state": "st",
        "code_challenge": challenge.code_challenge,
        "code_challenge_method": "S256",
    }
    hinted = authorize_url(CONFIG, challenge, login_hint="pat@example.mil")
    assert parse_qs(urlsplit(hinted).query)["login_hint"] == ["pat@example.mil"]


@pytest.mark.parametrize(
    ("url", "code"),
    [
        (f"{REDIRECT}#code=abc&state=st&session_state=x", "abc"),
        (f"{REDIRECT}?code=abc&state=st", "abc"),
        ("https://WEBMAIL.example.mil/mail/oauthRedirect.html#code=abc&state=st", "abc"),
        (f"{REDIRECT}#code=abc&state=other", None),
        (f"{REDIRECT}#state=st", None),
        (f"{REDIRECT}", None),
        ("https://webmail.example.mil/mail/#code=abc&state=st", None),
        ("https://login.microsoftonline.us/x?redirect_uri=whatever#code=abc&state=st", None),
    ],
)
def test_only_this_attempts_redirect_yields_a_code(url: str, code: str | None) -> None:
    assert read_redirect(url, REDIRECT, "st") == code


def test_a_redirect_reporting_an_error_fails_the_sign_in() -> None:
    url = (
        f"{REDIRECT}#error=access_denied&error_description=AADSTS53003%3A+Blocked+by+"
        "Conditional+Access.%0D%0ATrace+ID%3A+x&state=st"
    )
    with pytest.raises(AuthenticationError, match=r"failed: AADSTS53003: Blocked.*Access\.\s*$"):
        read_redirect(url, REDIRECT, "st")
    with pytest.raises(AuthenticationError, match=r"failed: access_denied$"):
        read_redirect(f"{REDIRECT}#error=access_denied&state=st", REDIRECT, "st")


@pytest.mark.parametrize(
    ("token", "who"),
    [
        (
            id_token({"name": "Pat Example", "preferred_username": "pat@x.mil"}),
            "Pat Example (pat@x.mil)",
        ),
        (id_token({"upn": "pat@x.mil"}), "pat@x.mil"),
        (id_token({"name": "Pat Example", "preferred_username": ""}), "Pat Example"),
        (id_token({"oid": "1"}), None),
        (id_token(["not", "claims"]), None),  # ty: ignore[invalid-argument-type]
        ("not-a-jwt", None),
        ("a.!!!.c", None),
        ("", None),
    ],
)
def test_the_id_token_names_who_signed_in(token: str, who: str | None) -> None:
    assert signed_in_as(token) == who


# --- the sign-in ------------------------------------------------------------------------


class FakeBrowser:
    """A browser whose tab addresses follow a script, one step per look."""

    def __init__(self, steps: list[list[str]], *, watchable: bool = True) -> None:
        self.steps = steps
        self.looks = 0
        self.watchable = watchable
        self.watched = False
        self.opened = ""
        self.closed = False

    def version(self) -> str:
        return "Fake/1.0"

    def watch_targets(self) -> None:
        if not self.watchable:
            raise BrowserError("The browser refused Target.setDiscoverTargets: Not allowed.")
        self.watched = True

    def page_urls(self) -> list[str]:
        self.looks += 1
        return self.steps[min(self.looks, len(self.steps)) - 1]

    def state(self) -> str:
        return parse_qs(urlsplit(self.opened).query)["state"][0]

    def launcher(self) -> Callable[[str, str], object]:
        @contextmanager
        def launch(binary: str, url: str) -> Generator[FakeBrowser]:
            assert binary == sys.executable
            self.opened = url
            try:
                yield self
            finally:
                self.closed = True

        return launch


class Redirecting(FakeBrowser):
    """Comes back to the redirect page, with this attempt's state, on the given look."""

    def __init__(self, on_look: int, extra: str = "code=the-code", **kwargs: bool) -> None:
        super().__init__([], **kwargs)
        self.on_look = on_look
        self.extra = extra

    def page_urls(self) -> list[str]:
        self.looks += 1
        if self.looks < self.on_look:
            return ["https://login.microsoftonline.us/tenant-abc/oauth2/v2.0/authorize?x=1"]
        return ["about:blank", f"{REDIRECT}#{self.extra}&state={self.state()}"]


class Recorder:
    def __init__(self, grant: TokenGrant | None = None) -> None:
        self.grant = grant or TokenGrant(
            access_token="a",
            refresh_token="refresh-1",
            expires_at=NOW + timedelta(hours=1),
            scope="s",
            id_token=id_token({"name": "Pat Example", "preferred_username": "pat@x.mil"}),
        )
        self.redeemed: list[tuple[RedeemConfig, str, str, float]] = []
        self.stored: list[tuple[MicrosoftSession, str]] = []

    async def redeem(
        self,
        config: RedeemConfig,
        code: str,
        verifier: str,
        *,
        now: datetime | None = None,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> TokenGrant:
        self.redeemed.append((config, code, verifier, timeout_seconds))
        return self.grant

    async def store(self, session: MicrosoftSession, name: str) -> None:
        self.stored.append((session, name))


async def run_sign_in(
    browser: FakeBrowser, recorder: Recorder, *, wait: float = 5.0
) -> tuple[str, list[str]]:
    said: list[str] = []
    message = await sign_in(
        ms_settings(wait),
        launch=browser.launcher(),  # ty: ignore[invalid-argument-type]
        redeem=recorder.redeem,
        store=recorder.store,
        now=lambda: NOW,
        poll_seconds=0.01,
        announce=said.append,
    )
    return message, said


async def test_sign_in_waits_for_the_redirect_then_stores_the_refresh_token() -> None:
    browser, recorder = Redirecting(on_look=3), Recorder()
    message, said = await run_sign_in(browser, recorder)
    assert "Opening a browser window on login.microsoftonline.us" in said[0]
    assert message.startswith("Signed in to Microsoft as Pat Example (pat@x.mil).")
    assert "login microsoft" in message
    assert "until the tenant's sign-in window ends" in message
    assert browser.watched and browser.closed
    assert browser.looks == 3
    ((config, code, verifier, timeout),) = recorder.redeemed
    assert (config, code, timeout) == (CONFIG, "the-code", 12.0)
    challenge = parse_qs(urlsplit(browser.opened).query)["code_challenge"][0]
    assert Challenge(verifier=verifier, state="").code_challenge == challenge
    ((session, name),) = recorder.stored
    assert name == "microsoft"
    assert session.refresh_token == "refresh-1"
    assert session.config == CONFIG
    assert "refresh-1" not in message and verifier not in message


async def test_sign_in_still_works_when_the_tabs_cannot_be_watched() -> None:
    browser = Redirecting(on_look=1, watchable=False)
    recorder = Recorder(TokenGrant(access_token="a", refresh_token="r", expires_at=NOW, scope="s"))
    message, _ = await run_sign_in(browser, recorder)
    assert message.startswith("Signed in to Microsoft as you.")
    assert len(recorder.stored) == 1


async def test_a_code_for_another_attempt_is_never_redeemed() -> None:
    browser = FakeBrowser([[f"{REDIRECT}#code=stray&state=someone-else"]])
    recorder = Recorder()
    with pytest.raises(AgentToolsError, match=r"did not finish.*Nothing was stored") as info:
        await run_sign_in(browser, recorder, wait=0.1)
    assert "no tab came back" in str(info.value)
    assert recorder.redeemed == [] and recorder.stored == []
    assert browser.closed


async def test_a_failed_sign_in_stores_nothing_and_closes_the_browser() -> None:
    browser = Redirecting(on_look=1, extra="error=access_denied&error_description=No")
    recorder = Recorder()
    with pytest.raises(AuthenticationError, match="sign-in failed: No"):
        await run_sign_in(browser, recorder)
    assert recorder.stored == []
    assert browser.closed


async def test_a_refused_code_stores_nothing() -> None:
    class Refusing(Recorder):
        async def redeem(self, *args: object, **kwargs: object) -> TokenGrant:
            raise AuthenticationError("The token endpoint refused the sign-in code.")

    browser, recorder = Redirecting(on_look=1), Refusing()
    with pytest.raises(AuthenticationError, match="refused the sign-in code"):
        await run_sign_in(browser, recorder)
    assert recorder.stored == []
    assert browser.closed


async def test_sign_in_needs_a_browser() -> None:
    with pytest.raises(ConfigError, match="configured browser 'no-such-browser-xyz'"):
        await sign_in(ms_settings(browser="no-such-browser-xyz"), announce=lambda line: None)


async def test_a_known_sign_in_window_is_stored_and_announced() -> None:
    browser, recorder = Redirecting(on_look=1), Recorder()
    settings = dataclasses.replace(ms_settings(), sign_in_hours=18.0)
    message = await sign_in(
        settings,
        launch=browser.launcher(),  # ty: ignore[invalid-argument-type]
        redeem=recorder.redeem,
        store=recorder.store,
        now=lambda: NOW,
        poll_seconds=0.01,
        announce=lambda line: None,
    )
    ((session, _),) = recorder.stored
    assert session.window_hours == 18.0
    ends = session.window_ends_at()
    assert ends is not None
    assert f"renews silently until about {ends.isoformat(timespec='seconds')};" in message
