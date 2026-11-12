"""Shared fixtures: a fake Jira served through httpx.MockTransport."""

import json
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import httpx
import pytest
import structlog

from productivity_agent_tools.config import Settings
from productivity_agent_tools.http import AtlassianClient
from productivity_agent_tools.jira.client import JiraClient
from productivity_agent_tools.jira.settings import JiraSettings

BANNER_HTML = "<html><body><button>I accept the terms of use</button></body></html>"

ISSUE: dict[str, Any] = {
    "id": "10001",
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
        "project": {"key": "ABC"},
        "duedate": "2026-11-30",
        "resolution": None,
        "timetracking": {"originalEstimate": "2d", "remainingEstimate": "1d", "timeSpent": "1d"},
        "timeoriginalestimate": 57600,
        "timespent": 28800,
        "components": [{"id": "10", "name": "API"}, {"id": "11"}],
        "fixVersions": [{"id": "20", "name": "1.0"}],
        "versions": [],
        "issuelinks": [
            {
                "id": "900",
                "type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
                "outwardIssue": {
                    "key": "ABC-2",
                    "fields": {"summary": "Ship it", "status": {"name": "To Do"}},
                },
            },
            {
                "id": "901",
                "type": {"name": "Relates", "inward": "relates to", "outward": "relates to"},
                "inwardIssue": {"key": "XYZ-9"},
            },
            {"id": "902", "type": {"name": "Odd"}, "inwardIssue": {"key": "XYZ-10"}},
        ],
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
        {
            "id": "21",
            "name": "Done",
            "to": {"name": "Done"},
            "fields": {
                "resolution": {
                    "required": False,
                    "name": "Resolution",
                    "allowedValues": [
                        {"id": "1", "name": "Fixed"},
                        {"id": "2", "name": "Won't Do"},
                    ],
                }
            },
        },
    ]
}


WORKLOGS: dict[str, Any] = {
    "total": 3,
    "worklogs": [
        {
            "id": "1",
            "author": {"name": "pat", "displayName": "Pat Example"},
            "started": "2026-11-09T09:00:00.000+0000",
            "timeSpent": "1h",
            "timeSpentSeconds": 3600,
            "comment": "triage",
        },
        {"id": "2", "author": {"name": "sam"}, "timeSpentSeconds": 1800},
        {"id": "3", "timeSpent": "2h", "timeSpentSeconds": 7200},
    ],
}
PEOPLE: list[dict[str, Any]] = [
    {"name": "jlee", "displayName": "Jo Lee"},
    {"name": "jleeson", "displayName": "Jamie Leeson"},
    {"name": "kim", "displayName": "Kim Park"},
    {"name": "pat", "displayName": "Pat Example"},
    {"displayName": "No Username"},
]


EDITMETA: dict[str, Any] = {
    "fields": {
        "summary": {"required": True, "name": "Summary", "schema": {"type": "string"}},
        "description": {"name": "Description", "schema": {"type": "string"}},
        "priority": {
            "name": "Priority",
            "schema": {"type": "priority"},
            "allowedValues": [{"id": "2", "name": "High"}, {"id": "3", "name": "Medium"}],
        },
        "reporter": {"name": "Reporter", "schema": {"type": "user"}},
        "duedate": {"name": "Due Date", "schema": {"type": "date"}},
        "timetracking": {"name": "Time Tracking", "schema": {"type": "timetracking"}},
        "labels": {
            "name": "Labels",
            "schema": {"type": "array", "items": "string"},
            "operations": ["add", "set", "remove"],
        },
        "components": {
            "name": "Component/s",
            "schema": {"type": "array", "items": "component"},
            "allowedValues": [{"id": "10", "name": "API"}, {"id": "12", "name": "Web"}],
        },
        "fixVersions": {
            "name": "Fix Version/s",
            "schema": {"type": "array", "items": "version"},
            "allowedValues": [{"id": "20", "name": "1.0"}, {"id": "21", "name": "1.1"}],
        },
        "versions": {
            "name": "Affects Version/s",
            "schema": {"type": "array", "items": "version"},
            "allowedValues": [{"id": "20", "name": "1.0"}],
        },
        "options": {"name": "Choice", "allowedValues": [{"value": "Red"}, {"key": "k"}]},
        "broken": "not an object",
    }
}
ISSUE_TYPES: dict[str, Any] = {
    "values": [
        {"id": "1", "name": "Bug", "subtask": False},
        {"id": "3", "name": "Task", "subtask": False},
        {"id": "5", "name": "Sub-task", "subtask": True},
    ]
}
CREATE_FIELDS: dict[str, Any] = {
    "values": [
        {"fieldId": "project", "name": "Project", "required": True},
        {"fieldId": "issuetype", "name": "Issue Type", "required": True},
        {"fieldId": "summary", "name": "Summary", "required": True},
        {"fieldId": "reporter", "name": "Reporter", "required": True},
        {"fieldId": "description", "name": "Description"},
        {"fieldId": "assignee", "name": "Assignee"},
        {"fieldId": "parent", "name": "Parent"},
        {
            "fieldId": "priority",
            "name": "Priority",
            "required": True,
            "hasDefaultValue": True,
            "allowedValues": [{"id": "2", "name": "High"}],
        },
        {"fieldId": "labels", "name": "Labels"},
        {
            "fieldId": "components",
            "name": "Component/s",
            "allowedValues": [{"id": "10", "name": "API"}],
        },
        {"name": "No id"},
    ]
}
BUG_FIELDS: dict[str, Any] = {
    "values": [
        *CREATE_FIELDS["values"],
        {"fieldId": "customfield_100", "name": "Severity", "required": True},
    ]
}
LINK_TYPES: dict[str, Any] = {
    "issueLinkTypes": [
        {"id": "1", "name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
        {"id": "2", "name": "Duplicate", "inward": "is duplicated by", "outward": "duplicates"},
    ]
}


DEV_STATUS: dict[str, Any] = {
    "errors": [],
    "detail": [
        {
            "pullRequests": [
                {
                    "id": "#42",
                    "name": "ABC-1 retry uploads",
                    "status": "OPEN",
                    "url": "https://host.example/bitbucket/projects/OPS/repos/uploader/pull-requests/42",
                    "author": {"name": "Sam Author"},
                    "source": {"branch": "feature/ABC-1"},
                    "destination": {"branch": "main"},
                    "repositoryName": "uploader",
                },
                {"id": "#7", "name": "Elsewhere", "status": "MERGED", "repositoryName": "other"},
                {"id": "x", "name": "No number"},
            ],
            "branches": [],
        },
        "junk",
    ],
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
        if path == "/jira/rest/api/2/issue/ABC-1/worklog":
            if request.method == "POST":
                return httpx.Response(201, json={"id": 501, **json.loads(request.content)})
            return httpx.Response(200, json=WORKLOGS)
        if path == "/jira/rest/api/2/issue/ABC-1/assignee" and request.method == "PUT":
            return httpx.Response(204)
        if path == "/jira/rest/api/2/issue/ABC-1" and request.method == "PUT":
            return httpx.Response(204)
        if path == "/jira/rest/api/2/issue/ABC-1/editmeta":
            return httpx.Response(200, json=EDITMETA)
        if path == "/jira/rest/api/2/issue/createmeta/ABC/issuetypes":
            return httpx.Response(200, json=ISSUE_TYPES)
        if path.startswith("/jira/rest/api/2/issue/createmeta/ABC/issuetypes/"):
            return httpx.Response(200, json=BUG_FIELDS if path.endswith("/1") else CREATE_FIELDS)
        if path == "/jira/rest/api/2/issue" and request.method == "POST":
            return httpx.Response(201, json={"id": "10002", "key": "ABC-2"})
        if path == "/jira/rest/dev-status/1.0/issue/detail":
            assert request.url.params["issueId"] == "10001"
            return httpx.Response(200, json=DEV_STATUS)
        if path == "/jira/rest/api/2/issueLinkType":
            return httpx.Response(200, json=LINK_TYPES)
        if path == "/jira/rest/api/2/issueLink" and request.method == "POST":
            return httpx.Response(201)
        if path in ("/jira/rest/api/2/user/assignable/search", "/jira/rest/api/2/user/search"):
            wanted = request.url.params["username"].casefold()
            return httpx.Response(
                200,
                json=[
                    p
                    for p in PEOPLE
                    if wanted in p.get("name", "") or wanted in p["displayName"].casefold()
                ],
            )
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
            "products": {"jira": JiraSettings()},
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
        settings = make_settings(**overrides)
        assert settings.products["jira"] is not None
        core = AtlassianClient.from_connection(
            settings.connection_for(settings.products["jira"]),
            token,
            dry_run=settings.dry_run,
            transport=httpx.MockTransport(fake.handler),
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
