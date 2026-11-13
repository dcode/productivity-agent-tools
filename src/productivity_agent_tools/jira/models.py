"""Typed results returned by the Jira tools."""

from pydantic import BaseModel, Field

from productivity_agent_tools.writes import WriteReport


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


class IssueLink(BaseModel):
    """A link from this issue to another."""

    relation: str = Field(
        description="How this issue relates to the other, such as 'blocks' or 'is blocked by'."
    )
    key: str = Field(description="Key of the other issue.")
    summary: str | None = Field(default=None, description="The other issue's summary.")
    status: str | None = Field(default=None, description="The other issue's status.")
    link_id: str | None = Field(default=None, description="Identifier of the link itself.")


class IssueDetail(IssueSummary):
    """Full view of one issue, including description and recent comments."""

    project: str | None = Field(default=None, description="Project key.")
    description: str | None = Field(default=None, description="Description in Jira wiki markup.")
    reporter: str | None = Field(default=None, description="Reporter display name.")
    created: str | None = Field(default=None, description="Creation timestamp (ISO 8601).")
    due_date: str | None = Field(default=None, description="Due date (YYYY-MM-DD), if set.")
    resolution: str | None = Field(
        default=None, description="Resolution, such as Fixed; None while unresolved."
    )
    labels: list[str] = Field(default_factory=list, description="Labels on the issue.")
    components: list[str] = Field(default_factory=list, description="Component names.")
    fix_versions: list[str] = Field(default_factory=list, description="Fix version names.")
    affects_versions: list[str] = Field(
        default_factory=list, description="Affects version names (the 'versions' field)."
    )
    original_estimate: str | None = Field(
        default=None, description="Original estimate, such as 2d."
    )
    remaining_estimate: str | None = Field(default=None, description="Remaining estimate.")
    time_spent: str | None = Field(default=None, description="Time logged so far.")
    original_estimate_seconds: int | None = Field(
        default=None, description="Original estimate in seconds (timeoriginalestimate)."
    )
    time_spent_seconds: int | None = Field(
        default=None, description="Time logged in seconds (timespent)."
    )
    links: list[IssueLink] = Field(default_factory=list, description="Links to other issues.")
    epic: str | None = Field(
        default=None, description="Key of the epic this issue belongs to (Epic Link), if any."
    )
    epic_name: str | None = Field(
        default=None, description="For an epic, its short name (Epic Name)."
    )
    story_points: float | None = Field(default=None, description="Estimate in story points.")
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
    resolutions: list[str] = Field(
        default_factory=list,
        description="Resolutions this transition can set; empty if its screen has none.",
    )
    resolution_required: bool = Field(
        default=False, description="True if a resolution must be given with this transition."
    )


class FieldInfo(BaseModel):
    """One field an issue can be edited or created with."""

    id: str = Field(description="Field id, such as priority or customfield_10010.")
    name: str = Field(description="Field name as Jira shows it.")
    required: bool = Field(description="True if a value must be given.")
    type: str | None = Field(default=None, description="Value type, such as string or array.")
    allowed_values: list[str] = Field(
        default_factory=list, description="Accepted values by name, when the field lists them."
    )
    more_values: int = Field(default=0, description="Allowed values not listed here.")
    operations: list[str] = Field(
        default_factory=list, description="How it can be changed: set, add, remove, edit."
    )


class EditFields(BaseModel):
    """The fields one issue can be edited with."""

    issue_key: str = Field(description="The issue.")
    fields: list[FieldInfo] = Field(description="Fields on the issue's edit screen.")


class IssueTypeInfo(BaseModel):
    """An issue type a project can create."""

    id: str = Field(description="Issue type id.")
    name: str = Field(description="Issue type name, such as Bug or Story.")
    subtask: bool = Field(description="True for a sub-task type, which needs a parent issue.")


class CreateFields(BaseModel):
    """What creating an issue in a project needs."""

    project: str = Field(description="Project key.")
    issue_types: list[IssueTypeInfo] = Field(description="Issue types the user can create here.")
    issue_type: str | None = Field(
        default=None, description="The issue type the fields are for, if one was asked for."
    )
    fields: list[FieldInfo] = Field(
        default_factory=list, description="Fields on that issue type's create screen."
    )


class LinkType(BaseModel):
    """A kind of link between issues."""

    name: str = Field(description="Link type name, such as Blocks.")
    outward: str = Field(description="Wording from the first issue, such as 'blocks'.")
    inward: str = Field(description="Wording from the second issue, such as 'is blocked by'.")


class WriteResult(WriteReport):
    """Outcome of a Jira write tool."""

    issue_key: str = Field(description="Issue the action applies to.")
    created_id: str | None = Field(
        default=None, description="Identifier of the comment or worklog created, if any."
    )


class Worklog(BaseModel):
    """Time someone logged on an issue."""

    id: str = Field(description="Worklog identifier.")
    author: str | None = Field(default=None, description="Who logged the time.")
    started: str | None = Field(default=None, description="When the work started (ISO 8601).")
    time_spent: str | None = Field(
        default=None, description="Duration as Jira shows it, e.g. 1h 30m."
    )
    seconds: int = Field(default=0, description="Duration in seconds.")
    comment: str | None = Field(default=None, description="Work description in wiki markup.")


class WorklogList(BaseModel):
    """Time logged on an issue."""

    issue_key: str = Field(description="Issue the worklogs belong to.")
    total: int = Field(description="Number of worklogs on the issue.")
    total_seconds: int = Field(description="Time logged across all worklogs, in seconds.")
    returned: int = Field(description="Number of worklogs included in this result.")
    worklogs: list[Worklog] = Field(description="Most recent worklogs, oldest first.")


class Assignee(BaseModel):
    """The person an issue was, or would be, assigned to."""

    username: str | None = Field(default=None, description="Jira username; None to unassign.")
    display_name: str | None = Field(default=None, description="Display name.")


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
