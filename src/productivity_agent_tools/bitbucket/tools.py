"""MCP tool definitions for Bitbucket. None of them changes anything in Bitbucket."""

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from productivity_agent_tools.bitbucket.client import DEFAULT_DIFF_CHARS, BitbucketClient
from productivity_agent_tools.bitbucket.models import (
    BitbucketStatus,
    CodeSearchResult,
    CommitBuilds,
    DefaultProject,
    Deployment,
    MergeStatus,
    ProjectList,
    PullRequestActivity,
    PullRequestChanges,
    PullRequestCommits,
    PullRequestDetail,
    PullRequestDiff,
    PullRequestIssues,
    PullRequestList,
    PullRequestTasks,
    RepositoryList,
    RepositoryPullRequests,
    RequiredBuildList,
)
from productivity_agent_tools.tooling import READ, anticipated

# Changes only this server's memory of which project to assume, nothing in Bitbucket.
_SESSION_SETTING = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)


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
        """Check the Bitbucket connection, the access token, and the default project.

        Call this first if another Bitbucket tool fails, to see what is wrong.
        """
        version = await bitbucket.server_version()
        await bitbucket.list_pull_requests(max_results=1)
        return BitbucketStatus(
            base_url=bitbucket.base_url,
            bitbucket_version=version,
            token_source=token_source,
            authenticated=True,
            default_project=bitbucket.default_project,
            extra_cookies=cookie_names,
        )

    @server.tool(annotations=READ)
    @anticipated
    async def bitbucket_search_code(
        query: str,
        project: str | None = None,
        repository: str | None = None,
        max_results: int | None = None,
    ) -> CodeSearchResult:
        """Search file contents across every repository the user can see.

        Use it to find where something is defined or used. The query is plain
        text and may include Bitbucket's modifiers, such as ext:py, lang:java
        or path:deploy. The search covers all projects unless project or
        repository is given; the default project is not applied. Only default
        branches are indexed.

        Args:
            query: What to search for.
            project: Only search this project key.
            repository: Only search this repository slug (give project too).
            max_results: Maximum files to return (default from config, at most 100).
        """
        return await bitbucket.search_code(query, project, repository, max_results)

    @server.tool(annotations=READ)
    @anticipated
    async def bitbucket_list_projects(
        name: str | None = None, max_results: int | None = None
    ) -> ProjectList:
        """List the Bitbucket projects the user can see, with their keys.

        Args:
            name: Only projects whose name contains this text.
            max_results: Maximum rows to return (default from config, at most 100).
        """
        return await bitbucket.list_projects(name, max_results)

    @server.tool(annotations=_SESSION_SETTING)
    @anticipated
    async def bitbucket_set_default_project(project: str) -> DefaultProject:
        """Choose the project other Bitbucket tools use when a call names no project.

        Use this when the user says which project they work in. It lasts until
        the server restarts and changes nothing in Bitbucket.

        Args:
            project: Project key, such as PROJ, from bitbucket_list_projects.
        """
        return await bitbucket.set_default_project(project)

    @server.tool(annotations=READ)
    @anticipated
    async def bitbucket_list_repositories(
        project: str | None = None, max_results: int | None = None
    ) -> RepositoryList:
        """List the repositories in a project, with their slugs.

        Args:
            project: Project key; leave out to use the default project.
            max_results: Maximum rows to return (default from config, at most 100).
        """
        return await bitbucket.list_repositories(project, max_results)

    @server.tool(annotations=READ)
    @anticipated
    async def bitbucket_list_pull_requests(
        role: str = "reviewer", state: str = "open", max_results: int | None = None
    ) -> PullRequestList:
        """List pull requests across all repositories that involve the user, newest first.

        Use role "reviewer" for pull requests waiting on the user's review (a
        reviewer whose status is UNAPPROVED has not reviewed yet) and role
        "author" for the user's own pull requests. To see every pull request in
        a repository, use bitbucket_list_repository_pull_requests instead.

        Args:
            role: The user's role: reviewer, author or participant.
            state: open, merged, declined or all.
            max_results: Maximum rows to return (default from config, at most 100).
        """
        return await bitbucket.list_pull_requests(role, state, max_results)

    @server.tool(annotations=READ)
    @anticipated
    async def bitbucket_list_repository_pull_requests(
        repository: str,
        project: str | None = None,
        state: str = "open",
        filter_text: str | None = None,
        max_results: int | None = None,
    ) -> RepositoryPullRequests:
        """List the pull requests in one repository, whoever is involved, newest first.

        Args:
            repository: Repository slug, such as my-service.
            project: Project key; leave out to use the default project.
            state: open, merged, declined or all.
            filter_text: Only pull requests whose title or description contains this text.
            max_results: Maximum rows to return (default from config, at most 100).
        """
        return await bitbucket.list_repository_pull_requests(
            repository, project, state, filter_text, max_results
        )

    @server.tool(annotations=READ)
    @anticipated
    async def bitbucket_get_pull_request(
        repository: str,
        pull_request_id: int,
        project: str | None = None,
        max_comments: int = 10,
    ) -> PullRequestDetail:
        """Get one pull request: description, reviewers, newest comments and build results.

        Text is in Markdown. For the code itself use
        bitbucket_get_pull_request_changes and bitbucket_get_pull_request_diff;
        for what blocks a merge use bitbucket_get_pull_request_merge_status.

        Args:
            repository: Repository slug, such as my-service.
            pull_request_id: Pull request number within the repository.
            project: Project key; leave out to use the default project.
            max_comments: How many of the newest comments to include (0 for none).
        """
        return await bitbucket.get_pull_request(repository, pull_request_id, project, max_comments)

    @server.tool(annotations=READ)
    @anticipated
    async def bitbucket_get_pull_request_commits(
        repository: str,
        pull_request_id: int,
        project: str | None = None,
        max_results: int | None = None,
    ) -> PullRequestCommits:
        """List the commits in a pull request, newest first.

        Args:
            repository: Repository slug, such as my-service.
            pull_request_id: Pull request number within the repository.
            project: Project key; leave out to use the default project.
            max_results: Maximum rows to return (default from config, at most 100).
        """
        return await bitbucket.get_pull_request_commits(
            repository, pull_request_id, project, max_results
        )

    @server.tool(annotations=READ)
    @anticipated
    async def bitbucket_get_pull_request_changes(
        repository: str,
        pull_request_id: int,
        project: str | None = None,
        max_results: int | None = None,
    ) -> PullRequestChanges:
        """List the files a pull request adds, modifies, deletes or moves.

        Args:
            repository: Repository slug, such as my-service.
            pull_request_id: Pull request number within the repository.
            project: Project key; leave out to use the default project.
            max_results: Maximum rows to return (default from config, at most 100).
        """
        return await bitbucket.get_pull_request_changes(
            repository, pull_request_id, project, max_results
        )

    @server.tool(annotations=READ)
    @anticipated
    async def bitbucket_get_pull_request_diff(
        repository: str,
        pull_request_id: int,
        project: str | None = None,
        context_lines: int = 3,
        max_chars: int = DEFAULT_DIFF_CHARS,
    ) -> PullRequestDiff:
        """Get the unified diff of a pull request.

        Large diffs are cut at max_chars; the result says so and gives the full
        length. Check bitbucket_get_pull_request_changes first to see how big
        the change is.

        Args:
            repository: Repository slug, such as my-service.
            pull_request_id: Pull request number within the repository.
            project: Project key; leave out to use the default project.
            context_lines: Unchanged lines to show around each change.
            max_chars: Longest diff to return (at most 100,000 characters).
        """
        return await bitbucket.get_pull_request_diff(
            repository, pull_request_id, project, context_lines, max_chars
        )

    @server.tool(annotations=READ)
    @anticipated
    async def bitbucket_get_pull_request_merge_status(
        repository: str, pull_request_id: int, project: str | None = None
    ) -> MergeStatus:
        """Check whether a pull request can be merged now and, if not, what blocks it.

        Lists each blocking merge check, such as missing approvals, failed or
        missing builds, open tasks or merge conflicts. Use it to tell the user
        what is left before a pull request can go in.

        Args:
            repository: Repository slug, such as my-service.
            pull_request_id: Pull request number within the repository.
            project: Project key; leave out to use the default project.
        """
        return await bitbucket.get_merge_status(repository, pull_request_id, project)

    @server.tool(annotations=READ)
    @anticipated
    async def bitbucket_get_pull_request_activity(
        repository: str,
        pull_request_id: int,
        project: str | None = None,
        max_results: int | None = None,
    ) -> PullRequestActivity:
        """Get a pull request's history, newest first: pushes, comments, reviews and merges.

        Use it to see what changed since the user last looked, such as new
        commits (RESCOPED), approvals, needs-work reviews (REVIEWED) or new
        comments and replies.

        Args:
            repository: Repository slug, such as my-service.
            pull_request_id: Pull request number within the repository.
            project: Project key; leave out to use the default project.
            max_results: Maximum events to return (default from config, at most 100).
        """
        return await bitbucket.get_pull_request_activity(
            repository, pull_request_id, project, max_results
        )

    @server.tool(annotations=READ)
    @anticipated
    async def bitbucket_list_pull_request_tasks(
        repository: str,
        pull_request_id: int,
        project: str | None = None,
        state: str = "open",
        max_results: int | None = None,
    ) -> PullRequestTasks:
        """List the tasks on a pull request, which must usually be resolved before merging.

        Args:
            repository: Repository slug, such as my-service.
            pull_request_id: Pull request number within the repository.
            project: Project key; leave out to use the default project.
            state: open, resolved or all.
            max_results: Maximum rows to return (default from config, at most 100).
        """
        return await bitbucket.list_pull_request_tasks(
            repository, pull_request_id, project, state, max_results
        )

    @server.tool(annotations=READ)
    @anticipated
    async def bitbucket_get_pull_request_issues(
        repository: str, pull_request_id: int, project: str | None = None
    ) -> PullRequestIssues:
        """List the Jira issue keys a pull request is linked to through its commits and title.

        The keys work with the jira_ tools, such as jira_get_issue.

        Args:
            repository: Repository slug, such as my-service.
            pull_request_id: Pull request number within the repository.
            project: Project key; leave out to use the default project.
        """
        return await bitbucket.get_pull_request_issues(repository, pull_request_id, project)

    @server.tool(annotations=READ)
    @anticipated
    async def bitbucket_get_commit_builds(
        commit_id: str, max_results: int | None = None
    ) -> CommitBuilds:
        """Get every CI build result reported for a commit, with totals by state.

        For a pull request, bitbucket_get_pull_request already shows the builds
        of its latest commit; use this for any other commit, or for test counts
        and descriptions when there are many builds. Builds are stored per
        commit, so no project or repository is needed.

        Args:
            commit_id: Commit hash, full or abbreviated.
            max_results: Maximum builds to return (default from config, at most 100).
        """
        return await bitbucket.get_commit_builds(commit_id, max_results)

    @server.tool(annotations=READ)
    @anticipated
    async def bitbucket_list_required_builds(
        repository: str, project: str | None = None, max_results: int | None = None
    ) -> RequiredBuildList:
        """List which builds must pass before a pull request into a repository can merge.

        Args:
            repository: Repository slug, such as my-service.
            project: Project key; leave out to use the default project.
            max_results: Maximum rows to return (default from config, at most 100).
        """
        return await bitbucket.list_required_builds(repository, project, max_results)

    @server.tool(annotations=READ)
    @anticipated
    async def bitbucket_get_deployment(
        repository: str,
        commit_id: str,
        deployment_key: str,
        environment_key: str,
        sequence_number: int,
        project: str | None = None,
    ) -> Deployment:
        """Get the state of one deployment that a CI or CD server reported for a commit.

        Bitbucket cannot list deployments: all three identifiers (deployment
        key, environment key and sequence number) must come from the user or
        from the CI or CD server. Do not guess them.

        Args:
            repository: Repository slug, such as my-service.
            commit_id: Commit hash the deployment delivered.
            deployment_key: Deployment key, as the CI or CD server reported it.
            environment_key: Environment key, such as production.
            sequence_number: Run number of the deployment.
            project: Project key; leave out to use the default project.
        """
        return await bitbucket.get_deployment(
            repository, commit_id, deployment_key, environment_key, sequence_number, project
        )
