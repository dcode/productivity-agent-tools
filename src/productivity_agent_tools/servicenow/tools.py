"""MCP tool definitions for ServiceNow: status, the user's incidents, and opening one."""

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from productivity_agent_tools.payload import JsonObject, text
from productivity_agent_tools.products import ToolContext
from productivity_agent_tools.repeats import RepeatGuard
from productivity_agent_tools.servicenow.client import ServiceNow
from productivity_agent_tools.servicenow.models import (
    FieldValue,
    FormQuestion,
    Incident,
    IncidentForm,
    IncidentSummary,
    IncidentWriteResult,
    QuestionChoice,
    ServiceNowStatus,
)
from productivity_agent_tools.tooling import READ, WRITE, anticipated
from productivity_agent_tools.writes import once, report


def _summary(servicenow: ServiceNow, row: JsonObject) -> IncidentSummary:
    """Read the fields every incident model has from a record with display values.

    Args:
        servicenow: The client, for the record's address.
        row: The record.

    Returns:
        The summary.
    """
    sys_id = _stored(row, "sys_id")
    return IncidentSummary(
        number=_shown(row, "number") or "",
        short_description=_blank_to_none(row, "short_description"),
        state=_blank_to_none(row, "state"),
        priority=_blank_to_none(row, "priority"),
        opened_at=_blank_to_none(row, "opened_at"),
        updated_at=_blank_to_none(row, "sys_updated_on"),
        url=servicenow.record_url("incident", sys_id) if sys_id else None,
    )


def _shown(row: JsonObject, key: str) -> str | None:
    """Read a field as the form shows it, whether it came as text or in both forms.

    Args:
        row: The record, read with ``sysparm_display_value`` ``true`` or ``all``.
        key: Field name.

    Returns:
        The display value, or None when the field is missing.
    """
    return text(row, key) if not isinstance(row.get(key), dict) else text(row, key, "display_value")


def _stored(row: JsonObject, key: str) -> str | None:
    """Read a field's stored value (a reference's sys_id), whichever form it came in.

    Args:
        row: The record.
        key: Field name.

    Returns:
        The stored value, or None when the field is missing.
    """
    return text(row, key) if not isinstance(row.get(key), dict) else text(row, key, "value")


def _blank_to_none(row: JsonObject, key: str) -> str | None:
    """Read a field's display value, treating ServiceNow's empty string as absent.

    Args:
        row: The record.
        key: Field name.

    Returns:
        The value, or None when it is missing or empty.
    """
    return _shown(row, key) or None


def register_servicenow_tools(
    server: MCPServer,
    servicenow: ServiceNow,
    context: ToolContext,
    repeats: RepeatGuard[IncidentWriteResult] | None = None,
) -> None:
    """Add the ServiceNow tools to a server.

    Args:
        server: The MCP server to add tools to.
        servicenow: ServiceNow client the tools call.
        context: Dry-run and session source for the status tool.
        repeats: Guard against identical repeated writes; a default one is made when None.
    """
    guard = repeats if repeats is not None else RepeatGuard[IncidentWriteResult]()

    @server.tool(annotations=READ)
    @anticipated
    async def servicenow_status() -> ServiceNowStatus:
        """Check the ServiceNow connection: who is signed in, when they signed in, and dry-run.

        Call this first if another servicenow_ tool fails, to see what is wrong. If
        the browser session has expired, only the user can fix that: ask them to
        run 'productivity-agent-tools login servicenow'.
        """
        me = await servicenow.me()
        return ServiceNowStatus(
            base_url=servicenow.base_url,
            session_source=await context.current_source(),
            username=text(me, "user_name"),
            display_name=text(me, "name"),
            dry_run=context.dry_run,
        )

    @server.tool(annotations=READ)
    @anticipated
    async def servicenow_list_incidents(
        include_closed: bool = False,
        max_results: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> list[IncidentSummary]:
        """List the user's own incidents (tickets they raised or are the caller of), newest first.

        Check this before servicenow_create_incident, so the same problem is not
        reported twice.

        Args:
            include_closed: Also list resolved, closed and cancelled incidents.
            max_results: Most incidents to return.
        """
        rows = await servicenow.my_incidents(include_closed=include_closed, max_results=max_results)
        return [_summary(servicenow, row) for row in rows]

    @server.tool(annotations=READ)
    @anticipated
    async def servicenow_get_incident(
        number: str, extra_fields: list[str] | None = None
    ) -> Incident:
        """Read one incident in detail: state, who is working on it, comments and resolution.

        extra_fields reads more fields by column name (such as u_site), each with its
        stored value and the text shown for it. For a field picked from a table, the
        stored value is the picked record's sys_id, which is what the incident form
        takes as the answer to that question.

        Args:
            number: Incident number, such as INC0012345.
            extra_fields: More fields to read, by column name.
        """
        extras = tuple(name.strip() for name in extra_fields or [] if name.strip())
        row = await servicenow.incident(number, extras)
        return Incident(
            **_summary(servicenow, row).model_dump(),
            description=_blank_to_none(row, "description"),
            urgency=_blank_to_none(row, "urgency"),
            impact=_blank_to_none(row, "impact"),
            category=_blank_to_none(row, "category"),
            assignment_group=_blank_to_none(row, "assignment_group"),
            assigned_to=_blank_to_none(row, "assigned_to"),
            resolved_at=_blank_to_none(row, "resolved_at"),
            close_notes=_blank_to_none(row, "close_notes"),
            comments=_blank_to_none(row, "comments"),
            extra_fields={
                name: FieldValue(value=_stored(row, name), display=_shown(row, name))
                for name in extras
            },
        )

    @server.tool(annotations=READ)
    @anticipated
    async def servicenow_get_incident_form() -> IncidentForm:
        """Show the helpdesk's incident form: its questions, what is required, the choices.

        Call this before servicenow_create_incident and answer its questions from
        what the user has said. A question with a default (set by the user in the
        config, or by the form) need not be answered. Ask the user for any required
        answer you do not know; never invent a phone number or a site. A question
        with a lookup_table takes a record's sys_id; a name is looked up in that
        table, and used if exactly one record has it.
        """
        form = await servicenow.catalog_form(servicenow.settings.incident_form)
        defaults = await servicenow.with_lookups(form, servicenow.settings.incident_defaults)
        configured, problems = form.configured_each(defaults)
        return IncidentForm(
            name=form.name,
            source=form.source,
            problems=problems,
            description=form.description or None,
            questions=[
                FormQuestion(
                    name=question.name,
                    label=question.label,
                    kind=question.kind,
                    required=question.mandatory,
                    choices=[
                        QuestionChoice(value=choice.value, label=choice.label)
                        for choice in question.choices
                    ],
                    default=configured.get(question.name) or question.default or None,
                    help_text=question.help_text or None,
                    lookup_table=question.lookup_table or None,
                )
                for question in form.questions
            ],
        )

    @server.tool(annotations=WRITE)
    @anticipated
    async def servicenow_create_incident(answers: dict[str, str]) -> IncidentWriteResult:
        """Open a helpdesk incident (a ticket) by submitting the "Create an incident" form.

        The helpdesk staff see it and act on it: check servicenow_list_incidents
        for one already open about the same problem, and confirm the answers with
        the user first. The form is submitted as the portal would, so the user is
        the caller and the helpdesk's own rules fill the incident from it.

        Answers are checked against the form before anything is sent: every
        required question must be answered (or have a default), and a choice must
        be one the form offers, given by its label or value in any case. If not,
        the error says what is missing or what the choices are, and nothing is sent.

        In dry-run mode nothing is sent and the result shows the request that
        would have been made. An identical create within 30 seconds is not sent again.

        Args:
            answers: Answers by question name or label, as servicenow_get_incident_form
                lists them, such as {"Category": "Software", "Please describe your issue
                below": "..."}. An empty answer clears a default.
        """
        form = await servicenow.catalog_form(servicenow.settings.incident_form)
        variables = form.answers(
            await servicenow.with_lookups(form, answers),
            await servicenow.with_lookups(form, servicenow.settings.incident_defaults),
        )

        async def send() -> IncidentWriteResult:
            outcome = await servicenow.submit_form(form, variables)
            number = text(outcome.response, "result", "number")
            sys_id = text(outcome.response, "result", "sys_id")
            table = text(outcome.response, "result", "table") or "incident"
            done = f"Opened incident {number}." if number else "Submitted the incident form."
            return IncidentWriteResult.model_validate(
                {
                    **report("create_incident", "ServiceNow", outcome, done),
                    "number": number,
                    "sys_id": sys_id,
                    "url": servicenow.record_url(table, sys_id) if sys_id else None,
                }
            )

        return await once(guard, ("create_incident", form.sys_id, tuple(variables.items())), send)
