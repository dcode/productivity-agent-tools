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


async def test_a_reseed_while_running_is_picked_up() -> None:
    clock = Clock()
    redeems: list[str] = []
    # The load returns the original session first, then a re-seeded one.
    provider = make_provider(
        clock,
        session=None,
        sessions=[
            MicrosoftSession.seeded_now("r0", CONFIG),
            MicrosoftSession.seeded_now("fresh", CONFIG),
        ],
        grants=[make_grant("access-1", "r1", START, ttl=10), make_grant("access-2", "r2", START)],
        stored=[],
        redeems=redeems,
    )
    await provider()
    clock.now = START + timedelta(seconds=10)  # force a renewal
    await provider()
    assert redeems == ["r0", "fresh"]  # the renewal reloaded and used the re-seeded token


async def test_no_session_is_an_authentication_error() -> None:
    clock = Clock()
    provider = make_provider(clock, session=None, grants=[], stored=[], redeems=[])
    with pytest.raises(AuthenticationError, match="login-microsoft"):
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
    assert "2026-01-01T12:00:00+00:00" in credential.source


async def test_resolver_without_a_session_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def load(name: str = "microsoft") -> None:
        return None

    monkeypatch.setattr("productivity_agent_tools.microsoft.credentials.load_session", load)
    with pytest.raises(AuthenticationError, match="login-microsoft"):
        await microsoft_token("microsoft", None)
