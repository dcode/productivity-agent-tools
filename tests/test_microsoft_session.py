"""Tests for storing and reading the Microsoft refresh-token session."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from productivity_agent_tools.microsoft.oauth import RedeemConfig, TokenGrant
from productivity_agent_tools.microsoft.session import (
    MicrosoftSession,
    load_session,
    store_session,
)

CONFIG = RedeemConfig(
    client_id="c",
    tenant="t",
    authority="login.microsoftonline.us",
    origin="https://o",
    redirect_uri="https://o/r",
    scope="https://outlook.example.us/.default",
)
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def grant(refresh: str = "new", *, access: str = "a1", ttl: int = 3600) -> TokenGrant:
    return TokenGrant(
        access_token=access,
        refresh_token=refresh,
        expires_at=NOW + timedelta(seconds=ttl),
        scope="",
    )


def test_dumps_and_loads_round_trip() -> None:
    session = MicrosoftSession.seeded_now("refresh-1", CONFIG)
    restored = MicrosoftSession.loads(session.dumps())
    assert restored == session
    assert restored is not None
    assert restored.config == CONFIG


def test_seeded_now_stamps_a_time() -> None:
    session = MicrosoftSession.seeded_now("r", CONFIG)
    assert session.obtained_at
    assert session.refresh_token == "r"


def test_rotated_replaces_the_token_and_keeps_the_config() -> None:
    session = MicrosoftSession.seeded_now("old", CONFIG)
    rotated = session.rotated(grant())
    assert rotated.refresh_token == "new"
    assert rotated.config == CONFIG


def test_rotating_keeps_the_sign_in_time_and_window() -> None:
    session = MicrosoftSession("old", "2026-01-01T08:00:00+00:00", CONFIG, window_hours=18.0)
    rotated = session.rotated(grant())
    assert rotated.signed_in_at == "2026-01-01T08:00:00+00:00"
    assert rotated.obtained_at != session.obtained_at
    assert rotated.window_hours == 18.0
    assert MicrosoftSession.loads(rotated.dumps()) == rotated


def test_a_session_stored_before_the_sign_in_time_was_kept_still_loads() -> None:
    stored = (
        '{"refresh_token": "r", "obtained_at": "2026-01-01T08:00:00+00:00", "config": '
        '{"client_id": "c", "tenant": "t", "authority": "a", "origin": "o", '
        '"redirect_uri": "r", "scope": "s"}}'
    )
    session = MicrosoftSession.loads(stored)
    assert session is not None
    assert session.signed_in_at == "2026-01-01T08:00:00+00:00"
    assert session.window_hours is None


@pytest.mark.parametrize("hours", ["18", True, 0, -1])
def test_an_unusable_window_is_ignored(hours: object) -> None:
    session = MicrosoftSession.seeded_now("r", CONFIG)
    data = json.loads(session.dumps())
    data["window_hours"] = hours
    restored = MicrosoftSession.loads(json.dumps(data))
    assert restored is not None and restored.window_hours is None


def test_the_window_end_is_estimated_only_when_known() -> None:
    known = MicrosoftSession("r", "2026-01-01T08:00:00+00:00", CONFIG, window_hours=18)
    assert known.window_ends_at() == datetime(2026, 1, 2, 2, 0, tzinfo=UTC)
    assert MicrosoftSession("r", "2026-01-01T08:00:00+00:00", CONFIG).window_ends_at() is None
    garbled = MicrosoftSession("r", "yesterday", CONFIG, window_hours=18)
    assert garbled.window_ends_at() is None


def test_describe_says_when_to_sign_in_again_without_secrets() -> None:
    known = MicrosoftSession("s3cret", "2026-01-01T08:00:00+00:00", CONFIG, window_hours=18)
    text = known.describe(datetime(2026, 1, 1, 9, 0, tzinfo=UTC))
    assert text == (
        "the system keychain, signed in at 2026-01-01T08:00:00+00:00; usable until about "
        "2026-01-02T02:00:00+00:00 (18 h sign-in window), then run "
        "'productivity-agent-tools login microsoft' again; refresh token last renewed at "
        "2026-01-01T08:00:00+00:00; current access token valid until 2026-01-01T09:00:00+00:00"
    )
    unknown = MicrosoftSession("s3cret", "2026-01-01T08:00:00+00:00", CONFIG).describe()
    assert "set sign_in_hours under [microsoft]" in unknown
    assert "access token" not in unknown
    assert "s3cret" not in text + unknown


def test_a_session_does_not_show_its_refresh_token() -> None:
    session = MicrosoftSession.seeded_now("s3cret-refresh", CONFIG)
    assert "s3cret-refresh" not in repr(session)


@pytest.mark.parametrize(
    "stored",
    [
        "not json",
        "[]",
        '{"obtained_at": "t", "config": {}}',  # no refresh_token
        '{"refresh_token": "r", "config": {}}',  # no obtained_at
        '{"refresh_token": "r", "obtained_at": "t"}',  # no config
        '{"refresh_token": "r", "obtained_at": "t", "config": "x"}',  # config not a dict
        '{"refresh_token": "r", "obtained_at": "t", "config": {"client_id": "c"}}',  # short config
    ],
)
def test_loads_rejects_malformed_text(stored: str) -> None:
    assert MicrosoftSession.loads(stored) is None


async def test_store_and_load_use_the_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: dict[str, str] = {}
    monkeypatch.setattr(
        "keyring.set_password", lambda service, name, value: saved.__setitem__(name, value)
    )
    monkeypatch.setattr("keyring.get_password", lambda service, name: saved.get(name))
    session = MicrosoftSession.seeded_now("r", CONFIG)
    await store_session(session)
    assert await load_session() == session


async def test_load_returns_none_when_nothing_is_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("keyring.get_password", lambda service, name: None)
    assert await load_session() is None


async def test_load_survives_a_keychain_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import keyring.errors

    def boom(service: str, name: str) -> str:
        raise keyring.errors.KeyringError("locked")

    monkeypatch.setattr("keyring.get_password", boom)
    assert await load_session() is None


def test_rotating_keeps_the_access_token_and_its_expiry() -> None:
    rotated = MicrosoftSession.seeded_now("old", CONFIG).rotated(grant(access="a9"))
    assert rotated.access_token == "a9"
    assert rotated.access_expires_at == "2026-01-01T13:00:00+00:00"
    assert "a9" not in repr(rotated)
    restored = MicrosoftSession.loads(rotated.dumps())
    assert restored == rotated
    cached = restored.cached_grant(NOW, skew_seconds=60) if restored else None
    assert cached is not None
    assert cached.authorization() == "Bearer a9"
    assert cached.refresh_token == "new"
    assert cached.scope == CONFIG.scope


@pytest.mark.parametrize(
    ("access", "expires"),
    [
        ("", ""),
        ("a", ""),
        ("a", "not a time"),
        ("a", "2026-01-01T13:00:00"),  # no time zone: not trusted
        ("a", "2026-01-01T12:00:30+00:00"),  # inside the skew
    ],
)
def test_a_missing_bad_or_expiring_access_token_is_not_used(access: str, expires: str) -> None:
    session = MicrosoftSession(
        "r", "2026-01-01T08:00:00+00:00", CONFIG, access_token=access, access_expires_at=expires
    )
    assert session.cached_grant(NOW, skew_seconds=60) is None


def test_a_session_stored_without_an_access_token_still_loads() -> None:
    stored = json.loads(MicrosoftSession.seeded_now("r", CONFIG).dumps())
    del stored["access_token"], stored["access_expires_at"]
    restored = MicrosoftSession.loads(json.dumps(stored))
    assert restored is not None
    assert restored.access_token == ""
    stored["access_token"] = 7
    stored["access_expires_at"] = None
    odd = MicrosoftSession.loads(json.dumps(stored))
    assert odd is not None
    assert (odd.access_token, odd.access_expires_at) == ("", "")
