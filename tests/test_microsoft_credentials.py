"""Tests for the Microsoft credential provider: caching, rotation and renewal."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from productivity_agent_tools.errors import AuthenticationError
from productivity_agent_tools.microsoft.credentials import MicrosoftCredential, microsoft_token
from productivity_agent_tools.microsoft.oauth import RedeemConfig, TokenGrant
from productivity_agent_tools.microsoft.session import MicrosoftSession

CONFIG = RedeemConfig(
    client_id="c",
    tenant="t",
    authority="login.microsoftonline.us",
    origin="https://o",
    redirect_uri="https://o/r",
    scope="https://outlook.example.us/.default",
)
START = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class Clock:
    """A clock the test moves forward by hand."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


def make_grant(access: str, refresh: str, now: datetime, ttl: int = 3600) -> TokenGrant:
    return TokenGrant(
        access_token=access,
        refresh_token=refresh,
        expires_at=now + timedelta(seconds=ttl),
        scope="",
    )


def make_provider(
    clock: Clock,
    *,
    session: MicrosoftSession | None,
    grants: list[TokenGrant],
    stored: list[MicrosoftSession],
    redeems: list[str],
    sessions: list[MicrosoftSession | None] | None = None,
) -> MicrosoftCredential:
    """Build a provider wired to in-memory fakes.

    ``grants`` is served one per redemption; ``redeems`` records each refresh
    token spent; ``stored`` records each session written; ``sessions`` overrides
    what each load returns (defaults to always ``session``).
    """
    loads = iter(sessions) if sessions is not None else None

    async def load(name: str) -> MicrosoftSession | None:
        await asyncio.sleep(0)
        if loads is not None:  # a scripted sequence, e.g. an external re-seed
            return next(loads)
        return stored[-1] if stored else session  # model the keychain: reads back what we stored

    async def store(new: MicrosoftSession, name: str) -> None:
        stored.append(new)

    async def redeem(
        config: RedeemConfig,
        refresh_token: str,
        *,
        now: datetime | None = None,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> TokenGrant:
        await asyncio.sleep(0)
        redeems.append(refresh_token)
        return grants.pop(0)

    return MicrosoftCredential(redeem=redeem, load=load, store=store, now=clock)


async def test_a_fresh_token_is_cached_between_requests() -> None:
    clock = Clock()
    redeems: list[str] = []
    provider = make_provider(
        clock,
        session=MicrosoftSession.seeded_now("r0", CONFIG),
        grants=[make_grant("access-1", "r1", START)],
        stored=[],
        redeems=redeems,
    )
    first = await provider()
    second = await provider()
    assert first == second == {"Authorization": "Bearer access-1"}
    assert redeems == ["r0"]  # one redemption served both requests


async def test_the_token_is_renewed_once_it_nears_expiry() -> None:
    clock = Clock()
    redeems: list[str] = []
    stored: list[MicrosoftSession] = []
    provider = make_provider(
        clock,
        session=MicrosoftSession.seeded_now("r0", CONFIG),
        grants=[make_grant("access-1", "r1", START, ttl=3600), make_grant("access-2", "r2", START)],
        stored=stored,
        redeems=redeems,
    )
    assert await provider() == {"Authorization": "Bearer access-1"}
    clock.now = START + timedelta(seconds=3600)  # token now within the skew window
    assert await provider() == {"Authorization": "Bearer access-2"}
    assert redeems == ["r0", "r1"]  # the second redemption used the rotated token
    assert [s.refresh_token for s in stored] == ["r1", "r2"]  # rotation persisted each time


async def test_concurrent_requests_share_one_redemption() -> None:
    clock = Clock()
    redeems: list[str] = []
    provider = make_provider(
        clock,
        session=MicrosoftSession.seeded_now("r0", CONFIG),
        grants=[make_grant("access-1", "r1", START)],
        stored=[],
        redeems=redeems,
    )
    results = await asyncio.gather(provider(), provider(), provider())
    assert all(r == {"Authorization": "Bearer access-1"} for r in results)
    assert redeems == ["r0"]  # the lock kept the three requests to a single redemption


class Keychain:
    """One stored session that a test can replace mid-redemption, as a new sign-in would."""

    def __init__(self, session: MicrosoftSession | None) -> None:
        self.session = session
        self.writes: list[MicrosoftSession] = []
        self.during_redeem: MicrosoftSession | None = None
        self.redeems: list[str] = []

    async def load(self, name: str) -> MicrosoftSession | None:
        await asyncio.sleep(0)
        return self.session

    async def store(self, new: MicrosoftSession, name: str) -> None:
        self.writes.append(new)
        self.session = new

    def provider(self, clock: Clock, grants: list[TokenGrant]) -> MicrosoftCredential:
        async def redeem(config: RedeemConfig, refresh_token: str, **_: object) -> TokenGrant:
            self.redeems.append(refresh_token)
            if self.during_redeem is not None:
                self.session, self.during_redeem = self.during_redeem, None
            return grants.pop(0)

        return MicrosoftCredential(redeem=redeem, load=self.load, store=self.store, now=clock)


async def test_a_new_sign_in_while_running_is_picked_up_at_the_next_renewal() -> None:
    clock = Clock()
    keychain = Keychain(MicrosoftSession.seeded_now("r0", CONFIG))
    provider = keychain.provider(
        clock, [make_grant("access-1", "r1", START, ttl=10), make_grant("access-2", "r2", START)]
    )
    await provider()
    keychain.session = MicrosoftSession.seeded_now("fresh", CONFIG)  # login microsoft
    clock.now = START + timedelta(seconds=10)  # force a renewal
    assert await provider() == {"Authorization": "Bearer access-2"}
    assert keychain.redeems == ["r0", "fresh"]
    assert keychain.session is not None and keychain.session.refresh_token == "r2"


async def test_a_sign_in_during_a_renewal_is_not_overwritten() -> None:
    clock = Clock()
    keychain = Keychain(MicrosoftSession.seeded_now("r0", CONFIG))
    newer = MicrosoftSession.seeded_now("from-login", CONFIG)
    keychain.during_redeem = newer
    provider = keychain.provider(clock, [make_grant("access-1", "r1", START)])
    assert await provider() == {"Authorization": "Bearer access-1"}
    assert keychain.writes == []  # the old sign-in's rotated token was not written back
    assert keychain.session is newer


async def test_describe_reads_the_keychain_each_time() -> None:
    clock = Clock()
    first = MicrosoftSession("r0", "2026-01-01T08:00:00+00:00", CONFIG, window_hours=18.0)
    keychain = Keychain(first)
    provider = keychain.provider(clock, [make_grant("access-1", "r1", START)])
    before = await provider.describe()
    assert "signed in at 2026-01-01T08:00:00+00:00" in before
    assert "access token" not in before
    await provider()
    after = await provider.describe()
    assert "signed in at 2026-01-01T08:00:00+00:00" in after  # rotation keeps the sign-in
    assert "usable until about 2026-01-02T02:00:00+00:00" in after
    assert "current access token valid until 2026-01-01T13:00:00+00:00" in after
    keychain.session = MicrosoftSession("x", "2026-01-01T20:00:00+00:00", CONFIG)
    assert "signed in at 2026-01-01T20:00:00+00:00" in await provider.describe()
    keychain.session = None
    assert "login microsoft" in await provider.describe()


async def test_no_session_is_an_authentication_error() -> None:
    clock = Clock()
    provider = make_provider(clock, session=None, grants=[], stored=[], redeems=[])
    with pytest.raises(AuthenticationError, match="login microsoft"):
        await provider()


async def test_a_reseed_error_propagates() -> None:
    clock = Clock()

    async def load(name: str) -> MicrosoftSession:
        return MicrosoftSession.seeded_now("stale", CONFIG)

    async def store(new: MicrosoftSession, name: str) -> None:
        pass

    async def redeem(config: RedeemConfig, refresh_token: str, **_: object) -> TokenGrant:
        raise AuthenticationError("expired; re-seed with login-microsoft")

    provider = MicrosoftCredential(redeem=redeem, load=load, store=store, now=clock)
    with pytest.raises(AuthenticationError, match="re-seed"):
        await provider()


async def test_resolver_describes_the_stored_session(monkeypatch: pytest.MonkeyPatch) -> None:
    session = MicrosoftSession("r", "2026-01-01T12:00:00+00:00", CONFIG)

    async def load(name: str = "microsoft") -> MicrosoftSession:
        return session

    monkeypatch.setattr("productivity_agent_tools.microsoft.credentials.load_session", load)
    credential = await microsoft_token("microsoft", None)
    assert credential.label == "OAuth token"
    assert credential.source == "the system keychain, signed in at 2026-01-01T12:00:00+00:00"
    assert credential.describe is not None


async def test_resolver_without_a_session_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def load(name: str = "microsoft") -> None:
        return None

    monkeypatch.setattr("productivity_agent_tools.microsoft.credentials.load_session", load)
    with pytest.raises(AuthenticationError, match="login microsoft"):
        await microsoft_token("microsoft", None)
