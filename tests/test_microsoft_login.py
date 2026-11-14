"""Tests for seeding the Microsoft refresh token."""

from datetime import UTC, datetime, timedelta

import pytest

from productivity_agent_tools.errors import AgentToolsError
from productivity_agent_tools.microsoft.login import extract_refresh_token, seed
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
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_extract_from_a_bare_token() -> None:
    assert extract_refresh_token("  0.ABC-refresh  ") == "0.ABC-refresh"


def test_extract_from_a_token_response() -> None:
    assert extract_refresh_token('{"refresh_token": "r1", "access_token": "a"}') == "r1"


def test_extract_rejects_json_without_a_refresh_token() -> None:
    with pytest.raises(AgentToolsError, match="no 'refresh_token'"):
        extract_refresh_token('{"access_token": "a"}')


def test_extract_rejects_broken_json() -> None:
    with pytest.raises(AgentToolsError, match="not valid JSON"):
        extract_refresh_token("{not json")


async def test_seed_verifies_and_stores_the_rotated_token() -> None:
    stored: list[MicrosoftSession] = []

    async def store(session: MicrosoftSession, name: str) -> None:
        stored.append(session)

    async def redeem(config: RedeemConfig, refresh_token: str, **_: object) -> TokenGrant:
        assert refresh_token == "pasted"
        return TokenGrant(
            access_token="a",
            refresh_token="rotated",
            expires_at=NOW + timedelta(minutes=59),
            scope="https://outlook.example.us/Mail.ReadWrite",
        )

    message = await seed(CONFIG, "pasted", redeem=redeem, store=store, now=lambda: NOW)
    assert stored[0].refresh_token == "rotated"
    assert stored[0].config == CONFIG
    assert "59 minutes" in message
    assert "Mail.ReadWrite" in message


async def test_seed_without_verifying_stores_the_pasted_token() -> None:
    stored: list[MicrosoftSession] = []

    async def store(session: MicrosoftSession, name: str) -> None:
        stored.append(session)

    async def redeem(config: RedeemConfig, refresh_token: str, **_: object) -> TokenGrant:
        raise AssertionError("verify=False must not redeem")

    message = await seed(
        CONFIG, "pasted", verify=False, redeem=redeem, store=store, now=lambda: NOW
    )
    assert stored[0].refresh_token == "pasted"
    assert "not verified" in message


async def test_seed_lets_a_verification_failure_surface() -> None:
    async def store(session: MicrosoftSession, name: str) -> None:
        raise AssertionError("must not store on a failed verify")

    async def redeem(config: RedeemConfig, refresh_token: str, **_: object) -> TokenGrant:
        raise AgentToolsError("the token endpoint refused the refresh")

    with pytest.raises(AgentToolsError, match="refused"):
        await seed(CONFIG, "pasted", redeem=redeem, store=store, now=lambda: NOW)
