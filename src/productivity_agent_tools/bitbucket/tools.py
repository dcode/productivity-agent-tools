"""MCP tool definitions for Bitbucket.

Only bitbucket_create_branch, bitbucket_add_pull_request_comment and
bitbucket_review_pull_request change anything in Bitbucket, and dry-run blocks
all three.
"""

from mcp.server.mcpserver import MCPServer

from productivity_agent_tools.bitbucket.branches import BranchType, issue_key_of
from productivity_agent_tools.bitbucket.client import (
    DEFAULT_DIFF_CHARS,
    BitbucketClient,
    normalize_review,
)
from productivity_agent_tools.bitbucket.models import (
    BitbucketStatus,
    BranchWriteResult,
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
    PullRequestWriteResult,
    RepositoryList,
    RepositoryPullRequests,
    RequiredBuildList,
)
from productivity_agent_tools.capabilities import IssueTracker
from productivity_agent_tools.errors import AgentToolsError
from productivity_agent_tools.products import ToolContext
from productivity_agent_tools.repeats import RepeatGuard
from productivity_agent_tools.tooling import (
    IDEMPOTENT_WRITE,
    READ,
    SESSION_SETTING,
    WRITE,
    anticipated,
)
from productivity_agent_tools.toolsets import toolset
from productivity_agent_tools.writes import once, report

_REVIEW_WORDS: dict[str, str] = {
    "APPROVED": "approved",
    "NEEDS_WORK": "marked as needing work",
    "UNAPPROVED": "no longer approved or marked as needing work",
}


def register_bitbucket_tools(
    server: MCPServer,
    bitbucket: BitbucketClient,
    context: ToolContext,
    repeats: RepeatGuard[PullRequestWriteResult] | None = None,
) -> None:
    """Register every Bitbucket tool on an MCP server.

    Args:
        server: The MCP server to add tools to.
        bitbucket: Bitbucket client the tools call.
        context: Token source, extra cookie names, and the other products that are on;
            an ``IssueTracker`` among them adds issue details to linked issues.
        repeats: Guard against identical repeated writes; a default one is made when None.
    """
    guard = repeats if repeats is not None else RepeatGuard[PullRequestWriteResult]()
    branch_guard = RepeatGuard[BranchWriteResult]()
    tracker = context.peer(IssueTracker)

    @server.tool(annotations=READ)
    @anticipated
    async def bitbucket_status() -> BitbucketStatus:
        """Check the Bitbucket connection: the account, where its token comes from, and dry-run.

        Call this first if another bitbucket_ tool fails, to see what is wrong. If
        the token is rejected or missing, only the user can fix that: ask them to
        run 'productivity-agent-tools set-token bitbucket'.
        """
        version = await bitbucket.server_version()
        await bitbucket.list_pull_requests(max_results=1)
        return BitbucketStatus(
            base_url=bitbucket.base_url,
            bitbucket_version=version,
            token_source=context.token_source,
            authenticated=True,
            user_slug=await bitbucket.user_slug(required=False),
            dry_run=bitbucket.dry_run,
            default_project=bitbucket.default_project,
            extra_cookies=list(context.cookie_names),
        )

    @server.tool(annotations=READ, meta=toolset("code"))
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

    @server.tool(annotations=SESSION_SETTING)
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

    @server.tool(annotations=READ, meta=toolset("review"))
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

    @server.tool(annotations=READ, meta=toolset("review"))
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

    @server.tool(annotations=READ, meta=toolset("review"))
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

    @server.tool(annotations=READ, meta=toolset("review"))
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

    @server.tool(annotations=READ, meta=toolset("review"))
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

    @server.tool(annotations=READ, meta=toolset("review"))
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

        When Jira is configured too, each issue's summary, status and assignee are
        included. The keys work with the jira_ tools, such as jira_get_issue.

        Args:
            repository: Repository slug, such as my-service.
            pull_request_id: Pull request number within the repository.
            project: Project key; leave out to use the default project.
        """
        found = await bitbucket.get_pull_request_issues(repository, pull_request_id, project)
        if tracker is None or not found.issues:
            return found
        try:
            details = await tracker.describe_issues([issue.key for issue in found.issues])
        except AgentToolsError as exc:
            return found.model_copy(update={"details_note": f"Jira did not describe them: {exc}"})
        return found.model_copy(
            update={
                "issues": [
                    issue.model_copy(
                        update={
                            "summary": details[issue.key].summary,
                            "status": details[issue.key].status,
                            "assignee": details[issue.key].assignee,
                        }
                    )
                    if issue.key in details
                    else issue
                    for issue in found.issues
                ]
            }
        )

    if bitbucket.settings.ci:

        @server.tool(annotations=READ, meta=toolset("builds"))
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

        @server.tool(annotations=READ, meta=toolset("builds"))
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

        @server.tool(annotations=READ, meta=toolset("builds"))
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

    @server.tool(annotations=WRITE, meta=toolset("code"))
    @anticipated
    async def bitbucket_create_branch(
        repository: str,
        branch_type: BranchType = "feature",
        issue_key: str | None = None,
        name: str | None = None,
        project: str | None = None,
        start_point: str | None = None,
    ) -> BranchWriteResult:
        """Create a branch, named the way Bitbucket's branching model names it.

        Visible to everyone with access to the repository; confirm the name
        with the user first. Give an issue_key to start work on a Jira issue:
        the branch is then named <prefix><KEY>-<summary>, such as
        feature/ABC-123-retry-failed-uploads, and shows up on the issue in
        Jira. The summary is looked up when Jira is configured; without it the
        name is <prefix><KEY>. Give name to choose the words yourself.

        branch_type picks the prefix: feature, bugfix, hotfix or release use
        the repository's own prefixes (usually feature/, bugfix/, hotfix/,
        release/); custom has no prefix and uses name exactly as given. A
        release is usually named for its version, such as name="1.4.0".

        In dry-run mode nothing is created and the result shows the name and
        the request that would have been made; the name is still checked, and
        a branch that already exists is refused. An identical request within
        30 seconds is not sent again.

        Args:
            repository: Repository slug, such as my-service.
            branch_type: feature (default), bugfix, hotfix, release or custom.
            issue_key: Jira issue key to name the branch after, such as ABC-123.
            name: Words after the prefix (spaces become dashes); for custom, the whole
                branch name. Leave out to use the issue's summary.
            project: Project key; leave out to use the default project.
            start_point: Branch, tag or commit to start from. Leave out for the
                repository's development branch (its production branch for a hotfix).
        """
        key, slug = bitbucket.repository_ref(project, repository)
        issue = issue_key_of(issue_key) if issue_key and issue_key.strip() else None
        summary: str | None = None
        lookup_note: str | None = None
        if issue and tracker is not None and not (name and name.strip()):
            try:
                found = await tracker.describe_issues([issue])
            except AgentToolsError as exc:
                lookup_note = f"Jira did not give the issue's summary, so it was left out: {exc}"
            else:
                if issue not in found:
                    raise AgentToolsError(
                        f"Jira has no issue {issue} that you can see, so no branch was created. "
                        "Check the key, or give name to create the branch anyway."
                    )
                summary = found[issue].summary

        async def send() -> BranchWriteResult:
            branch, start, note, outcome = await bitbucket.create_branch(
                slug,
                branch_type,
                project=key,
                issue_key=issue,
                name=name,
                summary=summary,
                start_point=start_point,
            )
            notes = " ".join(n for n in (note, lookup_note) if n)
            return BranchWriteResult.model_validate(
                {
                    **report(
                        "create_branch",
                        "Bitbucket",
                        outcome,
                        f"Created branch {branch} in {key}/{slug} from {start}.",
                    ),
                    "project": key,
                    "repository": slug,
                    "branch": branch,
                    "branch_type": branch_type.strip().casefold(),
                    "start_point": start,
                    "issue_key": issue,
                    "note": notes or None,
                    "url": bitbucket.branch_url(key, slug, branch) if outcome.sent else None,
                }
            )

        signature = ("create_branch", key, slug, branch_type, issue, name, summary, start_point)
        return await once(branch_guard, signature, send)

    @server.tool(annotations=WRITE, meta=toolset("review"))
    @anticipated
    async def bitbucket_add_pull_request_comment(
        repository: str,
        pull_request_id: int,
        text: str,
        project: str | None = None,
        reply_to: int | None = None,
        task: bool = False,
    ) -> PullRequestWriteResult:
        """Comment on a pull request, reply to a comment, or add a task. Visible to the team.

        Write the text in Markdown. The comment is attributed to the user. To
        reply, pass the id of the comment from bitbucket_get_pull_request or
        bitbucket_get_pull_request_activity. With task=true the comment becomes
        a task that must be resolved before merging.

        In dry-run mode nothing is sent and the result shows the request that
        would have been made. Call this once per comment: an identical comment
        on the same pull request within 30 seconds is not sent again.

        Args:
            repository: Repository slug, such as my-service.
            pull_request_id: Pull request number within the repository.
            text: Comment text in Markdown.
            project: Project key; leave out to use the default project.
            reply_to: Id of the comment to reply to; leave out for a new comment.
            task: True to add a task (blocker comment) instead of a plain comment.
        """
        key, slug, number = bitbucket.pull_request_ref(project, repository, pull_request_id)

        async def send() -> PullRequestWriteResult:
            outcome = await bitbucket.add_comment(slug, number, text, key, reply_to, task)
            kind = "Task" if task else "Reply" if reply_to is not None else "Comment"
            new_id = outcome.response.get("id") if isinstance(outcome.response, dict) else None
            comment_id = new_id if isinstance(new_id, int) else None
            page = bitbucket.pull_request_url(key, slug, number)
            return PullRequestWriteResult.model_validate(
                {
                    **report(
                        "add_task" if task else "add_comment",
                        "Bitbucket",
                        outcome,
                        f"{kind} added to {key}/{slug} pull request {number}.",
                    ),
                    "project": key,
                    "repository": slug,
                    "pull_request_id": number,
                    "comment_id": comment_id,
                    "url": f"{page}?commentId={comment_id}" if comment_id is not None else page,
                }
            )

        return await once(guard, ("add_comment", key, slug, number, reply_to, task, text), send)

    @server.tool(annotations=IDEMPOTENT_WRITE, meta=toolset("review"))
    @anticipated
    async def bitbucket_review_pull_request(
        repository: str,
        pull_request_id: int,
        review: str,
        project: str | None = None,
    ) -> PullRequestWriteResult:
        """Approve a pull request, mark it as needing work, or withdraw either. Visible to the team.

        The review is recorded as the user's. Bitbucket refuses a review of the
        user's own pull request. To explain a needs-work review, add a comment
        with bitbucket_add_pull_request_comment as well.

        In dry-run mode nothing is sent and the result shows the request that
        would have been made. An identical review of the same pull request
        within 30 seconds is not sent again.

        Args:
            repository: Repository slug, such as my-service.
            pull_request_id: Pull request number within the repository.
            review: approve, needs_work, or unapprove (clears an approval or needs-work).
            project: Project key; leave out to use the default project.
        """
        key, slug, number = bitbucket.pull_request_ref(project, repository, pull_request_id)

        async def send() -> PullRequestWriteResult:
            status, outcome = await bitbucket.set_review(slug, number, review, key)
            return PullRequestWriteResult.model_validate(
                {
                    **report(
                        f"review_{status.lower()}",
                        "Bitbucket",
                        outcome,
                        f"{key}/{slug} pull request {number} is now {_REVIEW_WORDS[status]} "
                        "by the user.",
                    ),
                    "project": key,
                    "repository": slug,
                    "pull_request_id": number,
                    "url": bitbucket.pull_request_url(key, slug, number),
                }
            )

        return await once(guard, ("review", key, slug, number, normalize_review(review)), send)
