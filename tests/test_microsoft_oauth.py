"""Tests for redeeming a refresh token at the token endpoint."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest

from productivity_agent_tools.errors import ApiError, AuthenticationError, ConnectivityError
from productivity_agent_tools.microsoft.oauth import (
    RedeemConfig,
    TokenGrant,
    redeem_refresh_token,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

CONFIG = RedeemConfig(
    client_id="client-123",
    tenant="tenant-abc",
    authority="login.microsoftonline.us",
    origin="https://webmail.example.mil",
    redirect_uri="https://webmail.example.mil/redirect.html",
    scope="https://outlook.example.us/.default",
)


def _transport(
    captured: list[httpx.Request], *, status: int = 200, json: dict | None = None, text: str = ""
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if json is not None:
            return httpx.Response(status, json=json)
        return httpx.Response(status, text=text)

    return httpx.MockTransport(handler)


async def test_redeem_returns_a_grant_and_sends_the_spa_shape() -> None:
    captured: list[httpx.Request] = []
    transport = _transport(
        captured,
        json={
            "access_token": "access-1",
            "refresh_token": "rotated-1",
            "expires_in": 3599,
            "scope": "https://outlook.example.us/Mail.ReadWrite",
        },
    )
    grant = await redeem_refresh_token(CONFIG, "old-refresh", now=NOW, transport=transport)

    assert grant.access_token == "access-1"
    assert grant.refresh_token == "rotated-1"
    assert grant.expires_at == NOW + timedelta(seconds=3599)
    assert grant.authorization() == "Bearer access-1"

    request = captured[0]
    assert str(request.url) == "https://login.microsoftonline.us/tenant-abc/oauth2/v2.0/token"
    assert request.headers["Origin"] == "https://webmail.example.mil"
    form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
    assert form["grant_type"] == "refresh_token"
    assert form["client_id"] == "client-123"
    assert form["refresh_token"] == "old-refresh"
    assert form["redirect_uri"] == "https://webmail.example.mil/redirect.html"
    assert "offline_access" in form["scope"].split()
    assert "https://outlook.example.us/.default" in form["scope"]


async def test_redeem_keeps_the_old_refresh_token_when_none_is_returned() -> None:
    transport = _transport([], json={"access_token": "a", "expires_in": 60})
    grant = await redeem_refresh_token(CONFIG, "keep-me", now=NOW, transport=transport)
    assert grant.refresh_token == "keep-me"


async def test_redeem_defaults_expiry_when_absent() -> None:
    transport = _transport([], json={"access_token": "a", "refresh_token": "b"})
    grant = await redeem_refresh_token(CONFIG, "r", now=NOW, transport=transport)
    assert grant.expires_at == NOW + timedelta(seconds=3600)


async def test_redeem_does_not_double_offline_access() -> None:
    captured: list[httpx.Request] = []
    config = RedeemConfig(
        client_id="c",
        tenant="t",
        authority="login.microsoftonline.us",
        origin="https://o",
        redirect_uri="https://o/r",
        scope="offline_access https://outlook.example.us/.default",
    )
    transport = _transport(captured, json={"access_token": "a", "refresh_token": "b"})
    await redeem_refresh_token(config, "r", now=NOW, transport=transport)
    scope = parse_qs(captured[0].content.decode())["scope"][0]
    assert scope.split().count("offline_access") == 1


async def test_an_expired_refresh_token_asks_to_reseed() -> None:
    transport = _transport(
        [],
        status=400,
        json={
            "error": "invalid_grant",
            "error_description": "AADSTS70043: The refresh token has expired.\nTrace ID: x",
        },
    )
    with pytest.raises(AuthenticationError, match="login-microsoft"):
        await redeem_refresh_token(CONFIG, "stale", now=NOW, transport=transport)


async def test_another_aadsts_error_is_an_api_error_with_the_first_line() -> None:
    transport = _transport(
        [],
        status=400,
        json={
            "error": "invalid_request",
            "error_description": "AADSTS65002: preauthorization required.\nTrace ID: y",
        },
    )
    with pytest.raises(ApiError, match="AADSTS65002: preauthorization required"):
        await redeem_refresh_token(CONFIG, "r", now=NOW, transport=transport)


async def test_a_missing_access_token_is_an_api_error() -> None:
    transport = _transport([], json={"token_type": "Bearer"})
    with pytest.raises(ApiError, match="no access token"):
        await redeem_refresh_token(CONFIG, "r", now=NOW, transport=transport)


async def test_a_non_json_body_is_an_api_error() -> None:
    transport = _transport([], text="not json")
    with pytest.raises(ApiError, match="non-JSON"):
        await redeem_refresh_token(CONFIG, "r", now=NOW, transport=transport)


async def test_a_non_dict_json_body_is_an_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2])

    with pytest.raises(ApiError, match="unexpected body"):
        await redeem_refresh_token(CONFIG, "r", now=NOW, transport=httpx.MockTransport(handler))


async def test_a_non_json_error_body_falls_back_to_the_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="oops")

    with pytest.raises(ApiError, match="HTTP 500"):
        await redeem_refresh_token(CONFIG, "r", now=NOW, transport=httpx.MockTransport(handler))


async def test_a_network_failure_is_a_connectivity_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(ConnectivityError, match="Microsoft login host"):
        await redeem_refresh_token(CONFIG, "r", now=NOW, transport=httpx.MockTransport(handler))


def test_expired_accounts_for_skew() -> None:
    grant = TokenGrant(
        access_token="a", refresh_token="b", expires_at=NOW + timedelta(seconds=30), scope=""
    )
    assert grant.expired(NOW, skew_seconds=60)
    assert not grant.expired(NOW, skew_seconds=10)


def test_a_grant_does_not_show_its_tokens() -> None:
    grant = TokenGrant(access_token="s3cret", refresh_token="r3fresh", expires_at=NOW, scope="x")
    assert "s3cret" not in repr(grant)
    assert "r3fresh" not in repr(grant)
