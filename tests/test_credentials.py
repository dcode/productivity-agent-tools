"""Tests for the credential seam between products and their adapters."""

from collections.abc import Mapping
from pathlib import Path

import httpx
import pytest

from productivity_agent_tools.credentials import (
    Credential,
    bearer_token,
    personal_access_token,
)
from productivity_agent_tools.errors import AuthenticationError, TokenNotFoundError
from productivity_agent_tools.http import AtlassianClient


async def test_bearer_token_sends_the_same_header_every_time() -> None:
    provide = bearer_token("abc")
    assert await provide() == {"Authorization": "Bearer abc"}
    assert await provide() == {"Authorization": "Bearer abc"}


async def test_personal_access_token_reports_its_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_TOKEN", "from-env")
    credential = await personal_access_token("jira", None)
    assert credential.source == "environment variable JIRA_TOKEN"
    assert await credential.headers() == {"Authorization": "Bearer from-env"}


async def test_personal_access_token_reads_the_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("JIRA_TOKEN", raising=False)
    env_file = tmp_path / "tokens.env"
    env_file.write_text("JIRA_TOKEN=from-file\n")
    credential = await personal_access_token("jira", env_file)
    assert await credential.headers() == {"Authorization": "Bearer from-file"}


async def test_a_missing_token_is_still_a_token_not_found_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NOPE_TOKEN", raising=False)
    monkeypatch.setattr("keyring.get_password", lambda *_: None)
    with pytest.raises(TokenNotFoundError, match="NOPE_TOKEN"):
        await personal_access_token("nope", None)


def test_a_credential_does_not_show_its_provider() -> None:
    credential = Credential(headers=bearer_token("s3cret"), source="a test")
    assert "s3cret" not in repr(credential)
    assert "headers" not in repr(credential)


async def test_the_adapter_asks_for_headers_before_every_request() -> None:
    """A credential that changes while connected is used from the next request on."""
    current = {"value": "first"}
    seen: list[dict[str, str | None]] = []

    async def session() -> Mapping[str, str]:
        return {"Cookie": f"session={current['value']}", "X-UserToken": current["value"]}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({name: request.headers.get(name) for name in ("cookie", "x-usertoken")})
        assert "authorization" not in request.headers
        return httpx.Response(200, json={})

    async with AtlassianClient(
        base_url="https://host.example",
        credentials=session,
        dry_run=False,
        transport=httpx.MockTransport(handler),
    ) as api:
        await api.get("/a")
        current["value"] = "second"
        await api.write("POST", "/b", {})
    assert seen == [
        {"cookie": "session=first", "x-usertoken": "first"},
        {"cookie": "session=second", "x-usertoken": "second"},
    ]


async def test_a_provider_that_cannot_renew_stops_the_request() -> None:
    sent: list[httpx.Request] = []

    async def expired() -> Mapping[str, str]:
        raise AuthenticationError("Your session has expired; sign in again.")

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={})

    async with AtlassianClient(
        base_url="https://host.example",
        credentials=expired,
        transport=httpx.MockTransport(handler),
    ) as api:
        with pytest.raises(AuthenticationError, match="sign in again"):
            await api.get("/a")
    assert sent == []
