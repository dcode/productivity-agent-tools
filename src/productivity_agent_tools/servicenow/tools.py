"""MCP tool definitions for ServiceNow: status, the user's incidents, and opening one."""

import dataclasses
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from productivity_agent_tools.payload import JsonObject, text
from productivity_agent_tools.products import ToolContext
from productivity_agent_tools.repeats import RepeatGuard
from productivity_agent_tools.servicenow.client import IncidentDraft, Level, ServiceNow
from productivity_agent_tools.servicenow.models import (
    Choice,
    Incident,
    IncidentSummary,
    IncidentWriteResult,
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
    sys_id = text(row, "sys_id")
    return IncidentSummary(
        number=text(row, "number") or "",
        short_description=_blank_to_none(row, "short_description"),
        state=_blank_to_none(row, "state"),
        priority=_blank_to_none(row, "priority"),
        opened_at=_blank_to_none(row, "opened_at"),
        updated_at=_blank_to_none(row, "sys_updated_on"),
        url=servicenow.record_url("incident", sys_id) if sys_id else None,
    )


def _blank_to_none(row: JsonObject, key: str) -> str | None:
    """Read a string field, treating ServiceNow's empty string as absent.

    Args:
        row: The record.
        key: Field name.

    Returns:
        The value, or None when it is missing or empty.
    """
    return text(row, key) or None


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
    async def servicenow_get_incident(number: str) -> Incident:
        """Read one incident in detail: state, who is working on it, comments and resolution.

        Args:
            number: Incident number, such as INC0012345.
        """
        row = await servicenow.incident(number)
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
        )

    @server.tool(annotations=READ)
    @anticipated
    async def servicenow_list_incident_categories() -> list[Choice]:
        """List the categories the helpdesk's incident form offers.

        servicenow_create_incident needs one; pick the closest to the problem and
        confirm it with the user.
        """
        rows = await servicenow.incident_categories()
        return [
            Choice(value=text(row, "value") or "", label=_blank_to_none(row, "label"))
            for row in rows
        ]

    @server.tool(annotations=WRITE)
    @anticipated
    async def servicenow_create_incident(
        short_description: str,
        category: str,
        description: str | None = None,
        urgency: Level | None = None,
        impact: Level | None = None,
        fields: dict[str, str] | None = None,
    ) -> IncidentWriteResult:
        """Open a helpdesk incident (a ticket) with the user as the caller.

        The helpdesk staff see it and act on it: confirm the wording with the
        user first, and check servicenow_list_incidents for one already open
        about the same problem. Leave urgency and impact out unless the user
        says how urgent it is; the instance then uses its defaults.

        A category is required, as on the helpdesk's form (the API alone would
        accept none). servicenow_list_incident_categories lists them; a label or
        value is accepted in any case, and an unknown one is refused with the list.

        In dry-run mode nothing is sent and the result shows the request that
        would have been made. An identical create within 30 seconds is not sent again.

        Args:
            short_description: One-line title, such as "VPN client fails to connect".
            category: Category label or value, such as Software or Network.
            description: What happened, what was tried, and any error text. Plain text.
            urgency: How soon it needs fixing: high, medium or low.
            impact: How much it affects: high (many people), medium or low (just the user).
            fields: Other fields by column name, such as {"u_location": "Building 2"}, for
                fields the instance requires that have no parameter here.
        """
        draft = IncidentDraft.build(
            short_description,
            category,
            description=description,
            urgency=urgency,
            impact=impact,
            fields=fields,
        )
        draft = dataclasses.replace(
            draft, category=await servicenow.incident_category(draft.category)
        )

        async def send() -> IncidentWriteResult:
            outcome = await servicenow.create_incident(draft)
            number = text(outcome.response, "result", "number")
            sys_id = text(outcome.response, "result", "sys_id")
            done = f"Opened incident {number}." if number else "Opened the incident."
            return IncidentWriteResult.model_validate(
                {
                    **report("create_incident", "ServiceNow", outcome, done),
                    "number": number,
                    "sys_id": sys_id,
                    "url": servicenow.record_url("incident", sys_id) if sys_id else None,
                }
            )

        return await once(guard, ("create_incident", draft), send)
