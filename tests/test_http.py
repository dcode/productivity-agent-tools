from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from structlog.testing import capture_logs

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


def _redirect(location: str | None) -> httpx.Response:
    return httpx.Response(302, headers={"location": location} if location else {})


async def test_redirect_names_the_destination_without_its_query(
    fake: FakeJira, make_core: Callable[..., AtlassianClient]
) -> None:
    fake.overrides[MYSELF] = _redirect("/consent/notice.html?state=s3cret#top")
    with pytest.raises(ApiError, match=r"to /jira/rest/api/2/myself \(HTTP 302\)") as info:
        await make_core().get(MYSELF)
    message = str(info.value)
    assert "https://host.example/consent/notice.html." in message
    assert "s3cret" not in message
    assert "(consent)" in message
    assert "path under [jira]" in message


async def test_redirect_to_another_host_points_at_sign_in_or_base_url(
    fake: FakeJira, make_core: Callable[..., AtlassianClient]
) -> None:
    fake.overrides[MYSELF] = _redirect("https://sso.example/login?SAMLRequest=abc")
    with pytest.raises(ApiError, match=r"https://sso\.example/login, on a different host") as info:
        await make_core().get(MYSELF)
    assert "SAMLRequest" not in str(info.value)
    assert "base_url" in str(info.value)


async def test_redirect_back_to_the_same_address_means_a_missing_cookie(
    fake: FakeJira, make_core: Callable[..., AtlassianClient]
) -> None:
    fake.require_banner_cookie = False
    fake.overrides[MYSELF] = _redirect(f"https://host.example{MYSELF}")
    with pytest.raises(ApiError, match=r"back to the same address.*--cookie name=value"):
        await make_core(cookies={}).get(MYSELF)


async def test_redirect_without_a_location_still_explains_itself(
    fake: FakeJira, make_core: Callable[..., AtlassianClient]
) -> None:
    fake.overrides[MYSELF] = _redirect(None)
    with pytest.raises(ApiError, match="without saying where to"):
        await make_core().get(MYSELF)


async def test_debug_events_describe_the_exchange_without_secrets(
    fake: FakeJira, make_core: Callable[..., AtlassianClient]
) -> None:
    fake.overrides["/jira/rest/api/2/search"] = httpx.Response(
        302,
        headers=[
            ("location", "/login.jsp?os_destination=s3cret"),
            ("set-cookie", "JSESSIONID=abc123; Path=/"),
            ("x-seraph-loginreason", "AUTHENTICATED_FAILED"),
        ],
    )
    with capture_logs() as events, pytest.raises(ApiError):
        await make_core().get("/jira/rest/api/2/search", {"jql": "project = HUSH"})
    request, response = events
    assert request == {
        "event": "http_request",
        "log_level": "debug",
        "method": "GET",
        "url": "https://host.example/jira/rest/api/2/search",
        "param_names": ["jql"],
        "cookie_names": ["consent"],
        "has_body": False,
    }
    assert response["event"] == "http_response"
    assert response["status"] == 302
    assert response["location"] == "https://host.example/login.jsp"
    assert response["set_cookie_names"] == ["JSESSIONID"]
    assert response["x_seraph_loginreason"] == "AUTHENTICATED_FAILED"
    logged = repr(events)
    for secret in ("good-token", "consent=yes", "abc123", "s3cret", "HUSH"):
        assert secret not in logged


async def test_dry_run_write_and_transport_failures_are_logged(
    make_core: Callable[..., AtlassianClient],
) -> None:
    def stall(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with capture_logs() as events:
        await make_core().write("POST", "/jira/rest/api/2/issue/ABC-1/comment", {"body": "x"})
        async with AtlassianClient(
            base_url="https://host.example", token="t", transport=httpx.MockTransport(stall)
        ) as core:
            with pytest.raises(ConnectivityError, match="Timed out"):
                await core.get(MYSELF)
    names = [event["event"] for event in events]
    assert names == ["write_blocked_by_dry_run", "http_request", "http_timeout"]
    assert events[0]["log_level"] == "info"


@pytest.mark.parametrize(
    ("base_url", "question"),
    [
        (
            "https://host.example/jira",
            "Did you mean to repeat '/jira' in the address "
            "https://host.example/jira/jira/rest/api/2/myself? If not, correct base_url",
        ),
        (
            "https://host.example//proxy",
            "Did you mean to have '//' in the address "
            "https://host.example//proxy/jira/rest/api/2/myself? If not, correct base_url",
        ),
    ],
)
async def test_errors_question_a_suspicious_address(
    fake: FakeJira, make_core: Callable[..., AtlassianClient], base_url: str, question: str
) -> None:
    with pytest.raises(NotFoundError) as info:
        await make_core(base_url=base_url).get(MYSELF)
    assert question in str(info.value)


async def test_repeated_segment_is_not_questioned_when_the_call_works(
    fake: FakeJira, make_core: Callable[..., AtlassianClient]
) -> None:
    fake.overrides["/jira/jira/rest/api/2/myself"] = httpx.Response(200, json={"name": "pat"})
    assert await make_core(base_url="https://host.example/jira").get(MYSELF) == {"name": "pat"}


async def test_shared_prefix_in_base_url_is_kept_and_not_questioned(
    fake: FakeJira, make_core: Callable[..., AtlassianClient]
) -> None:
    with pytest.raises(NotFoundError) as info:
        await make_core(base_url="https://host.example/atlassian").get(MYSELF)
    assert fake.requests[0].url.path == "/atlassian/jira/rest/api/2/myself"
    assert "Did you mean" not in str(info.value)


async def test_empty_success_labelled_html_is_a_success_not_a_web_page(
    fake: FakeJira, make_core: Callable[..., AtlassianClient]
) -> None:
    path = "/jira/rest/api/2/issue/ABC-1/transitions"
    fake.overrides[path] = httpx.Response(204, headers={"content-type": "text/html;charset=UTF-8"})
    outcome = await make_core(dry_run=False).write("POST", path, {"transition": {"id": "21"}})
    assert (outcome.sent, outcome.response) == (True, None)


async def test_connection_failure_mentions_vpn() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    async with AtlassianClient(
        base_url="https://host.example", token="t", transport=httpx.MockTransport(refuse)
    ) as core:
        with capture_logs() as events, pytest.raises(ConnectivityError, match="VPN"):
            await core.get(MYSELF)
    assert events[-1]["event"] == "http_transport_error"


def test_missing_ca_bundle_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="export-ca"):
        AtlassianClient(base_url="https://host.example", token="t", ca_bundle=tmp_path / "no.pem")
