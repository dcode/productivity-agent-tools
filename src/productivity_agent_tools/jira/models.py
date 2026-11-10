"""Typed results returned by the Jira tools."""

from pydantic import BaseModel, Field, JsonValue


class IssueSummary(BaseModel):
    """Compact view of one issue, as returned by searches."""

    key: str = Field(description="Issue key, such as PROJ-123.")
    summary: str = Field(description="One-line issue title.")
    status: str = Field(description="Current workflow status name.")
    issue_type: str | None = Field(default=None, description="Issue type name.")
    priority: str | None = Field(default=None, description="Priority name, if set.")
    assignee: str | None = Field(default=None, description="Assignee display name, if any.")
    updated: str | None = Field(default=None, description="Last update timestamp (ISO 8601).")
    url: str = Field(description="Browser URL of the issue.")


class Comment(BaseModel):
    """One comment on an issue."""

    id: str = Field(description="Comment identifier.")
    author: str | None = Field(default=None, description="Author display name.")
    created: str | None = Field(default=None, description="Creation timestamp (ISO 8601).")
    body: str = Field(description="Comment text in Jira wiki markup.")


class IssueDetail(IssueSummary):
    """Full view of one issue, including description and recent comments."""

    description: str | None = Field(default=None, description="Description in Jira wiki markup.")
    reporter: str | None = Field(default=None, description="Reporter display name.")
    created: str | None = Field(default=None, description="Creation timestamp (ISO 8601).")
    labels: list[str] = Field(default_factory=list, description="Labels on the issue.")
    comments: list[Comment] = Field(
        default_factory=list, description="Most recent comments, oldest first."
    )
    comment_total: int = Field(default=0, description="Total number of comments on the issue.")


class SearchResult(BaseModel):
    """Result of a JQL search."""

    jql: str = Field(description="The JQL that was run.")
    total: int = Field(description="Total number of matching issues on the server.")
    returned: int = Field(description="Number of issues included in this result.")
    issues: list[IssueSummary] = Field(description="Matching issues, in query order.")


class Preset(BaseModel):
    """A named JQL query from the local configuration."""

    name: str = Field(description="Preset name to pass to jira_run_preset.")
    jql: str = Field(description="The JQL the preset runs.")


class Transition(BaseModel):
    """A workflow transition currently available on an issue."""

    id: str = Field(description="Transition identifier.")
    name: str = Field(description="Transition name, such as 'Start Progress'.")
    to_status: str | None = Field(default=None, description="Status the issue would move to.")


class WriteRequest(BaseModel):
    """The exact HTTP request a write tool sends, or would send."""

    method: str = Field(description="HTTP method.")
    path: str = Field(description="Request path on the Jira host.")
    body: dict[str, JsonValue] = Field(description="JSON request body.")


class WriteResult(BaseModel):
    """Outcome of a write tool."""

    action: str = Field(description="What the tool was asked to do.")
    issue_key: str = Field(description="Issue the action applies to.")
    sent: bool = Field(description="True if the change was sent to Jira.")
    dry_run: bool = Field(description="True if dry-run blocked the change.")
    request: WriteRequest = Field(description="The request that was, or would be, sent.")
    message: str = Field(description="Plain-language summary of the outcome.")


class ServerStatus(BaseModel):
    """Connection and safety status of the Jira integration."""

    base_url: str = Field(description="Jira base URL in use.")
    jira_version: str | None = Field(default=None, description="Jira server version.")
    username: str | None = Field(default=None, description="Authenticated username.")
    display_name: str | None = Field(default=None, description="Authenticated display name.")
    token_source: str = Field(description="Where the access token was read from.")
    dry_run: bool = Field(description="True if writes are currently blocked.")
    extra_cookies: list[str] = Field(
        description="Names of the extra cookies sent with each request (values are not shown)."
    )
