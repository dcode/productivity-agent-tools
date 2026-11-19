import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from productivity_agent_tools import cli
from productivity_agent_tools.bitbucket.branches import (
    DEFAULT_PREFIXES,
    branch_name,
    check_ref_name,
    issue_key_of,
    summary_slug,
)
from productivity_agent_tools.bitbucket.client import BitbucketClient
from productivity_agent_tools.bitbucket.settings import BitbucketSettings
from productivity_agent_tools.config import Settings, parse_settings
from productivity_agent_tools.credentials import bearer_token
from productivity_agent_tools.errors import (
    AgentToolsError,
    AuthenticationError,
    ConfigError,
    TokenNotFoundError,
)
from productivity_agent_tools.http import AtlassianClient
from productivity_agent_tools.server import Connected, build_server
from tests.conftest import FakeJira

API = "/bitbucket/rest/api/1.0"
PULL: dict[str, Any] = {
    "id": 42,
    "title": "Add retry to the uploader",
    "description": "Retries **three** times.",
    "state": "OPEN",
    "createdDate": 1794300000000,
    "updatedDate": 1794330000000,
    "fromRef": {"displayId": "feature/retry", "latestCommit": "abc123"},
    "toRef": {
        "displayId": "main",
        "repository": {"slug": "uploader", "project": {"key": "OPS"}},
    },
    "author": {"user": {"displayName": "Sam Author"}},
    "reviewers": [
        {"user": {"displayName": "Pat Example"}, "status": "UNAPPROVED"},
        {"user": {"name": "rlee"}, "status": "APPROVED"},
    ],
    "properties": {"commentCount": 3, "openTaskCount": 1},
    "links": {
        "self": [
            {"href": "https://host.example/bitbucket/projects/OPS/repos/uploader/pull-requests/42"}
        ]
    },
}
ACTIVITIES: dict[str, Any] = {
    "values": [
        {"action": "APPROVED"},
        {
            "action": "COMMENTED",
            "comment": {
                "id": 3,
                "text": "newest",
                "author": {"displayName": "Pat Example"},
                "createdDate": 1794329000000,
            },
            "commentAnchor": {"path": "src/upload.py"},
        },
        {"action": "COMMENTED", "comment": {"id": 2, "text": "middle"}},
        {"action": "COMMENTED", "comment": {"id": 1, "text": "oldest"}},
    ]
}


PROJECT: dict[str, Any] = {
    "key": "OPS",
    "name": "Cloud Operations",
    "description": "Runs the cloud",
    "links": {"self": [{"href": "https://host.example/bitbucket/projects/OPS"}]},
}
REPOSITORY: dict[str, Any] = {
    "slug": "uploader",
    "name": "Uploader",
    "project": {"key": "OPS"},
    "links": {"self": [{"href": "https://host.example/bitbucket/projects/OPS/repos/uploader"}]},
}
COMMITS: list[dict[str, Any]] = [
    {
        "id": "abc123def456",
        "displayId": "abc123d",
        "author": {"name": "sauthor", "displayName": "Sam Author"},
        "authorTimestamp": 1794300000000,
        "message": "Retry uploads",
    },
    {"id": "000111", "author": {"name": "sauthor"}, "message": "Add test"},
]
CHANGES: list[dict[str, Any]] = [
    {"path": {"toString": "src/upload.py"}, "type": "MODIFY"},
    {
        "path": {"toString": "docs/retry.md"},
        "type": "MOVE",
        "srcPath": {"toString": "docs/old.md"},
    },
]
DIFF = "diff --git a/src/upload.py b/src/upload.py\n-old\n+new\n"


SEARCH: dict[str, Any] = {
    "scope": {"type": "GLOBAL"},
    "code": {
        "category": "primary",
        "isLastPage": False,
        "count": 37,
        "values": [
            {
                "repository": {"slug": "uploader", "project": {"key": "OPS"}},
                "file": "src/upload.py",
                "hitCount": 2,
                "hitContexts": [
                    [
                        {"line": 10, "text": "def <em>retry</em>(times):"},
                        {"line": 11, "text": "    if a &lt; b:"},
                    ],
                    [{"line": 40, "text": "<em>retry</em>(3)"}],
                ],
            },
            {"repository": {"slug": "docs"}, "file": "README.md", "hitContexts": None},
        ],
    },
}


BUILD_FULL: dict[str, Any] = {
    "state": "SUCCESSFUL",
    "key": "unit",
    "name": "Unit tests",
    "url": "https://ci.example/2",
    "description": "134 passed",
    "buildNumber": "17",
    "ref": "refs/heads/feature/retry",
    "updatedDate": 1794330000000,
    "duration": 90500,
    "testResults": {"successful": 134, "failed": 0, "skipped": 5},
}
BUILD_OLD: dict[str, Any] = {"state": "FAILED", "key": "lint", "dateAdded": 1794300000000}
REQUIRED: list[dict[str, Any]] = [
    {
        "id": 15,
        "buildParentKeys": ["unit", "lint", 7],
        "refMatcher": {"displayId": "main", "id": "refs/heads/main", "type": {"id": "BRANCH"}},
        "exemptRefMatcher": {"id": "release/*", "type": {"id": "PATTERN"}},
    },
    {"id": 16, "refMatcher": {"id": "ANY_REF_MATCHER_ID", "type": {"id": "ANY_REF"}}},
    {"buildParentKeys": "unit", "refMatcher": {"type": {"id": "BRANCH"}}},
]
DEPLOYMENT: dict[str, Any] = {
    "key": "deploy-api",
    "displayName": "API deploy",
    "description": "Ships the API",
    "deploymentSequenceNumber": 7,
    "state": "SUCCESSFUL",
    "environment": {
        "key": "prod",
        "displayName": "Production",
        "type": "PRODUCTION",
        "url": "https://api.example",
    },
    "fromCommit": {"id": "0001111"},
    "toCommit": {"id": "abc123"},
    "url": "https://cd.example/7",
    "lastUpdated": 1794330000,
}
MERGE: dict[str, Any] = {
    "canMerge": False,
    "conflicted": True,
    "outcome": "CONFLICTED",
    "vetoes": [
        {"summaryMessage": "Needs 2 approvals", "detailedMessage": "1 of 2 reviewers approved."},
        {},
    ],
}
HISTORY: list[dict[str, Any]] = [
    {
        "id": 9,
        "action": "COMMENTED",
        "commentAction": "ADDED",
        "createdDate": 1794330000000,
        "user": {"displayName": "Pat Example"},
        "comment": {
            "id": 3,
            "text": "why?",
            "anchor": {"path": "src/upload.py", "line": 12},
            "comments": [{"id": 4}, {"id": 5}],
        },
    },
    {
        "id": 8,
        "action": "RESCOPED",
        "user": {"name": "sauthor"},
        "added": {"commits": [{"displayId": "abc123d"}, {"id": "fullhash"}]},
        "removed": {"commits": [{"displayId": "0001111"}]},
    },
    {"id": 7, "action": "REVIEWED", "commentAnchor": {"path": "a.py", "line": 3}},
    {},
]
TASKS: list[dict[str, Any]] = [
    {
        "id": 21,
        "text": "Add a test",
        "state": "RESOLVED",
        "author": {"displayName": "Pat Example"},
        "createdDate": 1794300000000,
        "resolver": {"displayName": "Sam Author"},
        "resolvedDate": 1794330000000,
    },
    {"id": 22, "author": {"name": "rlee"}},
]
ISSUES: list[Any] = [
    {"key": "ABC-1", "url": "https://host.example/jira/browse/ABC-1"},
    {"url": "https://nowhere.example"},
    "junk",
]


ISSUE_COMMITS: list[dict[str, Any]] = [
    {
        "repository": {"slug": "uploader", "project": {"key": "OPS"}},
        "toCommit": {
            "id": "abc123def456",
            "displayId": "abc123d",
            "author": {"name": "sauthor"},
            "authorTimestamp": 1794300000000,
            "message": "ABC-1 retry",
        },
    },
    {
        "repository": {"slug": "uploader", "project": {"key": "OPS"}},
        "toCommit": {"id": "def789"},
    },
    {"repository": {"slug": "uploader", "project": {"key": "OPS"}}, "toCommit": {}},
]
OTHER_PULL: dict[str, Any] = {
    **PULL,
    "id": 43,
    "title": "Backport retry",
    "links": {},
}


BRANCH_MODEL: dict[str, Any] = {
    "development": {"refId": "refs/heads/develop", "useDefault": False},
    "production": {"refId": "refs/heads/main", "useDefault": False},
    "types": [
        {"id": "BUGFIX", "displayName": "Bugfix", "prefix": "bugfix/"},
        {"id": "FEATURE", "displayName": "Feature", "prefix": "feat/"},
        {"id": "HOTFIX", "displayName": "Hotfix", "prefix": "hotfix/"},
        {"id": "RELEASE", "displayName": "Release"},
    ],
}


class FakeBitbucket:
    """Answers the Bitbucket endpoints the client uses."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.builds: httpx.Response = httpx.Response(
            200,
            json={"values": [{"state": "FAILED", "key": "unit", "url": "https://ci.example/1"}]},
        )
        self.last_page = True
        self.diff: httpx.Response = httpx.Response(200, text=DIFF)
        self.search: httpx.Response = httpx.Response(200, json=SEARCH)
        self.required: httpx.Response = httpx.Response(
            200, json={"values": REQUIRED, "isLastPage": True}
        )
        self.deployment: httpx.Response = httpx.Response(200, json=DEPLOYMENT)
        self.tasks: httpx.Response = httpx.Response(
            200, json={"values": TASKS, "isLastPage": False}
        )
        self.issues: httpx.Response = httpx.Response(200, json=ISSUES)
        self.whoami: httpx.Response = httpx.Response(200, text="pat.example@corp\n")
        self.merge: httpx.Response = httpx.Response(200, json=MERGE)
        # The X-AUSERNAME header real servers put on REST answers; None sends none.
        self.rest_username: str | None = None
        self.branch_model: httpx.Response = httpx.Response(200, json=BRANCH_MODEL)
        self.branches: list[str] = ["main", "feature/ABC-1-fix-the-thing-old"]
        self.default_branch: httpx.Response = httpx.Response(
            200, json={"id": "refs/heads/main", "displayId": "main"}
        )
        self.review: httpx.Response | None = None
        self.issue_commits: httpx.Response = httpx.Response(
            200, json={"values": ISSUE_COMMITS, "isLastPage": False}
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("authorization") != "Bearer bb-token":
            return httpx.Response(
                401, json={"errors": [{"message": "Authentication failed", "context": None}]}
            )
        path = request.url.path
        if path == "/bitbucket/plugins/servlet/applinks/whoami":
            return self.whoami
        if path == f"{API}/users/pat.example@corp":
            return httpx.Response(
                200, json={"name": "pat.example@corp", "slug": "pat.example_corp"}
            )
        if path == f"{API}/application-properties":
            named = {"X-AUSERNAME": self.rest_username} if self.rest_username else {}
            return httpx.Response(200, json={"version": "9.4.2"}, headers=named)
        if path == f"{API}/dashboard/pull-requests":
            return httpx.Response(200, json={"values": [PULL], "isLastPage": self.last_page})
        if path == "/bitbucket/rest/search/1.0/search" and request.method == "POST":
            return self.search
        if path == f"{API}/projects":
            return httpx.Response(200, json={"values": [PROJECT], "isLastPage": True})
        if path == f"{API}/projects/OPS":
            return httpx.Response(200, json=PROJECT)
        if path == f"{API}/projects/OPS/repos":
            return httpx.Response(200, json={"values": [REPOSITORY], "isLastPage": False})
        if path == "/bitbucket/rest/branch-utils/latest/projects/OPS/repos/uploader/branchmodel":
            return self.branch_model
        if path == f"{API}/projects/OPS/repos/uploader/default-branch":
            return httpx.Response(404, json={"errors": [{"message": "No such resource"}]})
        if path == f"{API}/projects/OPS/repos/uploader/branches/default":
            return self.default_branch
        if path == f"{API}/projects/OPS/repos/uploader/branches":
            if request.method == "POST":
                created = json.loads(request.content)
                self.branches.append(created["name"])
                return httpx.Response(
                    200, json={"id": f"refs/heads/{created['name']}", "displayId": created["name"]}
                )
            wanted = request.url.params["filterText"]
            found = [{"displayId": b} for b in self.branches if wanted in b]
            return httpx.Response(200, json={"values": found, "isLastPage": True})
        if path == f"{API}/projects/OPS/repos/uploader/pull-requests":
            return httpx.Response(200, json={"values": [PULL], "isLastPage": True})
        base = f"{API}/projects/OPS/repos/uploader/pull-requests/42"
        if path == base:
            return httpx.Response(200, json=PULL)
        if path == f"{base}/commits":
            return httpx.Response(200, json={"values": COMMITS, "isLastPage": True})
        if path == f"{base}/changes":
            return httpx.Response(200, json={"values": CHANGES, "isLastPage": True})
        if path == f"{base}.diff":
            return self.diff
        if path == f"{base}/activities":
            if "limit" in request.url.params and request.url.params["limit"] != "100":
                return httpx.Response(200, json={"values": HISTORY, "isLastPage": True})
            return httpx.Response(200, json=ACTIVITIES)
        if path == f"{base}/merge":
            return self.merge
        if path == f"{base}/blocker-comments":
            if request.method == "POST":
                return httpx.Response(201, json={"id": 77, "severity": "BLOCKER"})
            return self.tasks
        if path == f"{base}/comments" and request.method == "POST":
            return httpx.Response(201, json={"id": 76, **json.loads(request.content)})
        if path.startswith(f"{base}/participants/") and request.method == "PUT":
            if self.review is not None:
                return self.review
            return httpx.Response(200, json={"status": json.loads(request.content)["status"]})
        if (
            path
            == "/bitbucket/rest/jira/latest/projects/OPS/repos/uploader/pull-requests/42/issues"
        ):
            return self.issues
        if path == "/bitbucket/rest/jira/latest/issues/ABC-1/commits":
            return self.issue_commits
        if path == f"{API}/projects/OPS/repos/uploader/commits/abc123def456/pull-requests":
            return httpx.Response(200, json={"values": [PULL], "isLastPage": True})
        if path == f"{API}/projects/OPS/repos/uploader/commits/def789/pull-requests":
            return httpx.Response(200, json={"values": [PULL, OTHER_PULL], "isLastPage": True})
        if path == "/bitbucket/rest/build-status/1.0/commits/abc123":
            return self.builds
        if path == "/bitbucket/rest/build-status/1.0/commits/stats/abc123":
            return httpx.Response(200, json={"successful": 3, "failed": 1, "inProgress": 2})
        if path == "/bitbucket/rest/required-builds/latest/projects/OPS/repos/uploader/conditions":
            return self.required
        if path == f"{API}/projects/OPS/repos/uploader/commits/abc123/deployments":
            return self.deployment
        return httpx.Response(
            404, json={"errors": [{"message": "Pull request 7 does not exist in OPS/uploader."}]}
        )


@pytest.fixture
def bitbucket() -> FakeBitbucket:
    return FakeBitbucket()


@pytest.fixture
async def make_api(bitbucket: FakeBitbucket) -> AsyncIterator[Callable[..., AtlassianClient]]:
    created: list[AtlassianClient] = []

    def build(token: str = "bb-token", dry_run: bool = True) -> AtlassianClient:
        api = AtlassianClient(
            base_url="https://host.example",
            credentials=bearer_token(token),
            dry_run=dry_run,
            transport=httpx.MockTransport(bitbucket.handler),
        )
        created.append(api)
        return api

    yield build
    for api in created:
        await api.aclose()


@pytest.fixture
def client(make_api: Callable[..., AtlassianClient]) -> BitbucketClient:
    return BitbucketClient(make_api(), BitbucketSettings())


async def test_list_asks_for_role_and_state_and_returns_compact_rows(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    result = await client.list_pull_requests(" Reviewer ", "OPEN", 500)
    assert dict(bitbucket.requests[0].url.params) == {
        "role": "REVIEWER",
        "limit": "100",
        "order": "NEWEST",
        "state": "OPEN",
    }
    assert (result.returned, result.more_available) == (1, False)
    row = result.pull_requests[0]
    assert (row.project, row.repository, row.id) == ("OPS", "uploader", 42)
    assert (row.source_branch, row.target_branch) == ("feature/retry", "main")
    assert [(r.name, r.status) for r in row.reviewers] == [
        ("Pat Example", "UNAPPROVED"),
        ("rlee", "APPROVED"),
    ]
    assert (row.comment_count, row.open_task_count) == (3, 1)
    assert row.updated == "2026-11-10T17:00:00+00:00"
    assert row.url is not None and row.url.endswith("/pull-requests/42")


async def test_all_states_omits_the_filter_and_more_pages_are_flagged(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    bitbucket.last_page = False
    result = await client.list_pull_requests("author", "all")
    params = dict(bitbucket.requests[0].url.params)
    assert "state" not in params
    assert (params["role"], params["limit"]) == ("AUTHOR", "25")
    assert result.more_available is True


async def test_detail_has_newest_comments_oldest_first_and_builds(
    client: BitbucketClient,
) -> None:
    detail = await client.get_pull_request("uploader", 42, "OPS", max_comments=2)
    assert detail.description == "Retries **three** times."
    assert detail.created == "2026-11-10T08:40:00+00:00"
    assert detail.latest_commit == "abc123"
    assert [c.text for c in detail.comments] == ["middle", "newest"]
    assert (detail.comments[1].author, detail.comments[1].file) == ("Pat Example", "src/upload.py")
    assert [(b.state, b.name, b.url) for b in detail.builds] == [
        ("FAILED", "unit", "https://ci.example/1")
    ]
    assert detail.builds_available is True


async def test_no_comments_requested_skips_the_activity_call(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    detail = await client.get_pull_request("uploader", 42, "OPS", max_comments=0)
    assert detail.comments == []
    assert not any(r.url.path.endswith("/activities") for r in bitbucket.requests)


async def test_unreadable_builds_are_reported_as_unknown_not_as_a_failure(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    bitbucket.builds = httpx.Response(404, json={"errors": [{"message": "no such plugin"}]})
    detail = await client.get_pull_request("uploader", 42, "OPS")
    assert (detail.builds, detail.builds_available) == ([], False)


@pytest.mark.parametrize(
    ("project", "repository", "number"),
    [("..", "uploader", 42), ("OPS", "a/b", 42), ("OPS", "", 42), ("OPS", "uploader", 0)],
)
async def test_identifiers_are_validated_before_any_request(
    bitbucket: FakeBitbucket, client: BitbucketClient, project: str, repository: str, number: int
) -> None:
    with pytest.raises(AgentToolsError, match="not a valid"):
        await client.get_pull_request(repository, number, project)
    assert bitbucket.requests == []


async def test_bad_role_and_state_list_the_choices(client: BitbucketClient) -> None:
    with pytest.raises(AgentToolsError, match="reviewer, author, participant"):
        await client.list_pull_requests("owner")
    with pytest.raises(AgentToolsError, match="open, merged, declined, all"):
        await client.list_pull_requests(state="closed")


async def test_bitbucket_error_messages_reach_the_user(
    make_api: Callable[..., AtlassianClient], client: BitbucketClient
) -> None:
    with pytest.raises(AgentToolsError, match=r"Pull request 7 does not exist in OPS/uploader"):
        await client.get_pull_request("uploader", 7, "OPS")
    expired = BitbucketClient(make_api(token="old"), BitbucketSettings())
    with pytest.raises(AuthenticationError, match="Authentication failed"):
        await expired.server_version()


def _settings(**bitbucket: Any) -> Settings:
    return parse_settings(
        {
            "base_url": "https://host.example",
            "cookies": {"consent": "yes"},
            "jira": {},
            "bitbucket": bitbucket,
        }
    )


def test_bitbucket_is_off_until_its_table_exists() -> None:
    assert "bitbucket" not in parse_settings({"base_url": "https://host.example"}).products
    settings = _settings()
    assert settings.products["bitbucket"] == BitbucketSettings()
    assert (
        settings.connection_for(settings.products["bitbucket"]).base_url == "https://host.example"
    )


def test_bitbucket_table_overrides_and_is_validated() -> None:
    settings = _settings(base_url="https://git.example", path="", default_max_results=5, cookies={})
    bitbucket = settings.product("bitbucket", BitbucketSettings)
    assert bitbucket is not None
    assert (bitbucket.path, bitbucket.default_max_results) == ("", 5)
    connection = settings.connection_for(settings.products["bitbucket"])
    assert (connection.base_url, connection.cookies) == ("https://git.example", {})
    for bad in ({"default_max_results": 0}, {"base_url": "http://git.example"}):
        with pytest.raises(ConfigError, match="bitbucket"):
            _settings(**bad)
    with pytest.raises(ConfigError, match="'bitbucket' must be a table"):
        parse_settings({"base_url": "https://host.example", "bitbucket": "yes"})


def _structured(result: Any) -> dict[str, Any]:
    assert not result.is_error, result.content
    return dict(result.structured_content)


async def test_tools_are_registered_only_when_bitbucket_is_connected(
    fake: FakeJira,
    make_core: Callable[..., AtlassianClient],
    make_api: Callable[..., AtlassianClient],
) -> None:
    settings = _settings()
    without = build_server(settings, {"jira": Connected(make_core(), "jira source")})
    assert not [t.name for t in await without.list_tools() if t.name.startswith("bitbucket_")]
    assert "bitbucket_" not in (without.instructions or "")

    server = build_server(
        settings,
        {
            "jira": Connected(make_core(), "jira source"),
            "bitbucket": Connected(make_api(), "bb source"),
        },
    )
    tools = {t.name: t for t in await server.list_tools() if t.name.startswith("bitbucket_")}
    assert sorted(tools) == [
        "bitbucket_add_pull_request_comment",
        "bitbucket_create_branch",
        "bitbucket_get_commit_builds",
        "bitbucket_get_deployment",
        "bitbucket_get_pull_request",
        "bitbucket_get_pull_request_activity",
        "bitbucket_get_pull_request_changes",
        "bitbucket_get_pull_request_commits",
        "bitbucket_get_pull_request_diff",
        "bitbucket_get_pull_request_issues",
        "bitbucket_get_pull_request_merge_status",
        "bitbucket_list_projects",
        "bitbucket_list_pull_request_tasks",
        "bitbucket_list_pull_requests",
        "bitbucket_list_repositories",
        "bitbucket_list_repository_pull_requests",
        "bitbucket_list_required_builds",
        "bitbucket_review_pull_request",
        "bitbucket_search_code",
        "bitbucket_set_default_project",
        "bitbucket_status",
    ]
    not_read_only = sorted(
        name
        for name, tool in tools.items()
        if tool.annotations is None or not tool.annotations.read_only_hint
    )
    assert not_read_only == [
        "bitbucket_add_pull_request_comment",
        "bitbucket_create_branch",
        "bitbucket_review_pull_request",
        "bitbucket_set_default_project",
    ]
    assert "bitbucket_" in (server.instructions or "")

    status = _structured(await server.call_tool("bitbucket_status", {}))
    assert status == {
        "base_url": "https://host.example/bitbucket",
        "bitbucket_version": "9.4.2",
        "token_source": "bb source",
        "authenticated": True,
        "user_slug": "pat.example_corp",
        "dry_run": True,
        "default_project": None,
        "extra_cookies": ["consent"],
    }
    listed = _structured(await server.call_tool("bitbucket_list_pull_requests", {}))
    assert listed["pull_requests"][0]["title"] == "Add retry to the uploader"
    detail = _structured(
        await server.call_tool(
            "bitbucket_get_pull_request",
            {"project": "OPS", "repository": "uploader", "pull_request_id": 42},
        )
    )
    assert detail["builds"][0]["state"] == "FAILED"

    chosen = _structured(
        await server.call_tool("bitbucket_set_default_project", {"project": "OPS"})
    )
    assert (chosen["key"], chosen["name"]) == ("OPS", "Cloud Operations")
    assert "until the server restarts" in chosen["message"]
    assert _structured(await server.call_tool("bitbucket_status", {}))["default_project"] == "OPS"
    for tool, key in [
        ("bitbucket_list_repositories", "repositories"),
        ("bitbucket_list_projects", "projects"),
    ]:
        assert _structured(await server.call_tool(tool, {}))[key]
    pull = {"repository": "uploader", "pull_request_id": 42}
    for tool, key in [
        ("bitbucket_get_pull_request", "title"),
        ("bitbucket_get_pull_request_commits", "commits"),
        ("bitbucket_get_pull_request_changes", "files"),
        ("bitbucket_get_pull_request_diff", "diff"),
        ("bitbucket_get_pull_request_merge_status", "vetoes"),
        ("bitbucket_get_pull_request_activity", "activities"),
        ("bitbucket_list_pull_request_tasks", "tasks"),
        ("bitbucket_get_pull_request_issues", "issues"),
    ]:
        assert _structured(await server.call_tool(tool, pull))[key]
    for tool, arguments, key in [
        ("bitbucket_get_commit_builds", {"commit_id": "abc123"}, "builds"),
        ("bitbucket_list_required_builds", {"repository": "uploader"}, "checks"),
        (
            "bitbucket_get_deployment",
            {
                "repository": "uploader",
                "commit_id": "abc123",
                "deployment_key": "deploy-api",
                "environment_key": "prod",
                "sequence_number": 7,
            },
            "environment",
        ),
    ]:
        assert _structured(await server.call_tool(tool, arguments))[key]
    listed_in_repo = _structured(
        await server.call_tool(
            "bitbucket_list_repository_pull_requests", {"repository": "uploader"}
        )
    )
    assert listed_in_repo["project"] == "OPS"


def _both(fake: FakeJira, bitbucket: FakeBitbucket) -> httpx.MockTransport:
    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/bitbucket/"):
            return bitbucket.handler(request)
        return fake.handler(request)

    return httpx.MockTransport(route)


async def test_doctor_checks_bitbucket_when_configured(
    fake: FakeJira,
    bitbucket: FakeBitbucket,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("JIRA_TOKEN", "good-token")
    monkeypatch.setenv("BITBUCKET_TOKEN", "bb-token")
    bitbucket.last_page = False
    assert await cli._doctor(Path("/cfg.toml"), _settings(), _both(fake, bitbucket)) is True
    output = capsys.readouterr().out
    assert "[ok]   bitbucket token found in environment variable BITBUCKET_TOKEN" in output
    assert "[ok]   Bitbucket version 9.4.2 at https://host.example/bitbucket" in output
    assert "[ok]   pull requests readable (1+ open with you as reviewer)" in output

    monkeypatch.setenv("BITBUCKET_TOKEN", "expired")
    assert await cli._doctor(Path("/cfg.toml"), _settings(), _both(fake, bitbucket)) is False
    failed = capsys.readouterr().out
    assert "[ok]   search works" in failed
    assert "[fail] The server rejected the access token" in failed


async def test_doctor_says_when_bitbucket_is_not_configured(
    fake: FakeJira,
    make_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("JIRA_TOKEN", "good-token")
    assert await cli._doctor(Path("/cfg.toml"), make_settings(), httpx.MockTransport(fake.handler))
    assert "[info] bitbucket is not configured" in capsys.readouterr().out


async def test_serve_connects_bitbucket_and_needs_its_token(
    fake: FakeJira, bitbucket: FakeBitbucket, monkeypatch: pytest.MonkeyPatch
) -> None:
    served: list[list[str]] = []

    async def run_stdio(self: MCPServer) -> None:
        served.append([tool.name for tool in await self.list_tools()])

    async def no_keychain(product: str) -> None:
        return None

    monkeypatch.setattr(MCPServer, "run_stdio_async", run_stdio)
    monkeypatch.setattr("productivity_agent_tools.tokens._from_keychain", no_keychain)
    monkeypatch.setenv("JIRA_TOKEN", "good-token")
    monkeypatch.delenv("BITBUCKET_TOKEN", raising=False)
    with pytest.raises(TokenNotFoundError, match="BITBUCKET_TOKEN"):
        await cli._serve(_settings(), _both(fake, bitbucket))

    monkeypatch.setenv("BITBUCKET_TOKEN", "bb-token")
    await cli._serve(_settings(), _both(fake, bitbucket))
    assert "bitbucket_list_pull_requests" in served[0]
    assert "jira_search" in served[0]


def test_set_token_rejects_unknown_products(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        cli.set_token("gitlab")
    assert info.value.code == 1
    assert "Tokens: bitbucket, confluence, jira, microsoft." in capsys.readouterr().err


async def test_projects_can_be_listed_and_filtered_by_name(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    result = await client.list_projects(" cloud ", 5)
    assert dict(bitbucket.requests[0].url.params) == {"limit": "5", "name": "cloud"}
    project = result.projects[0]
    assert (project.key, project.name, project.description) == (
        "OPS",
        "Cloud Operations",
        "Runs the cloud",
    )
    assert project.url == "https://host.example/bitbucket/projects/OPS"
    await client.list_projects()
    assert "name" not in dict(bitbucket.requests[1].url.params)


async def test_calls_without_a_project_need_a_default(client: BitbucketClient) -> None:
    with pytest.raises(AgentToolsError, match="bitbucket_set_default_project"):
        await client.list_repositories()
    with pytest.raises(AgentToolsError, match="default_project under"):
        await client.get_pull_request("uploader", 42)


async def test_default_project_comes_from_config_or_is_chosen_for_the_session(
    make_api: Callable[..., AtlassianClient],
) -> None:
    configured = BitbucketClient(make_api(), BitbucketSettings(default_project="OPS"))
    assert configured.default_project == "OPS"
    assert (await configured.list_repositories()).project == "OPS"

    chosen = BitbucketClient(make_api(), BitbucketSettings())
    with pytest.raises(AgentToolsError, match="does not exist"):
        await chosen.set_default_project("NOPE")
    assert chosen.default_project is None
    await chosen.set_default_project(" OPS ")
    repositories = await chosen.list_repositories()
    assert (repositories.returned, repositories.more_available) == (1, True)
    repository = repositories.repositories[0]
    assert (repository.project, repository.slug, repository.name) == ("OPS", "uploader", "Uploader")
    assert repository.url is not None and repository.url.endswith("/repos/uploader")
    assert (await chosen.list_repositories("OPS")).project == "OPS"


def test_default_project_is_read_from_config() -> None:
    settings = _settings(default_project=" PROJ ")
    bitbucket = settings.product("bitbucket", BitbucketSettings)
    assert bitbucket is not None and bitbucket.default_project == "PROJ"
    assert _settings().products["bitbucket"] == BitbucketSettings()


async def test_repository_pull_requests_filter_by_state_and_text(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    result = await client.list_repository_pull_requests("uploader", "OPS", "all", " retry ", 10)
    assert dict(bitbucket.requests[0].url.params) == {
        "state": "ALL",
        "order": "NEWEST",
        "limit": "10",
        "filterText": "retry",
    }
    assert (result.project, result.repository, result.state) == ("OPS", "uploader", "all")
    assert result.pull_requests[0].id == 42
    await client.list_repository_pull_requests("uploader", "OPS")
    assert dict(bitbucket.requests[1].url.params)["state"] == "OPEN"
    assert "filterText" not in dict(bitbucket.requests[1].url.params)


async def test_commits_and_changed_files(client: BitbucketClient) -> None:
    commits = await client.get_pull_request_commits("uploader", 42, "OPS")
    assert [(c.short_id, c.author, c.message) for c in commits.commits] == [
        ("abc123d", "Sam Author", "Retry uploads"),
        (None, "sauthor", "Add test"),
    ]
    assert commits.commits[0].authored == "2026-11-10T08:40:00+00:00"
    changes = await client.get_pull_request_changes("uploader", 42, "OPS")
    assert [(f.path, f.change, f.previous_path) for f in changes.files] == [
        ("src/upload.py", "MODIFY", None),
        ("docs/retry.md", "MOVE", "docs/old.md"),
    ]


async def test_diff_is_requested_as_text_and_cut_to_size(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    whole = await client.get_pull_request_diff("uploader", 42, "OPS", context_lines=99)
    request = bitbucket.requests[0]
    assert request.url.path.endswith("/pull-requests/42.diff")
    assert request.headers["accept"] == "text/plain"
    assert dict(request.url.params) == {"contextLines": "50"}
    assert (whole.diff, whole.total_chars, whole.truncated) == (DIFF, len(DIFF), False)

    cut = await client.get_pull_request_diff("uploader", 42, "OPS", max_chars=10)
    assert (cut.diff, cut.total_chars, cut.truncated) == (DIFF[:10], len(DIFF), True)

    bitbucket.diff = httpx.Response(200)
    empty = await client.get_pull_request_diff("uploader", 42, "OPS")
    assert (empty.diff, empty.truncated) == ("", False)


async def test_code_search_posts_the_query_and_cleans_up_the_hits(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    result = await client.search_code(" retry ext:py ", max_results=500)
    request = bitbucket.requests[0]
    assert request.method == "POST"
    assert json.loads(request.content) == {
        "query": "retry ext:py",
        "entities": {"code": {}},
        "limits": {"primary": 100, "secondary": 10},
    }
    assert (result.query, result.total, result.returned, result.more_available) == (
        "retry ext:py",
        37,
        2,
        True,
    )
    first, second = result.hits
    assert (first.project, first.repository, first.file, first.hit_count) == (
        "OPS",
        "uploader",
        "src/upload.py",
        2,
    )
    assert [(line.line, line.text) for line in first.lines] == [
        (10, "def retry(times):"),
        (11, "    if a < b:"),
        (40, "retry(3)"),
    ]
    assert (
        first.url
        == "https://host.example/bitbucket/projects/OPS/repos/uploader/browse/src/upload.py"
    )
    assert (second.project, second.lines, second.url) == ("", [], None)


async def test_code_search_scopes_only_when_asked_and_runs_in_dry_run(
    bitbucket: FakeBitbucket, make_api: Callable[..., AtlassianClient]
) -> None:
    api = make_api()
    assert api.dry_run is True
    client = BitbucketClient(api, BitbucketSettings(default_project="OPS"))
    await client.search_code("retry")
    await client.search_code("retry", project="PROJ", repository="uploader")
    queries = [json.loads(request.content)["query"] for request in bitbucket.requests]
    assert queries == ["retry", "retry project:PROJ repo:uploader"]


async def test_code_search_problems_are_explained(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    with pytest.raises(AgentToolsError, match="query is empty"):
        await client.search_code("   ")
    with pytest.raises(AgentToolsError, match="not a valid project key"):
        await client.search_code("retry", project="a b")
    assert bitbucket.requests == []

    bitbucket.search = httpx.Response(404, json={"errors": [{"message": "No such resource"}]})
    with pytest.raises(AgentToolsError, match="does not document"):
        await client.search_code("retry")

    bitbucket.search = httpx.Response(503)
    with pytest.raises(AgentToolsError, match=r"unavailable right now \(HTTP 503\).*one by one"):
        await client.search_code("retry")

    bitbucket.search = httpx.Response(400, json={"errors": [{"message": "Bad query syntax"}]})
    with pytest.raises(AgentToolsError, match="Bad query syntax"):
        await client.search_code("retry")

    bitbucket.search = httpx.Response(200, json={"scope": {"type": "GLOBAL"}})
    empty = await client.search_code("retry")
    assert (empty.total, empty.returned, empty.more_available, empty.hits) == (None, 0, False, [])


def _bitbucket_only() -> Settings:
    return parse_settings({"base_url": "https://host.example", "bitbucket": {}})


async def test_bitbucket_alone_offers_no_jira_tools_and_needs_no_jira_token(
    bitbucket: FakeBitbucket,
    make_api: Callable[..., AtlassianClient],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def no_keychain(product: str) -> None:
        return None

    monkeypatch.setattr("productivity_agent_tools.tokens._from_keychain", no_keychain)
    monkeypatch.delenv("JIRA_TOKEN", raising=False)
    monkeypatch.setenv("BITBUCKET_TOKEN", "bb-token")
    transport = httpx.MockTransport(bitbucket.handler)

    listing = await cli._call(_bitbucket_only(), None, (), transport)
    assert "bitbucket_status" in listing
    assert "jira_" not in listing

    server = build_server(_bitbucket_only(), {"bitbucket": Connected(make_api())})
    assert "jira_" not in (server.instructions or "")
    assert (server.instructions or "").startswith("The bitbucket_ tools")

    assert await cli._doctor(Path("/cfg.toml"), _bitbucket_only(), transport) is True
    output = capsys.readouterr().out
    assert "[info] jira is not configured" in output
    assert "[ok]   Bitbucket version 9.4.2" in output


async def test_jira_adapter_without_a_jira_table_registers_nothing(
    make_core: Callable[..., AtlassianClient],
) -> None:
    server = build_server(_bitbucket_only(), {"jira": Connected(make_core(), "jira source")})
    assert await server.list_tools() == []


async def test_pull_request_builds_carry_the_richer_fields_when_reported(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    bitbucket.builds = httpx.Response(200, json={"values": [BUILD_FULL, BUILD_OLD]})
    detail = await client.get_pull_request("uploader", 42, "OPS", max_comments=0)
    full, old = detail.builds
    assert (full.name, full.key, full.build_number, full.ref) == (
        "Unit tests",
        "unit",
        "17",
        "refs/heads/feature/retry",
    )
    assert (full.updated, full.duration_seconds, full.description) == (
        "2026-11-10T17:00:00+00:00",
        90.5,
        "134 passed",
    )
    assert full.tests is not None
    assert (full.tests.successful, full.tests.failed, full.tests.skipped) == (134, 0, 5)
    assert (old.name, old.key, old.updated, old.tests, old.duration_seconds) == (
        "lint",
        "lint",
        "2026-11-10T08:40:00+00:00",
        None,
        None,
    )


async def test_commit_builds_have_totals_and_newest_first(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    bitbucket.builds = httpx.Response(200, json={"values": [BUILD_FULL], "isLastPage": False})
    result = await client.get_commit_builds(" abc123 ", 500)
    stats, page = bitbucket.requests
    assert stats.url.path.endswith("/commits/stats/abc123")
    assert dict(page.url.params) == {"limit": "100", "orderBy": "NEWEST"}
    assert result.commit == "abc123"
    assert result.counts.model_dump() == {
        "successful": 3,
        "failed": 1,
        "in_progress": 2,
        "cancelled": 0,
        "unknown": 0,
    }
    assert (result.returned, result.more_available, result.builds[0].key) == (1, True, "unit")


@pytest.mark.parametrize("commit", ["", "abc", "xyz123", "abc123/../x"])
async def test_commit_hashes_are_validated_before_any_request(
    bitbucket: FakeBitbucket, client: BitbucketClient, commit: str
) -> None:
    with pytest.raises(AgentToolsError, match="not a valid commit hash"):
        await client.get_commit_builds(commit)
    assert bitbucket.requests == []


async def test_required_builds_describe_their_branches(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    result = await client.list_required_builds("uploader", "OPS")
    assert (result.project, result.repository, result.returned) == ("OPS", "uploader", 3)
    first, second, third = result.checks
    assert (first.id, first.build_keys, first.branches, first.exempt_branches) == (
        15,
        ["unit", "lint"],
        "main",
        "pattern: release/*",
    )
    assert (second.branches, second.exempt_branches, second.build_keys) == ("any branch", None, [])
    assert (third.id, third.build_keys, third.branches) == (None, [], None)

    bitbucket.required = httpx.Response(404, json={"errors": [{"message": "Not found"}]})
    with pytest.raises(AgentToolsError, match=r"older than 7\.14"):
        await client.list_required_builds("uploader", "OPS")


async def test_deployment_is_fetched_by_its_three_identifiers(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    found = await client.get_deployment("uploader", "abc123", " deploy-api ", " prod ", 7, "OPS")
    assert dict(bitbucket.requests[0].url.params) == {
        "key": "deploy-api",
        "environmentKey": "prod",
        "deploymentSequenceNumber": "7",
    }
    assert (found.key, found.name, found.state, found.sequence_number) == (
        "deploy-api",
        "API deploy",
        "SUCCESSFUL",
        7,
    )
    assert found.environment is not None
    assert (found.environment.key, found.environment.name, found.environment.type) == (
        "prod",
        "Production",
        "PRODUCTION",
    )
    assert (found.from_commit, found.to_commit, found.url) == (
        "0001111",
        "abc123",
        "https://cd.example/7",
    )
    assert found.updated == "2026-11-10T17:00:00+00:00"

    bitbucket.deployment = httpx.Response(
        200, json={"environment": {}, "lastUpdated": 1794330000000}
    )
    sparse = await client.get_deployment("uploader", "abc123", "deploy-api", "prod", 7, "OPS")
    assert (sparse.key, sparse.state, sparse.updated) == (
        "deploy-api",
        "UNKNOWN",
        "2026-11-10T17:00:00+00:00",
    )
    assert sparse.environment is not None and sparse.environment.key == "prod"
    bitbucket.deployment = httpx.Response(200, json={})
    assert (
        await client.get_deployment("uploader", "abc123", "d", "e", 1, "OPS")
    ).environment is None


async def test_deployment_problems_are_explained(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    with pytest.raises(AgentToolsError, match="both its key and its environment key"):
        await client.get_deployment("uploader", "abc123", " ", "prod", 7, "OPS")
    assert bitbucket.requests == []
    bitbucket.deployment = httpx.Response(404, json={"errors": [{"message": "No deployment"}]})
    with pytest.raises(AgentToolsError, match="cannot list deployments"):
        await client.get_deployment("uploader", "abc123", "deploy-api", "prod", 7, "OPS")


async def test_merge_status_lists_what_blocks_the_merge(client: BitbucketClient) -> None:
    status = await client.get_merge_status("uploader", 42, "OPS")
    assert (status.can_merge, status.conflicted, status.outcome) == (False, True, "CONFLICTED")
    assert [(v.summary, v.detail) for v in status.vetoes] == [
        ("Needs 2 approvals", "1 of 2 reviewers approved."),
        ("Blocked by a merge check.", None),
    ]


async def test_merge_status_of_a_merged_pull_request_is_a_note_not_an_error(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    merged = {"errors": [{"message": "This pull request has already been merged."}]}
    bitbucket.merge = httpx.Response(409, json=merged)
    status = await client.get_merge_status("uploader", 42, "OPS")
    assert (status.can_merge, status.conflicted, status.vetoes) == (False, False, [])
    assert status.note == (
        "The server returned HTTP 409. (This pull request has already been merged.)"
    )
    bitbucket.merge = httpx.Response(500, json={})
    with pytest.raises(AgentToolsError, match="HTTP 500"):
        await client.get_merge_status("uploader", 42, "OPS")


async def test_user_slug_comes_from_the_username_header_of_a_rest_answer(
    bitbucket: FakeBitbucket, make_api: Callable[..., AtlassianClient]
) -> None:
    # As on Bitbucket 10.2.8: REST answers name the user, the whoami servlet does not.
    bitbucket.whoami = httpx.Response(200, text="")
    bitbucket.rest_username = "pat.example%40corp"
    api = make_api()
    assert api.username is None
    assert await BitbucketClient(api, BitbucketSettings()).user_slug() == "pat.example_corp"
    assert api.username == "pat.example@corp"
    properties, lookup = bitbucket.requests
    assert properties.url.path.endswith("/application-properties")
    assert lookup.url.path.endswith("/users/pat.example@corp")

    bitbucket.rest_username = "anonymous"
    unknown = make_api()
    assert await BitbucketClient(unknown, BitbucketSettings()).user_slug(False) is None
    assert unknown.username is None


async def test_a_username_already_seen_skips_the_whoami_servlet(
    bitbucket: FakeBitbucket, make_api: Callable[..., AtlassianClient]
) -> None:
    api = make_api()
    api.username = "pat.example@corp"
    assert await BitbucketClient(api, BitbucketSettings()).user_slug() == "pat.example_corp"
    (lookup,) = bitbucket.requests
    assert lookup.url.path.endswith("/users/pat.example@corp")


async def test_ci_false_hides_build_and_deployment_tools_and_skips_build_reads(
    bitbucket: FakeBitbucket, make_api: Callable[..., AtlassianClient]
) -> None:
    settings = _settings(ci=False)
    server = build_server(settings, {"bitbucket": Connected(make_api())})
    names = {t.name for t in await server.list_tools()}
    assert "bitbucket_get_pull_request" in names
    assert not names & {
        "bitbucket_get_commit_builds",
        "bitbucket_list_required_builds",
        "bitbucket_get_deployment",
    }
    detail = await BitbucketClient(make_api(), BitbucketSettings(ci=False)).get_pull_request(
        "uploader", 42, "OPS"
    )
    assert (detail.builds, detail.builds_available) == ([], False)
    assert not any("build-status" in r.url.path for r in bitbucket.requests)
    with pytest.raises(ConfigError, match="'ci' must be true/false"):
        _settings(ci="no")


async def test_activity_reads_comments_pushes_and_reviews(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    result = await client.get_pull_request_activity("uploader", 42, "OPS", 5)
    assert dict(bitbucket.requests[0].url.params) == {"limit": "5"}
    comment, push, review, empty = result.activities
    assert (comment.action, comment.user, comment.comment, comment.comment_id) == (
        "COMMENTED",
        "Pat Example",
        "why?",
        3,
    )
    assert (comment.comment_action, comment.file, comment.line, comment.replies) == (
        "ADDED",
        "src/upload.py",
        12,
        2,
    )
    assert comment.created == "2026-11-10T17:00:00+00:00"
    assert (push.user, push.commits_added, push.commits_removed) == (
        "sauthor",
        ["abc123d", "fullhash"],
        ["0001111"],
    )
    assert (review.action, review.file, review.line, review.replies) == ("REVIEWED", "a.py", 3, 0)
    assert (empty.action, empty.id, empty.user) == ("UNKNOWN", None, None)


async def test_tasks_filter_by_state(bitbucket: FakeBitbucket, client: BitbucketClient) -> None:
    result = await client.list_pull_request_tasks("uploader", 42, "OPS", " Resolved ")
    assert dict(bitbucket.requests[0].url.params) == {"limit": "25", "state": "RESOLVED"}
    assert (result.state, result.returned, result.more_available) == ("resolved", 2, True)
    done, open_task = result.tasks
    assert (done.id, done.text, done.state, done.author) == (
        21,
        "Add a test",
        "RESOLVED",
        "Pat Example",
    )
    assert (done.resolved_by, done.resolved) == ("Sam Author", "2026-11-10T17:00:00+00:00")
    assert (open_task.text, open_task.state, open_task.author) == ("", "OPEN", "rlee")

    await client.list_pull_request_tasks("uploader", 42, "OPS", "all")
    assert "state" not in dict(bitbucket.requests[1].url.params)
    with pytest.raises(AgentToolsError, match="open, resolved, all"):
        await client.list_pull_request_tasks("uploader", 42, "OPS", "closed")
    bitbucket.tasks = httpx.Response(404, json={"errors": [{"message": "Not found"}]})
    with pytest.raises(AgentToolsError, match=r"older than 7\.2,"):
        await client.list_pull_request_tasks("uploader", 42, "OPS")


async def test_linked_jira_issues_skip_entries_without_a_key(
    bitbucket: FakeBitbucket, client: BitbucketClient
) -> None:
    result = await client.get_pull_request_issues("uploader", 42, "OPS")
    assert [(i.key, i.url) for i in result.issues] == [
        ("ABC-1", "https://host.example/jira/browse/ABC-1")
    ]
    assert result.returned == 1
    bitbucket.issues = httpx.Response(404, json={"errors": [{"message": "Not found"}]})
    with pytest.raises(AgentToolsError, match="not linked to Jira"):
        await client.get_pull_request_issues("uploader", 42, "OPS")


def _live(make_api: Callable[..., AtlassianClient], **settings: Any) -> BitbucketClient:
    return BitbucketClient(make_api(dry_run=False), BitbucketSettings(**settings))


def _writes(bitbucket: FakeBitbucket) -> list[httpx.Request]:
    return [r for r in bitbucket.requests if r.method in ("POST", "PUT")]


async def test_user_slug_comes_from_whoami_and_is_asked_for_once(
    bitbucket: FakeBitbucket, make_api: Callable[..., AtlassianClient]
) -> None:
    client = _live(make_api)
    assert await client.user_slug() == "pat.example_corp"
    assert await client.user_slug() == "pat.example_corp"
    _properties, whoami, lookup = bitbucket.requests
    assert whoami.headers["accept"] == "text/plain"
    assert lookup.url.raw_path.endswith(b"/users/pat.example%40corp")


async def test_configured_user_slug_skips_the_lookup(
    bitbucket: FakeBitbucket, make_api: Callable[..., AtlassianClient]
) -> None:
    assert await _live(make_api, user_slug="me").user_slug() == "me"
    assert bitbucket.requests == []


async def test_unknown_user_is_explained_or_tolerated(
    bitbucket: FakeBitbucket, make_api: Callable[..., AtlassianClient]
) -> None:
    bitbucket.whoami = httpx.Response(200, text="  ")
    client = _live(make_api)
    assert await client.user_slug(required=False) is None
    with pytest.raises(AgentToolsError, match="Set user_slug under"):
        await client.user_slug()

    bitbucket.whoami = httpx.Response(404, json={"errors": [{"message": "No servlet"}]})
    failing = _live(make_api)
    assert await failing.user_slug(required=False) is None
    with pytest.raises(AgentToolsError, match="No servlet"):
        await failing.user_slug()


async def test_comment_reply_and_task_go_to_the_right_place(
    bitbucket: FakeBitbucket, make_api: Callable[..., AtlassianClient]
) -> None:
    client = _live(make_api)
    plain = await client.add_comment("uploader", 42, "Looks good", "OPS")
    reply = await client.add_comment("uploader", 42, "Agreed", "OPS", reply_to=3)
    task = await client.add_comment("uploader", 42, "Add a test", "OPS", task=True)
    paths = [r.url.path.rsplit("/", 1)[-1] for r in _writes(bitbucket)]
    assert paths == ["comments", "comments", "blocker-comments"]
    assert [json.loads(r.content) for r in _writes(bitbucket)] == [
        {"text": "Looks good"},
        {"text": "Agreed", "parent": {"id": 3}},
        {"text": "Add a test"},
    ]
    assert all(r.headers["x-atlassian-token"] == "no-check" for r in _writes(bitbucket))
    assert (plain.sent, reply.sent, task.sent) == (True, True, True)


@pytest.mark.parametrize(
    ("text", "reply_to", "number", "message"),
    [
        ("  ", None, 42, "must not be empty"),
        ("hi", 0, 42, "not a valid comment identifier"),
        ("hi", None, 0, "not a valid pull request number"),
    ],
)
async def test_bad_comments_are_refused_before_any_request(
    bitbucket: FakeBitbucket,
    make_api: Callable[..., AtlassianClient],
    text: str,
    reply_to: int | None,
    number: int,
    message: str,
) -> None:
    with pytest.raises(AgentToolsError, match=message):
        await _live(make_api).add_comment("uploader", number, text, "OPS", reply_to=reply_to)
    assert bitbucket.requests == []


@pytest.mark.parametrize(
    ("review", "status"),
    [("approve", "APPROVED"), ("Needs work", "NEEDS_WORK"), (" Unapprove ", "UNAPPROVED")],
)
async def test_reviews_set_the_users_participant_status(
    bitbucket: FakeBitbucket, make_api: Callable[..., AtlassianClient], review: str, status: str
) -> None:
    chosen, outcome = await _live(make_api).set_review("uploader", 42, review, "OPS")
    (request,) = _writes(bitbucket)
    assert request.url.path.endswith("/pull-requests/42/participants/pat.example_corp")
    assert json.loads(request.content) == {"status": status}
    assert (chosen, outcome.sent) == (status, True)


async def test_bad_review_choices_list_the_options(client: BitbucketClient) -> None:
    with pytest.raises(AgentToolsError, match="approve, needs_work, unapprove"):
        await client.set_review("uploader", 42, "decline", "OPS")


async def test_tools_write_once_and_honour_dry_run(
    bitbucket: FakeBitbucket, make_api: Callable[..., AtlassianClient]
) -> None:
    settings = _settings(default_project="OPS")
    dry = build_server(settings, {"bitbucket": Connected(make_api())})
    pull = {"repository": "uploader", "pull_request_id": 42}

    blocked = _structured(
        await dry.call_tool("bitbucket_add_pull_request_comment", {**pull, "text": "hi"})
    )
    assert (blocked["sent"], blocked["dry_run"]) == (False, True)
    assert blocked["message"].startswith("DRY RUN: nothing was sent to Bitbucket")
    assert blocked["request"] == {
        "method": "POST",
        "path": f"{API}/projects/OPS/repos/uploader/pull-requests/42/comments",
        "body": {"text": "hi"},
    }
    assert blocked["url"].endswith("/pull-requests/42/overview")
    dry_review = _structured(
        await dry.call_tool("bitbucket_review_pull_request", {**pull, "review": "approve"})
    )
    assert dry_review["request"]["path"].endswith("/participants/pat.example_corp")
    assert _writes(bitbucket) == []

    live = build_server(settings, {"bitbucket": Connected(make_api(dry_run=False))})
    arguments = {**pull, "text": "Ship it", "reply_to": 3}
    first = _structured(await live.call_tool("bitbucket_add_pull_request_comment", arguments))
    again = _structured(await live.call_tool("bitbucket_add_pull_request_comment", arguments))
    assert (first["sent"], first["comment_id"], first["action"]) == (True, 76, "add_comment")
    assert first["message"] == "Reply added to OPS/uploader pull request 42."
    assert first["url"].endswith("/pull-requests/42/overview?commentId=76")
    assert (again["repeat_suppressed"], again["comment_id"]) == (True, 76)
    assert "not sent a second time" in again["message"]

    task = _structured(
        await live.call_tool(
            "bitbucket_add_pull_request_comment", {**pull, "text": "Ship it", "task": True}
        )
    )
    assert (task["action"], task["comment_id"]) == ("add_task", 77)
    assert task["message"].startswith("Task added")

    review = {**pull, "review": "needs-work"}
    marked = _structured(await live.call_tool("bitbucket_review_pull_request", review))
    repeat = _structured(
        await live.call_tool("bitbucket_review_pull_request", {**pull, "review": "Needs work"})
    )
    assert (marked["action"], marked["comment_id"]) == ("review_needs_work", None)
    assert marked["message"] == (
        "OPS/uploader pull request 42 is now marked as needing work by the user."
    )
    assert repeat["repeat_suppressed"] is True
    assert len(_writes(bitbucket)) == 3


async def test_bitbucket_refusals_reach_the_agent(
    bitbucket: FakeBitbucket, make_api: Callable[..., AtlassianClient]
) -> None:
    bitbucket.review = httpx.Response(
        409, json={"errors": [{"message": "You cannot review your own pull request."}]}
    )
    server = build_server(_settings(), {"bitbucket": Connected(make_api(dry_run=False))})
    with pytest.raises(ToolError, match=r"HTTP 409\. \(You cannot review your own pull request"):
        await server.call_tool(
            "bitbucket_review_pull_request",
            {
                "project": "OPS",
                "repository": "uploader",
                "pull_request_id": 42,
                "review": "approve",
            },
        )


def test_branch_names_follow_the_branching_model() -> None:
    prefixes = DEFAULT_PREFIXES
    long = "The Installer and Updater code need to account for /var/lib getting full"
    assert (
        branch_name("feature", prefixes, issue_key="ABC-763", summary="Update Reflector's deploy")
        == "feature/ABC-763-update-reflectors-deploy"
    )
    assert (
        branch_name("Bugfix", prefixes, issue_key="ABC-763", summary=long)
        == "bugfix/ABC-763-the-installer-and-updater-code-need-to"
    )
    assert summary_slug("x" * 60) == "x" * 40
    assert (
        branch_name("feature", prefixes, issue_key="ABC-763", summary=" ?! ") == "feature/ABC-763"
    )
    assert branch_name("release", prefixes, name="1.2.0") == "release/1.2.0"
    assert branch_name("hotfix", prefixes, name="hotfix/ABC-9 dns", issue_key="ABC-9") == (
        "hotfix/ABC-9-dns"
    )
    assert branch_name("feature", prefixes, name="retry uploads", issue_key="ABC-9") == (
        "feature/ABC-9-retry-uploads"
    )
    assert branch_name("custom", prefixes, name="spike/try it", issue_key="ABC-1") == "spike/try-it"
    assert branch_name("custom", prefixes, issue_key="ABC-1", summary="Go") == "ABC-1-go"
    assert issue_key_of(" abc-7 ") == "ABC-7"
    with pytest.raises(AgentToolsError, match="not a valid issue key"):
        issue_key_of("seven")
    with pytest.raises(
        AgentToolsError, match="Use one of: feature, bugfix, hotfix, release, custom"
    ):
        branch_name("epic", prefixes, name="x")
    with pytest.raises(AgentToolsError, match="Give an issue_key, a name, or both"):
        branch_name("feature", prefixes)
    for bad in ("a..b", "x.lock", "/x", "a//b", "y" * 101):
        with pytest.raises(AgentToolsError, match="not a valid branch name"):
            check_ref_name(bad)


async def test_create_branch_uses_the_repositorys_model_and_start_points(
    bitbucket: FakeBitbucket, make_api: Callable[..., AtlassianClient]
) -> None:
    live = _live(make_api, default_project="OPS")
    branch, start, note, outcome = await live.create_branch(
        "uploader", "feature", issue_key="ABC-1", summary="Fix the thing"
    )
    assert (branch, start, note) == ("feat/ABC-1-fix-the-thing", "refs/heads/develop", None)
    (sent,) = _writes(bitbucket)
    assert outcome.sent and json.loads(sent.content) == {
        "name": "feat/ABC-1-fix-the-thing",
        "startPoint": "refs/heads/develop",
    }
    assert live.branch_url("OPS", "uploader", branch).endswith(
        "/projects/OPS/repos/uploader/browse?at=refs%2Fheads%2Ffeat%2FABC-1-fix-the-thing"
    )

    _, hotfix_start, _, _ = await live.create_branch("uploader", "hotfix", name="dns")
    assert hotfix_start == "refs/heads/main"
    _, given_start, _, _ = await live.create_branch(
        "uploader", "bugfix", name="x", start_point=" v1.2 "
    )
    assert given_start == "v1.2"
    with pytest.raises(AgentToolsError, match="'release' is not a branch type this repository"):
        await live.create_branch("uploader", "release", name="1.0")
    with pytest.raises(
        AgentToolsError, match="already has a branch named feat/ABC-1-fix-the-thing"
    ):
        await live.create_branch("uploader", "feature", issue_key="ABC-1", summary="Fix the thing")
    assert len(_writes(bitbucket)) == 3


async def test_create_branch_without_a_model_uses_default_prefixes_and_branch(
    bitbucket: FakeBitbucket, make_api: Callable[..., AtlassianClient]
) -> None:
    bitbucket.branch_model = httpx.Response(404, json={"errors": [{"message": "No model"}]})
    live = _live(make_api, default_project="OPS")
    branch, start, note, _ = await live.create_branch("uploader", "release", name="1.4.0")
    assert (branch, start) == ("release/1.4.0", "refs/heads/main")
    assert note is not None and "default prefixes" in note
    assert await live.default_branch("uploader") == "refs/heads/main"
    assert live.repository_ref(None, " uploader ") == ("OPS", "uploader")
    bitbucket.default_branch = httpx.Response(204)
    with pytest.raises(AgentToolsError, match="has no default branch"):
        await live.create_branch("uploader", "feature", name="first")


async def test_create_branch_tool_names_the_branch_after_the_jira_issue(
    fake: FakeJira,
    bitbucket: FakeBitbucket,
    make_core: Callable[..., AtlassianClient],
    make_api: Callable[..., AtlassianClient],
) -> None:
    settings = _settings(default_project="OPS")
    both = {"jira": Connected(make_core()), "bitbucket": Connected(make_api())}
    dry = build_server(settings, both)
    ask = {"repository": "uploader", "issue_key": "abc-1"}
    planned = _structured(await dry.call_tool("bitbucket_create_branch", ask))
    assert (planned["sent"], planned["branch"], planned["url"]) == (
        False,
        "feat/ABC-1-fix-the-thing",
        None,
    )
    assert planned["request"]["body"]["startPoint"] == "refs/heads/develop"
    assert (planned["branch_type"], planned["issue_key"]) == ("feature", "ABC-1")
    assert _writes(bitbucket) == []

    fake.overrides["/jira/rest/api/2/search"] = httpx.Response(200, json={"total": 0, "issues": []})
    with pytest.raises(ToolError, match="Jira has no issue ABC-1 that you can see"):
        await dry.call_tool("bitbucket_create_branch", ask)
    fake.overrides["/jira/rest/api/2/search"] = httpx.Response(500, json={})
    noted = _structured(await dry.call_tool("bitbucket_create_branch", ask))
    assert noted["branch"] == "feat/ABC-1"
    assert "Jira did not give the issue's summary" in noted["note"]


async def test_create_branch_tool_works_without_jira_and_suppresses_repeats(
    bitbucket: FakeBitbucket, make_api: Callable[..., AtlassianClient]
) -> None:
    live = build_server(
        parse_settings(
            {"base_url": "https://host.example", "bitbucket": {"default_project": "OPS"}}
        ),
        {"bitbucket": Connected(make_api(dry_run=False))},
    )
    ask = {"repository": "uploader", "issue_key": "ABC-9", "branch_type": "bugfix"}
    made = _structured(await live.call_tool("bitbucket_create_branch", ask))
    assert (made["sent"], made["branch"]) == (True, "bugfix/ABC-9")
    assert made["message"] == (
        "Created branch bugfix/ABC-9 in OPS/uploader from refs/heads/develop."
    )
    assert made["url"].endswith("browse?at=refs%2Fheads%2Fbugfix%2FABC-9")
    again = _structured(await live.call_tool("bitbucket_create_branch", ask))
    assert again["repeat_suppressed"] is True
    assert len(_writes(bitbucket)) == 1
