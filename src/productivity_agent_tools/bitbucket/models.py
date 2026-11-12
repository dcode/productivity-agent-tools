"""Typed results returned by the Bitbucket tools."""

from pydantic import BaseModel, Field

from productivity_agent_tools.writes import WriteReport


class Reviewer(BaseModel):
    """One reviewer on a pull request."""

    name: str = Field(description="Reviewer display name.")
    status: str = Field(description="APPROVED, NEEDS_WORK or UNAPPROVED (not reviewed yet).")


class PullRequestSummary(BaseModel):
    """Compact view of one pull request, as returned by lists."""

    project: str = Field(description="Project key of the target repository.")
    repository: str = Field(description="Slug of the target repository.")
    id: int = Field(description="Pull request number within the repository.")
    title: str = Field(description="Pull request title.")
    state: str = Field(description="OPEN, MERGED or DECLINED.")
    author: str | None = Field(default=None, description="Author display name.")
    source_branch: str | None = Field(default=None, description="Branch the changes come from.")
    target_branch: str | None = Field(default=None, description="Branch the changes merge into.")
    reviewers: list[Reviewer] = Field(default_factory=list, description="Reviewers and status.")
    comment_count: int = Field(default=0, description="Number of comments.")
    open_task_count: int = Field(default=0, description="Number of unresolved tasks.")
    updated: str | None = Field(default=None, description="Last update timestamp (ISO 8601).")
    url: str | None = Field(default=None, description="Browser URL of the pull request.")


class PullRequestComment(BaseModel):
    """One comment on a pull request."""

    id: int | None = Field(default=None, description="Comment identifier.")
    author: str | None = Field(default=None, description="Author display name.")
    created: str | None = Field(default=None, description="Creation timestamp (ISO 8601).")
    text: str = Field(description="Comment text in Markdown.")
    file: str | None = Field(default=None, description="File the comment is on, if any.")


class TestResults(BaseModel):
    """Test counts a CI server reported with a build."""

    successful: int = Field(default=0, description="Tests that passed.")
    failed: int = Field(default=0, description="Tests that failed.")
    skipped: int = Field(default=0, description="Tests that were skipped.")


class BuildStatus(BaseModel):
    """One build result a CI server reported for a commit."""

    state: str = Field(description="SUCCESSFUL, FAILED, INPROGRESS, CANCELLED or UNKNOWN.")
    name: str | None = Field(default=None, description="Build name, or its key when unnamed.")
    key: str | None = Field(default=None, description="Build key, unique per commit.")
    url: str | None = Field(default=None, description="Link to the build on the CI server.")
    description: str | None = Field(default=None, description="What the CI server said.")
    build_number: str | None = Field(default=None, description="CI build number.")
    ref: str | None = Field(default=None, description="Branch or tag the build ran for.")
    updated: str | None = Field(default=None, description="Last report time (ISO 8601).")
    duration_seconds: float | None = Field(default=None, description="How long the build ran.")
    tests: TestResults | None = Field(default=None, description="Test counts, if reported.")


class BuildCounts(BaseModel):
    """How many of a commit's builds are in each state."""

    successful: int = Field(default=0, description="Builds that passed.")
    failed: int = Field(default=0, description="Builds that failed.")
    in_progress: int = Field(default=0, description="Builds still running.")
    cancelled: int = Field(default=0, description="Builds that were stopped.")
    unknown: int = Field(default=0, description="Builds in no known state.")


class CommitBuilds(BaseModel):
    """Every build result reported for one commit."""

    commit: str = Field(description="The commit hash that was asked about.")
    counts: BuildCounts = Field(description="Totals by state, across all builds of the commit.")
    returned: int = Field(description="Number of builds included in this result.")
    more_available: bool = Field(description="True if the server has more than were returned.")
    builds: list[BuildStatus] = Field(description="Builds, newest first.")


class RequiredBuild(BaseModel):
    """A merge check that needs green builds before a pull request can merge."""

    id: int | None = Field(default=None, description="Merge check identifier.")
    build_keys: list[str] = Field(
        default_factory=list, description="Build keys (parent keys) that must pass."
    )
    branches: str | None = Field(
        default=None, description="Target branches the check applies to, such as main or a pattern."
    )
    exempt_branches: str | None = Field(
        default=None, description="Source branches the check does not apply to."
    )


class RequiredBuildList(BaseModel):
    """Required-build merge checks configured on a repository."""

    project: str = Field(description="Project key of the repository.")
    repository: str = Field(description="Repository slug.")
    returned: int = Field(description="Number of checks included in this result.")
    more_available: bool = Field(description="True if the server has more than were returned.")
    checks: list[RequiredBuild] = Field(description="The configured checks.")


class DeploymentEnvironment(BaseModel):
    """The environment a deployment went to."""

    key: str = Field(description="Environment key.")
    name: str | None = Field(default=None, description="Environment display name.")
    type: str | None = Field(
        default=None, description="DEVELOPMENT, TESTING, STAGING or PRODUCTION."
    )
    url: str | None = Field(default=None, description="Address of the environment.")


class Deployment(BaseModel):
    """One deployment a CI or CD server reported against a commit."""

    key: str = Field(description="Deployment key.")
    name: str | None = Field(default=None, description="Deployment display name.")
    description: str | None = Field(default=None, description="Deployment description.")
    sequence_number: int | None = Field(default=None, description="Run number of this deployment.")
    state: str = Field(
        description="PENDING, IN_PROGRESS, SUCCESSFUL, FAILED, CANCELLED, ROLLED_BACK or UNKNOWN."
    )
    environment: DeploymentEnvironment | None = Field(
        default=None, description="Where it was deployed."
    )
    from_commit: str | None = Field(default=None, description="Commit deployed before this one.")
    to_commit: str | None = Field(default=None, description="Commit this deployment delivered.")
    url: str | None = Field(default=None, description="Link to the deployment on its server.")
    updated: str | None = Field(default=None, description="Last update time (ISO 8601).")


class PullRequestDetail(PullRequestSummary):
    """Full view of one pull request, with description, comments and builds."""

    description: str | None = Field(default=None, description="Description in Markdown.")
    created: str | None = Field(default=None, description="Creation timestamp (ISO 8601).")
    latest_commit: str | None = Field(default=None, description="Head commit of the source.")
    comments: list[PullRequestComment] = Field(
        default_factory=list, description="Most recent comments, oldest first."
    )
    builds: list[BuildStatus] = Field(
        default_factory=list, description="Build results for the latest commit."
    )
    builds_available: bool = Field(
        default=True,
        description="False if build results could not be read; an empty list then means unknown.",
    )


class PullRequestList(BaseModel):
    """Pull requests that involve the user."""

    role: str = Field(description="The user's role that was asked for.")
    state: str = Field(description="The pull request state that was asked for.")
    returned: int = Field(description="Number of pull requests included in this result.")
    more_available: bool = Field(description="True if the server has more than were returned.")
    pull_requests: list[PullRequestSummary] = Field(description="Newest first.")


class RepositoryPullRequests(BaseModel):
    """Pull requests in one repository."""

    project: str = Field(description="Project key of the repository.")
    repository: str = Field(description="Repository slug.")
    state: str = Field(description="The pull request state that was asked for.")
    returned: int = Field(description="Number of pull requests included in this result.")
    more_available: bool = Field(description="True if the server has more than were returned.")
    pull_requests: list[PullRequestSummary] = Field(description="Newest first.")


class Project(BaseModel):
    """One Bitbucket project the user can see."""

    key: str = Field(description="Project key, used wherever a tool takes a project.")
    name: str = Field(description="Project name.")
    description: str | None = Field(default=None, description="Project description.")
    url: str | None = Field(default=None, description="Browser URL of the project.")


class ProjectList(BaseModel):
    """Projects the user can see."""

    returned: int = Field(description="Number of projects included in this result.")
    more_available: bool = Field(description="True if the server has more than were returned.")
    projects: list[Project] = Field(description="Matching projects.")


class Repository(BaseModel):
    """One repository in a project."""

    project: str = Field(description="Project key.")
    slug: str = Field(description="Repository slug, used wherever a tool takes a repository.")
    name: str = Field(description="Repository name.")
    description: str | None = Field(default=None, description="Repository description.")
    url: str | None = Field(default=None, description="Browser URL of the repository.")


class RepositoryList(BaseModel):
    """Repositories in one project."""

    project: str = Field(description="Project key that was listed.")
    returned: int = Field(description="Number of repositories included in this result.")
    more_available: bool = Field(description="True if the server has more than were returned.")
    repositories: list[Repository] = Field(description="Repositories in the project.")


class Commit(BaseModel):
    """One commit in a pull request."""

    id: str = Field(description="Full commit hash.")
    short_id: str | None = Field(default=None, description="Abbreviated commit hash.")
    author: str | None = Field(default=None, description="Author name.")
    authored: str | None = Field(default=None, description="Author timestamp (ISO 8601).")
    message: str = Field(description="Commit message.")


class PullRequestCommits(BaseModel):
    """Commits in a pull request."""

    returned: int = Field(description="Number of commits included in this result.")
    more_available: bool = Field(description="True if the server has more than were returned.")
    commits: list[Commit] = Field(description="Newest first.")


class ChangedFile(BaseModel):
    """One file changed by a pull request."""

    path: str = Field(description="Path of the file after the change.")
    change: str = Field(description="ADD, MODIFY, DELETE, MOVE or COPY.")
    previous_path: str | None = Field(
        default=None, description="Path before the change, for a moved or copied file."
    )


class PullRequestChanges(BaseModel):
    """Files changed by a pull request."""

    returned: int = Field(description="Number of files included in this result.")
    more_available: bool = Field(description="True if the server has more than were returned.")
    files: list[ChangedFile] = Field(description="Changed files.")


class PullRequestDiff(BaseModel):
    """The unified diff of a pull request, possibly cut short."""

    diff: str = Field(description="Unified diff text.")
    total_chars: int = Field(description="Length of the whole diff in characters.")
    truncated: bool = Field(
        description="True if the diff was longer than max_chars and was cut. Use "
        "bitbucket_get_pull_request_changes to see every file."
    )


class CodeLine(BaseModel):
    """One line of a file shown in a search hit."""

    line: int | None = Field(default=None, description="Line number in the file.")
    text: str = Field(description="The line's text.")


class CodeHit(BaseModel):
    """One file that matched a code search."""

    project: str = Field(description="Project key of the repository.")
    repository: str = Field(description="Repository slug.")
    file: str = Field(description="Path of the matching file.")
    hit_count: int | None = Field(default=None, description="Number of matches in the file.")
    lines: list[CodeLine] = Field(
        default_factory=list, description="Matching lines with a little context."
    )
    url: str | None = Field(default=None, description="Browser URL of the file.")


class CodeSearchResult(BaseModel):
    """Result of a code search."""

    query: str = Field(description="The query that was sent, including any added filters.")
    total: int | None = Field(default=None, description="Total matching files on the server.")
    returned: int = Field(description="Number of files included in this result.")
    more_available: bool = Field(description="True if the server has more than were returned.")
    hits: list[CodeHit] = Field(description="Matching files, best match first.")


class DefaultProject(BaseModel):
    """Result of choosing the default Bitbucket project."""

    key: str = Field(description="Project key now used when a tool call names no project.")
    name: str | None = Field(default=None, description="Project name.")
    message: str = Field(description="Plain-language summary, including how long it lasts.")


class BitbucketStatus(BaseModel):
    """Connection status reported by the bitbucket_status tool."""

    base_url: str = Field(description="Bitbucket base URL in use.")
    bitbucket_version: str | None = Field(default=None, description="Server version.")
    token_source: str = Field(description="Where the access token was read from.")
    authenticated: bool = Field(description="True if the token was accepted.")
    user_slug: str | None = Field(
        default=None,
        description="Slug of the signed-in user; None if Bitbucket would not say, in which "
        "case reviews fail until user_slug is set under [bitbucket].",
    )
    dry_run: bool = Field(description="True if comments and reviews are currently blocked.")
    default_project: str | None = Field(
        default=None, description="Project key used when a tool call names no project."
    )
    extra_cookies: list[str] = Field(
        description="Names of the extra cookies sent with each request (values are not shown)."
    )


class MergeVeto(BaseModel):
    """One reason Bitbucket gives for refusing a merge."""

    summary: str = Field(description="Short reason, such as a failed merge check.")
    detail: str | None = Field(default=None, description="Longer explanation, if given.")


class MergeStatus(BaseModel):
    """Whether a pull request can be merged now, and if not, why."""

    can_merge: bool = Field(description="True if nothing blocks the merge.")
    conflicted: bool = Field(description="True if the branches have merge conflicts.")
    outcome: str | None = Field(default=None, description="CLEAN, CONFLICTED or UNKNOWN.")
    vetoes: list[MergeVeto] = Field(
        default_factory=list,
        description="Merge checks blocking the merge (approvals, builds, tasks and so on).",
    )


class Activity(BaseModel):
    """One event in a pull request's history."""

    id: int | None = Field(default=None, description="Activity identifier.")
    action: str = Field(
        description="OPENED, UPDATED, RESCOPED, COMMENTED, APPROVED, UNAPPROVED, REVIEWED "
        "(needs work), MERGED, DECLINED, REOPENED and so on."
    )
    user: str | None = Field(default=None, description="Who did it.")
    created: str | None = Field(default=None, description="When (ISO 8601).")
    comment: str | None = Field(default=None, description="Comment text, for a comment event.")
    comment_id: int | None = Field(default=None, description="Comment identifier, if any.")
    comment_action: str | None = Field(
        default=None, description="ADDED, EDITED, REPLIED or DELETED, for a comment event."
    )
    file: str | None = Field(default=None, description="File a comment is on, if any.")
    line: int | None = Field(default=None, description="Line a comment is on, if any.")
    replies: int = Field(default=0, description="Number of replies to the comment.")
    commits_added: list[str] = Field(
        default_factory=list, description="Short hashes pushed, for a RESCOPED event."
    )
    commits_removed: list[str] = Field(
        default_factory=list, description="Short hashes dropped, for a RESCOPED event."
    )


class PullRequestActivity(BaseModel):
    """A pull request's history, newest first."""

    returned: int = Field(description="Number of events included in this result.")
    more_available: bool = Field(description="True if the server has more than were returned.")
    activities: list[Activity] = Field(description="Events, newest first.")


class PullRequestTask(BaseModel):
    """One task (blocker comment) on a pull request."""

    id: int | None = Field(default=None, description="Task identifier.")
    text: str = Field(description="What has to be done, in Markdown.")
    state: str = Field(description="OPEN or RESOLVED.")
    author: str | None = Field(default=None, description="Who created the task.")
    created: str | None = Field(default=None, description="Creation time (ISO 8601).")
    resolved_by: str | None = Field(default=None, description="Who resolved it, if resolved.")
    resolved: str | None = Field(default=None, description="Resolution time (ISO 8601).")


class PullRequestTasks(BaseModel):
    """Tasks on a pull request."""

    state: str = Field(description="The task state that was asked for.")
    returned: int = Field(description="Number of tasks included in this result.")
    more_available: bool = Field(description="True if the server has more than were returned.")
    tasks: list[PullRequestTask] = Field(description="Tasks, oldest first.")


class LinkedIssue(BaseModel):
    """A Jira issue that a pull request's commits mention."""

    key: str = Field(description="Jira issue key, usable with the jira_ tools.")
    url: str | None = Field(default=None, description="Browser URL of the issue.")
    summary: str | None = Field(
        default=None, description="Issue summary, when Jira is configured too."
    )
    status: str | None = Field(default=None, description="Issue status, when Jira is configured.")
    assignee: str | None = Field(
        default=None, description="Assignee display name, when Jira is configured."
    )


class PullRequestIssues(BaseModel):
    """Jira issues linked to a pull request."""

    returned: int = Field(description="Number of issues found.")
    issues: list[LinkedIssue] = Field(description="Linked issues.")
    details_note: str | None = Field(
        default=None,
        description="Why summaries and statuses are missing, when Jira could not supply them.",
    )


class IssueCommit(BaseModel):
    """A commit whose message mentions a Jira issue key."""

    project: str = Field(description="Project key of the repository.")
    repository: str = Field(description="Repository slug.")
    id: str = Field(description="Full commit hash.")
    short_id: str | None = Field(default=None, description="Abbreviated commit hash.")
    author: str | None = Field(default=None, description="Author name.")
    authored: str | None = Field(default=None, description="Author timestamp (ISO 8601).")
    message: str = Field(default="", description="Commit message.")


class PullRequestWriteResult(WriteReport):
    """Outcome of a Bitbucket write tool."""

    project: str = Field(description="Project key of the repository.")
    repository: str = Field(description="Repository slug.")
    pull_request_id: int = Field(description="Pull request number.")
    comment_id: int | None = Field(default=None, description="Identifier of a new comment.")
    url: str | None = Field(default=None, description="Browser URL of the change, if known.")
