"""Typed results returned by the Bitbucket tools."""

from pydantic import BaseModel, Field


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


class BuildStatus(BaseModel):
    """One build result reported for the pull request's latest commit."""

    state: str = Field(description="SUCCESSFUL, FAILED or INPROGRESS.")
    name: str | None = Field(default=None, description="Build name or key.")
    url: str | None = Field(default=None, description="Link to the build.")


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
    default_project: str | None = Field(
        default=None, description="Project key used when a tool call names no project."
    )
    extra_cookies: list[str] = Field(
        description="Names of the extra cookies sent with each request (values are not shown)."
    )
