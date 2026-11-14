"""Tests for storing and reading the Microsoft refresh-token session."""

import pytest

from productivity_agent_tools.microsoft.oauth import RedeemConfig
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
    rotated = session.rotated("new")
    assert rotated.refresh_token == "new"
    assert rotated.config == CONFIG


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
