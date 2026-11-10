"""MCP tool definitions for Bitbucket. All of them only read."""

from mcp.server.mcpserver import MCPServer

from productivity_agent_tools.bitbucket.client import BitbucketClient
from productivity_agent_tools.bitbucket.models import (
    BitbucketStatus,
    PullRequestDetail,
    PullRequestList,
)
from productivity_agent_tools.tooling import READ, anticipated


def register_bitbucket_tools(
    server: MCPServer,
    bitbucket: BitbucketClient,
    token_source: str,
    cookie_names: list[str],
) -> None:
    """Register every Bitbucket tool on an MCP server.

    Args:
        server: The MCP server to add tools to.
        bitbucket: Bitbucket client the tools call.
        token_source: Description of where the access token came from.
        cookie_names: Names of the extra cookies sent to Bitbucket.
    """

    @server.tool(annotations=READ)
    @anticipated
    async def bitbucket_status() -> BitbucketStatus:
        """Check the Bitbucket connection and whether the access token is accepted.

        Call this first if another Bitbucket tool fails, to see what is wrong.
        """
        version = await bitbucket.server_version()
        await bitbucket.list_pull_requests(max_results=1)
        return BitbucketStatus(
            base_url=bitbucket.base_url,
            bitbucket_version=version,
            token_source=token_source,
            authenticated=True,
            extra_cookies=cookie_names,
        )

    @server.tool(annotations=READ)
    @anticipated
    async def bitbucket_list_pull_requests(
        role: str = "reviewer", state: str = "open", max_results: int | None = None
    ) -> PullRequestList:
        """List pull requests across all repositories that involve the user, newest first.

        Use role "reviewer" for pull requests waiting on the user's review (a
        reviewer whose status is UNAPPROVED has not reviewed yet) and role
        "author" for the user's own pull requests.

        Args:
            role: The user's role: reviewer, author or participant.
            state: open, merged, declined or all.
            max_results: Maximum rows to return (default from config, at most 100).
        """
        return await bitbucket.list_pull_requests(role, state, max_results)

    @server.tool(annotations=READ)
    @anticipated
    async def bitbucket_get_pull_request(
        project: str, repository: str, pull_request_id: int, max_comments: int = 10
    ) -> PullRequestDetail:
        """Get one pull request: description, reviewers, newest comments and build results.

        Take project, repository and id from bitbucket_list_pull_requests. Text
        is in Markdown. The diff itself is not included.

        Args:
            project: Project key of the repository, such as PROJ.
            repository: Repository slug, such as my-service.
            pull_request_id: Pull request number within the repository.
            max_comments: How many of the newest comments to include (0 for none).
        """
        return await bitbucket.get_pull_request(project, repository, pull_request_id, max_comments)
