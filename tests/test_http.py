from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from productivity_agent_tools.errors import (
    ApiError,
    AuthenticationError,
    ConfigError,
    ConnectivityError,
    InterstitialPageError,
    NotFoundError,
)
from productivity_agent_tools.http import AtlassianClient
from tests.conftest import FakeJira

MYSELF = "/jira/rest/api/2/myself"


async def test_web_page_without_cookies_explains_the_cookie_option(
    fake: FakeJira, make_core: Callable[..., AtlassianClient]
) -> None:
    core = make_core(cookies={})
    with pytest.raises(InterstitialPageError, match=r"\[cookies\].*--cookie name=value"):
        await core.get(MYSELF)
    assert "cookie" not in fake.requests[0].headers


async def test_web_page_despite_cookies_names_them_without_values(
    make_core: Callable[..., AtlassianClient],
) -> None:
    core = make_core(cookies={"wrongname": "s3cret"})
    with pytest.raises(InterstitialPageError, match=r"\(wrongname\)") as info:
        await core.get(MYSELF)
    assert "s3cret" not in str(info.value)


async def test_several_cookies_are_sent_in_one_header(
    fake: FakeJira, make_core: Callable[..., AtlassianClient]
) -> None:
    await make_core(cookies={"consent": "yes", "route": "a1"}).get(MYSELF)
    assert fake.requests[0].headers["cookie"] == "consent=yes; route=a1"


async def test_request_sends_configured_cookie_and_bearer(
    fake: FakeJira, make_core: Callable[..., AtlassianClient]
) -> None:
    body = await make_core().get(MYSELF)
    assert body == {"name": "pat", "displayName": "Pat Example"}
    sent = fake.requests[0].headers
    assert sent["cookie"] == "consent=yes"
    assert sent["authorization"] == "Bearer good-token"


async def test_session_cookies_from_server_are_not_replayed(
    fake: FakeJira, make_core: Callable[..., AtlassianClient]
) -> None:
    core = make_core()
    await core.get(MYSELF)
    await core.get(MYSELF)
    assert "JSESSIONID" not in fake.requests[1].headers["cookie"]


async def test_bad_token_is_an_authentication_error(
    make_core: Callable[..., AtlassianClient],
) -> None:
    with pytest.raises(AuthenticationError, match="set-token"):
        await make_core(token="expired").get(MYSELF)


async def test_error_statuses_carry_jira_detail(make_core: Callable[..., AtlassianClient]) -> None:
    core = make_core()
    with pytest.raises(NotFoundError, match="Issue Does Not Exist"):
        await core.get("/jira/rest/api/2/issue/NOPE-1")
    with pytest.raises(ApiError, match="Bad JQL") as info:
        await core.get("/jira/rest/api/2/search", params={"jql": "BROKEN"})
    assert info.value.status == 400


async def test_dry_run_write_sends_nothing(
    fake: FakeJira, make_core: Callable[..., AtlassianClient]
) -> None:
    outcome = await make_core().write("POST", "/jira/rest/api/2/issue/ABC-1/comment", {"body": "x"})
    assert outcome.sent is False
    assert outcome.payload == {"body": "x"}
    assert fake.requests == []


async def test_live_write_is_sent(
    fake: FakeJira, make_core: Callable[..., AtlassianClient]
) -> None:
    core = make_core(dry_run=False)
    outcome = await core.write("POST", "/jira/rest/api/2/issue/ABC-1/comment", {"body": "x"})
    assert outcome.sent is True
    assert outcome.response == {"id": "99", "body": "x"}
    assert len(fake.writes()) == 1


async def test_connection_failure_mentions_vpn() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    async with AtlassianClient(
        base_url="https://host.example", token="t", transport=httpx.MockTransport(refuse)
    ) as core:
        with pytest.raises(ConnectivityError, match="VPN"):
            await core.get(MYSELF)


def test_missing_ca_bundle_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="export-ca"):
        AtlassianClient(base_url="https://host.example", token="t", ca_bundle=tmp_path / "no.pem")
