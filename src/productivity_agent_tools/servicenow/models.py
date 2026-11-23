"""Shapes returned by the ServiceNow tools."""

from pydantic import BaseModel, Field

from productivity_agent_tools.writes import WriteReport


class ServiceNowStatus(BaseModel):
    """Connection status reported by the servicenow_status tool."""

    base_url: str = Field(description="Address of the ServiceNow instance in use.")
    session_source: str = Field(description="Where the browser session came from, and when.")
    username: str | None = Field(default=None, description="Signed-in user's username.")
    display_name: str | None = Field(default=None, description="Signed-in user's name.")
    dry_run: bool = Field(description="True if writes are currently blocked.")


class IncidentSummary(BaseModel):
    """One incident in a list."""

    number: str = Field(description="Incident number, such as INC0012345.")
    short_description: str | None = Field(default=None, description="One-line title.")
    state: str | None = Field(default=None, description="State, such as New or In Progress.")
    priority: str | None = Field(default=None, description="Priority, such as 4 - Low.")
    opened_at: str | None = Field(default=None, description="When it was opened.")
    updated_at: str | None = Field(default=None, description="When it last changed.")
    url: str | None = Field(default=None, description="Its form in the web interface.")


class Incident(IncidentSummary):
    """One incident in detail."""

    description: str | None = Field(default=None, description="Full description.")
    urgency: str | None = Field(
        default=None, description="Urgency, such as 2 - Medium. Null when not readable."
    )
    impact: str | None = Field(
        default=None,
        description="Impact, such as 3 - Low. Null when your account may not read it, as "
        "with urgency; the priority still reflects both.",
    )
    category: str | None = Field(default=None, description="Category.")
    assignment_group: str | None = Field(default=None, description="Group working on it.")
    assigned_to: str | None = Field(default=None, description="Person working on it.")
    resolved_at: str | None = Field(default=None, description="When it was resolved.")
    close_notes: str | None = Field(default=None, description="How it was resolved.")
    comments: str | None = Field(
        default=None, description="Comments visible to you, newest first, as one text."
    )


class IncidentWriteResult(WriteReport):
    """Outcome of creating an incident."""

    number: str | None = Field(default=None, description="The new incident's number.")
    sys_id: str | None = Field(default=None, description="The new incident's sys_id.")
    url: str | None = Field(default=None, description="Its form in the web interface.")


class Choice(BaseModel):
    """One choice of a field on a ServiceNow form."""

    value: str = Field(description="What is stored, and what the create tool sends.")
    label: str | None = Field(default=None, description="What the form shows.")
