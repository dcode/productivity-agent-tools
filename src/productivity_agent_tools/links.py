"""The Jira and Bitbucket integration: tools offered only when both are configured.

An issue's pull requests come from two places, merged into one list:

* Jira's Development panel (``rest/dev-status``, undocumented), which knows
  pull requests linked by branch name or title as well as by commits.
* Bitbucket's documented Jira integration: the commits whose messages mention
  the key, and the pull requests that contain those commits.

Either source may be missing (an older server, no application link, or the
undocumented resource gone), so each answers or is reported as unavailable.
"""

import asyncio
import re
from collections.abc import Mapping

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from productivity_agent_tools.bitbucket.client import BitbucketClient
from productivity_agent_tools.bitbucket.models import PullRequestSummary
from productivity_agent_tools.errors import AgentToolsError
from productivity_agent_tools.jira.client import JiraClient, normalize_issue_key
from productivity_agent_tools.payload import JsonObject, text
from productivity_agent_tools.products import Integration, client_of
from productivity_agent_tools.tooling import READ, anticipated

DEVELOPMENT_PANEL: str = "Jira development panel"
BITBUCKET_COMMITS: str = "Bitbucket commits"
MAX_COMMITS: int = 50
# Each distinct commit costs one Bitbucket request; this bounds a single tool call.
MAX_COMMIT_LOOKUPS: int = 20
_PULL_URL = re.compile(r"/projects/([^/]+)/repos/([^/]+)/pull-requests/(\d+)")


class IssuePullRequest(BaseModel):
    """A pull request linked to a Jira issue."""

    project: str | None = Field(default=None, description="Bitbucket project key.")
    repository: str | None = Field(default=None, description="Repository slug.")
    id: int | None = Field(default=None, description="Pull request number.")
    title: str | None = Field(default=None, description="Pull request title.")
    state: str | None = Field(default=None, description="OPEN, MERGED or DECLINED.")
    author: str | None = Field(default=None, description="Author name.")
    source_branch: str | None = Field(default=None, description="Branch the changes come from.")
    target_branch: str | None = Field(default=None, description="Branch they merge into.")
    url: str | None = Field(default=None, description="Browser URL of the pull request.")
    found_by: list[str] = Field(
        description="How it was found: the Development panel, or the commits that mention the key."
    )


class IssuePullRequests(BaseModel):
    """The pull requests linked to one Jira issue."""

    issue_key: str = Field(description="The issue.")
    returned: int = Field(description="Number of pull requests found.")
    pull_requests: list[IssuePullRequest] = Field(description="Linked pull requests.")
    sources: list[str] = Field(description="Sources that answered.")
    unavailable: list[str] = Field(
        default_factory=list, description="Sources that could not be read, with the reason."
    )
    commits_checked: int = Field(
        default=0, description="Commits mentioning the key whose pull requests were looked up."
    )
    more_commits: bool = Field(
        default=False,
        description="True if more commits mention the key than were checked; the list may "
        "then be incomplete. Do not try to check the rest one by one.",
    )


def _from_panel(pull: JsonObject) -> IssuePullRequest:
    """Build one pull request from a Development panel entry.

    Args:
        pull: One entry of the panel's ``pullRequests``.

    Returns:
        The pull request, with project, repository and number read from its URL.
    """
    url = text(pull, "url")
    match = _PULL_URL.search(url or "")
    number = (text(pull, "id") or "").lstrip("#")
    return IssuePullRequest(
        project=match.group(1) if match else None,
        repository=match.group(2) if match else text(pull, "repositoryName"),
        id=int(match.group(3)) if match else int(number) if number.isdigit() else None,
        title=text(pull, "name"),
        state=text(pull, "status"),
        author=text(pull, "author", "name"),
        source_branch=text(pull, "source", "branch"),
        target_branch=text(pull, "destination", "branch"),
        url=url,
        found_by=[DEVELOPMENT_PANEL],
    )


def _from_bitbucket(pull: PullRequestSummary, commit: str) -> IssuePullRequest:
    """Build one pull request found through a commit.

    Args:
        pull: The pull request as Bitbucket lists it.
        commit: Short hash of the commit that mentions the key.

    Returns:
        The pull request.
    """
    return IssuePullRequest(
        project=pull.project,
        repository=pull.repository,
        id=pull.id,
        title=pull.title,
        state=pull.state,
        author=pull.author,
        source_branch=pull.source_branch,
        target_branch=pull.target_branch,
        url=pull.url,
        found_by=[f"commit {commit}"],
    )


async def issue_pull_requests(
    jira: JiraClient, bitbucket: BitbucketClient, key: str
) -> IssuePullRequests:
    """Find the pull requests linked to a Jira issue, from both sources.

    Args:
        jira: Jira client.
        bitbucket: Bitbucket client.
        key: Issue key.

    Returns:
        The merged pull requests, newest sources first, and which sources answered.

    Raises:
        AgentToolsError: If the key is invalid.
    """
    issue_key = normalize_issue_key(key)
    sources: list[str] = []
    unavailable: list[str] = []
    found: dict[tuple[str | None, str | None, int | None] | str, IssuePullRequest] = {}

    def add(pull: IssuePullRequest) -> None:
        ident = (
            (pull.project, pull.repository, pull.id)
            if pull.project and pull.repository and pull.id
            else pull.url or repr(pull)
        )
        if ident in found:
            seen = found[ident]
            found[ident] = seen.model_copy(
                update={
                    "found_by": seen.found_by + [w for w in pull.found_by if w not in seen.found_by]
                }
            )
        else:
            found[ident] = pull

    async def panel() -> list[IssuePullRequest]:
        return [_from_panel(p) for p in await jira.development_pull_requests(issue_key)]

    async def commits() -> tuple[list[IssuePullRequest], int, bool, int]:
        listed, more = await bitbucket.issue_commits(issue_key, MAX_COMMITS)
        distinct = list({(c.project, c.repository, c.id): c for c in listed}.values())
        checked = distinct[:MAX_COMMIT_LOOKUPS]
        pages = await asyncio.gather(
            *(bitbucket.commit_pull_requests(c.project, c.repository, c.id) for c in checked),
            return_exceptions=True,
        )
        pulls: list[IssuePullRequest] = []
        failed = 0
        for commit, page in zip(checked, pages, strict=True):
            if isinstance(page, AgentToolsError):
                failed += 1  # such as a repository the user cannot read
                continue
            if isinstance(page, BaseException):
                raise page
            short = commit.short_id or commit.id[:11]
            pulls.extend(_from_bitbucket(pull, short) for pull in page)
        return pulls, len(checked), more or len(distinct) > len(checked), failed

    panel_result, commit_result = await asyncio.gather(panel(), commits(), return_exceptions=True)
    if isinstance(panel_result, AgentToolsError):
        unavailable.append(f"{DEVELOPMENT_PANEL}: {panel_result}")
    elif isinstance(panel_result, BaseException):
        raise panel_result
    else:
        sources.append(DEVELOPMENT_PANEL)
        for pull in panel_result:
            add(pull)
    checked_count, more_commits = 0, False
    if isinstance(commit_result, AgentToolsError):
        unavailable.append(f"{BITBUCKET_COMMITS}: {commit_result}")
    elif isinstance(commit_result, BaseException):
        raise commit_result
    else:
        sources.append(BITBUCKET_COMMITS)
        pulls, checked_count, more_commits, failed = commit_result
        for pull in pulls:
            add(pull)
        if failed:
            unavailable.append(
                f"{BITBUCKET_COMMITS}: the pull requests of {failed} of {checked_count} commits "
                "could not be read"
            )
    pull_requests = list(found.values())
    return IssuePullRequests(
        issue_key=issue_key,
        returned=len(pull_requests),
        pull_requests=pull_requests,
        sources=sources,
        unavailable=unavailable,
        commits_checked=checked_count,
        more_commits=more_commits,
    )


def register_link_tools(server: MCPServer, jira: JiraClient, bitbucket: BitbucketClient) -> None:
    """Register the tools that need both Jira and Bitbucket.

    Args:
        server: The MCP server to add tools to.
        jira: Jira client.
        bitbucket: Bitbucket client.
    """

    @server.tool(annotations=READ)
    @anticipated
    async def jira_get_issue_pull_requests(key: str) -> IssuePullRequests:
        """List the Bitbucket pull requests linked to a Jira issue, with their state.

        It combines what Jira's Development panel shows (pull requests linked by
        branch name, title or commits) with the pull requests that contain
        commits mentioning the key. Use bitbucket_get_pull_request for one
        pull request's details.

        Args:
            key: Issue key, such as ABC-123.
        """
        return await issue_pull_requests(jira, bitbucket, key)


INSTRUCTIONS: str = (
    "To go from a Jira issue to its pull requests, call jira_get_issue_pull_requests; from a "
    "pull request to its issues, bitbucket_get_pull_request_issues."
)


def _register(server: MCPServer, clients: Mapping[str, object]) -> None:
    """Register the link tools from the clients of the products that are on.

    Args:
        server: The MCP server to add tools to.
        clients: The product clients, by product name.
    """
    register_link_tools(
        server,
        client_of(clients, "jira", JiraClient),
        client_of(clients, "bitbucket", BitbucketClient),
    )


INTEGRATION: Integration = Integration(
    name="jira_bitbucket",
    requires=("jira", "bitbucket"),
    instructions=INSTRUCTIONS,
    register_tools=_register,
)
