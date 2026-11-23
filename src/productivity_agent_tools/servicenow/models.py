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


class Comment(BaseModel):
    """One comment on a record's journal."""

    at: str | None = Field(default=None, description="When it was written, instance time.")
    author: str | None = Field(default=None, description="Who wrote it.")
    kind: str | None = Field(default=None, description="Its journal, such as Additional comments.")
    body: str = Field(description="The comment.")


class FieldValue(BaseModel):
    """A field read in both its forms."""

    value: str | None = Field(
        default=None, description="What is stored: for a field picked from a table, a sys_id."
    )
    display: str | None = Field(default=None, description="What the form shows for it.")


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
    comments: list[Comment] = Field(
        default_factory=list, description="Comments visible to you, newest first."
    )
    extra_fields: dict[str, FieldValue] = Field(
        default_factory=dict, description="The fields asked for with extra_fields."
    )


class IncidentWriteResult(WriteReport):
    """Outcome of opening an incident through the helpdesk's form."""

    number: str | None = Field(default=None, description="The new incident's number.")
    sys_id: str | None = Field(default=None, description="The new incident's sys_id.")
    url: str | None = Field(default=None, description="Its form in the web interface.")


class QuestionChoice(BaseModel):
    """One choice a form question offers."""

    value: str = Field(description="What is sent.")
    label: str = Field(description="What the form shows; either may be given as the answer.")


class FormQuestion(BaseModel):
    """One question on the incident form."""

    name: str = Field(description="The question's name; answers may be keyed by it.")
    label: str = Field(description="The question as the form shows it; also accepted as a key.")
    kind: str = Field(description="The kind of answer, such as select_box or multi_line_text.")
    shown: bool = Field(
        description="True if the form shows it, given the configured defaults; the form's "
        "rules can show or hide it as answers change. A hidden question takes no answer."
    )
    required: bool = Field(
        description="True if the form requires an answer, given the configured defaults; "
        "the form's rules can change this as answers change."
    )
    choices: list[QuestionChoice] = Field(
        default_factory=list, description="The choices; an answer must be one of them."
    )
    default: str | None = Field(
        default=None,
        description="The answer used when none is given: from the config's "
        "[servicenow.incident_defaults], else the form's own.",
    )
    help_text: str | None = Field(default=None, description="The form's help text.")
    lookup_table: str | None = Field(
        default=None,
        description="For a question answered by picking a record (such as a site): the table "
        "it is picked from. Its answer is that record's sys_id; a name is looked up there.",
    )


class IncidentForm(BaseModel):
    """The helpdesk's form for opening an incident."""

    name: str = Field(description="The form's name in the catalog.")
    source: str = Field(
        description="Where the form was read: the portal's page (as the portal shows it) "
        "or the catalog API (which can leave questions out)."
    )
    description: str | None = Field(default=None, description="The form's description.")
    problems: list[str] = Field(
        default_factory=list,
        description="Configured defaults that do not fit this form; servicenow_create_incident "
        "refuses until the user fixes them in the config.",
    )
    rules: list[str] = Field(
        default_factory=list,
        description="The form's rules in words: which answers show, hide or require which "
        "questions. servicenow_create_incident applies them to the answers given.",
    )
    questions: list[FormQuestion] = Field(description="The questions, in the form's order.")
