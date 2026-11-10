"""Shared fixtures: a fake Jira served through httpx.MockTransport."""

import json
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import httpx
import pytest
import structlog

from productivity_agent_tools.config import JiraSettings, Settings
from productivity_agent_tools.http import AtlassianClient
from productivity_agent_tools.jira.client import JiraClient

BANNER_HTML = "<html><body><button>I accept the terms of use</button></body></html>"

ISSUE: dict[str, Any] = {
    "key": "ABC-1",
    "fields": {
        "summary": "Fix the thing",
        "status": {"name": "To Do"},
        "issuetype": {"name": "Task"},
        "priority": {"name": "High"},
        "assignee": {"displayName": "Pat Example"},
        "updated": "2026-11-09T10:00:00.000+0000",
        "description": "h3. Details",
        "reporter": {"displayName": "Sam Example"},
        "created": "2026-11-01T10:00:00.000+0000",
        "labels": ["ops"],
        "comment": {
            "comments": [
                {"id": str(n), "author": {"displayName": "Sam Example"}, "body": f"note {n}"}
                for n in range(1, 4)
            ]
        },
    },
}

TRANSITIONS = {
    "transitions": [
        {"id": "11", "name": "Start Progress", "to": {"name": "In Progress"}},
        {"id": "21", "name": "Done", "to": {"name": "Done"}},
    ]
}


class FakeJira:
    """Records requests and answers like the proxy plus Jira would."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.require_banner_cookie = True
        self.valid_token = "good-token"
        self.overrides: dict[str, httpx.Response] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.require_banner_cookie and "consent=yes" not in request.headers.get("cookie", ""):
            return httpx.Response(200, html=BANNER_HTML)
        if request.headers.get("authorization") != f"Bearer {self.valid_token}":
            return httpx.Response(401, json={"errorMessages": [], "errors": {}})
        path = request.url.path
        if path in self.overrides:
            return self.overrides[path]
        session = {"set-cookie": "JSESSIONID=abc; Path=/jira; HttpOnly"}
        if path == "/jira/rest/api/2/myself":
            return httpx.Response(
                200, json={"name": "pat", "displayName": "Pat Example"}, headers=session
            )
        if path == "/jira/rest/api/2/serverInfo":
            return httpx.Response(200, json={"version": "9.12.0"})
        if path == "/jira/rest/api/2/search":
            if "BROKEN" in request.url.params["jql"]:
                return httpx.Response(400, json={"errorMessages": ["Bad JQL"], "errors": {}})
            return httpx.Response(200, json={"total": 7, "issues": [ISSUE]})
        if path == "/jira/rest/api/2/issue/ABC-1" and request.method == "GET":
            return httpx.Response(200, json=ISSUE)
        if path == "/jira/rest/api/2/issue/ABC-1/transitions":
            if request.method == "GET":
                return httpx.Response(200, json=TRANSITIONS)
            return httpx.Response(204)
        if path == "/jira/rest/api/2/issue/ABC-1/comment" and request.method == "POST":
            return httpx.Response(201, json={"id": "99", **json.loads(request.content)})
        return httpx.Response(404, json={"errorMessages": ["Issue Does Not Exist"]})

    def writes(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method != "GET"]


@pytest.fixture(autouse=True)
def default_logging() -> Iterator[None]:
    yield
    structlog.reset_defaults()


@pytest.fixture
def fake() -> FakeJira:
    return FakeJira()


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    def build(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "base_url": "https://host.example",
            "cookies": {"consent": "yes"},
            "jira": JiraSettings(),
        }
        values.update(overrides)
        return Settings(**values)

    return build


@pytest.fixture
async def make_core(
    fake: FakeJira, make_settings: Callable[..., Settings]
) -> AsyncIterator[Callable[..., AtlassianClient]]:
    created: list[AtlassianClient] = []

    def build(token: str = "good-token", **overrides: Any) -> AtlassianClient:
        core = AtlassianClient.from_settings(
            make_settings(**overrides), token, transport=httpx.MockTransport(fake.handler)
        )
        created.append(core)
        return core

    yield build
    for core in created:
        await core.aclose()


@pytest.fixture
def make_jira(make_core: Callable[..., AtlassianClient]) -> Callable[..., JiraClient]:
    def build(**overrides: Any) -> JiraClient:
        return JiraClient(make_core(**overrides), JiraSettings())

    return build
