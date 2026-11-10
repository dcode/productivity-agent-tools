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


class BitbucketStatus(BaseModel):
    """Connection status reported by the bitbucket_status tool."""

    base_url: str = Field(description="Bitbucket base URL in use.")
    bitbucket_version: str | None = Field(default=None, description="Server version.")
    token_source: str = Field(description="Where the access token was read from.")
    authenticated: bool = Field(description="True if the token was accepted.")
    extra_cookies: list[str] = Field(
        description="Names of the extra cookies sent with each request (values are not shown)."
    )
