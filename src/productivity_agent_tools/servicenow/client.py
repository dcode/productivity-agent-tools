"""ServiceNow client: the reads and writes the tools are built from."""

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import JsonValue

from productivity_agent_tools.api import ApiClient, WriteOutcome
from productivity_agent_tools.errors import (
    AgentToolsError,
    ApiError,
    NotFoundError,
    PermissionDeniedError,
)
from productivity_agent_tools.payload import JsonObject, objects, text, value
from productivity_agent_tools.servicenow.settings import ServiceNowSettings

USER_TABLE: str = "/api/now/table/sys_user"
# Every read is narrowed to the caller: an unfiltered query reports the whole table's
# row count and returns no rows, because the access rules hide other people's records.
MY_ID: str = "javascript:gs.getUserID()"
ME_PARAMS: dict[str, str | int] = {
    "sysparm_query": f"sys_id={MY_ID}",
    "sysparm_fields": "sys_id,user_name,name,email",
    "sysparm_limit": 1,
    "sysparm_exclude_reference_link": "true",
}

INCIDENT_TABLE: str = "/api/now/table/incident"
# Read with display values, so a state reads "In Progress" and a group by its name.
INCIDENT_FIELDS: str = (
    "sys_id,number,short_description,description,state,urgency,impact,priority,category,"
    "assignment_group,assigned_to,opened_at,sys_updated_on,resolved_at,close_notes,comments"
)
INCIDENT_SUMMARY_FIELDS: str = (
    "sys_id,number,short_description,state,priority,opened_at,sys_updated_on"
)
type Level = Literal["high", "medium", "low"]
# The out-of-the-box choice values for urgency and impact; priority follows from the two.
LEVELS: dict[str, str] = {"high": "1", "medium": "2", "low": "3"}
# Fields the create tool fills itself, so ``fields`` may not name them.
OWN_FIELDS: frozenset[str] = frozenset(
    {"caller_id", "short_description", "description", "urgency", "impact", "category"}
)
_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
CHOICE_TABLE: str = "/api/now/table/sys_choice"
CATEGORY_PARAMS: dict[str, str | int] = {
    "sysparm_query": "name=incident^element=category^inactive=false^language=en^ORDERBYsequence",
    "sysparm_fields": "value,label",
    "sysparm_limit": 500,
}


@dataclass(frozen=True, slots=True, kw_only=True)
class IncidentDraft:
    """What a new incident should say. Hashable, so it can key the repeat guard.

    Attributes:
        short_description: One-line title.
        category: The instance's category value, such as ``software``. The incident form
            requires one, though the Table API does not.
        description: Longer description, plain text.
        urgency: How soon it needs fixing.
        impact: How much it affects.
        fields: Other fields by column name (such as site-specific ``u_`` fields), sorted.
    """

    short_description: str
    category: str
    description: str | None = None
    urgency: Level | None = None
    impact: Level | None = None
    fields: tuple[tuple[str, str], ...] = ()

    @classmethod
    def build(
        cls,
        short_description: str,
        category: str,
        *,
        description: str | None = None,
        urgency: Level | None = None,
        impact: Level | None = None,
        fields: dict[str, str] | None = None,
    ) -> IncidentDraft:
        """Check and tidy what the user asked for.

        Args:
            short_description: One-line title; must not be blank.
            category: Category value; must not be blank.
            description: Longer description.
            urgency: high, medium or low.
            impact: high, medium or low.
            fields: Other fields by column name.

        Returns:
            The draft.

        Raises:
            AgentToolsError: If the title or category is blank, or a field name is not
                allowed.
        """
        title = short_description.strip()
        if not title:
            raise AgentToolsError("An incident needs a short_description (a one-line title).")
        chosen = category.strip()
        if not chosen:
            raise AgentToolsError(
                "An incident needs a category, as the helpdesk's form does. "
                "servicenow_list_incident_categories lists them."
            )
        extra: dict[str, str] = {}
        for name, field_value in (fields or {}).items():
            key = name.strip()
            if not _FIELD_NAME.match(key) or key.startswith("sys_"):
                raise AgentToolsError(
                    f"'{name}' is not a field this tool can set: use a column name such as "
                    "u_location, and no sys_ fields."
                )
            if key in OWN_FIELDS:
                raise AgentToolsError(
                    f"Set '{key}' with its own parameter, not in fields."
                    if key != "caller_id"
                    else "The caller is always you; caller_id cannot be set."
                )
            extra[key] = field_value
        return cls(
            short_description=title,
            description=(description or "").strip() or None,
            urgency=urgency,
            impact=impact,
            category=chosen,
            fields=tuple(sorted(extra.items())),
        )

    def payload(self, caller: str) -> dict[str, JsonValue]:
        """Build the request body.

        Args:
            caller: ``sys_id`` of the signed-in user, who is the caller.

        Returns:
            The JSON body for the Table API.
        """
        body: dict[str, JsonValue] = {
            "caller_id": caller,
            "short_description": self.short_description,
            "category": self.category,
        }
        if self.description:
            body["description"] = self.description
        if self.urgency:
            body["urgency"] = LEVELS[self.urgency]
        if self.impact:
            body["impact"] = LEVELS[self.impact]
        body.update(dict(self.fields))
        return body


def normalize_incident_number(number: str) -> str:
    """Tidy an incident number as typed, such as `` inc0012345 ``.

    Args:
        number: The number.

    Returns:
        The number in upper case.

    Raises:
        AgentToolsError: If it is blank or has characters a number cannot have.
    """
    tidy = number.strip().upper()
    if not re.fullmatch(r"[A-Z]+[0-9]+", tidy):
        raise AgentToolsError(f"'{number}' is not an incident number, such as INC0012345.")
    return tidy


class ServiceNow:
    """Reads from one ServiceNow instance through an ``ApiClient``.

    Attributes:
        settings: The ServiceNow settings.
    """

    def __init__(self, api: ApiClient, settings: ServiceNowSettings) -> None:
        """Create the client.

        Args:
            api: The adapter connected to the instance.
            settings: The ServiceNow settings.
        """
        self._api = api
        self.settings = settings

    @property
    def base_url(self) -> str:
        """Address of the instance."""
        return self._api.base_url

    async def me(self) -> JsonObject:
        """Read the signed-in user's own record.

        Returns:
            The record: ``sys_id``, ``user_name``, ``name`` and ``email``.

        Raises:
            AgentToolsError: If the session is not accepted or the record is not returned.
        """
        found = objects(value(await self._api.get(USER_TABLE, ME_PARAMS), "result"))
        if not found:
            raise ApiError(200, "ServiceNow did not return your own user record.")
        return found[0]

    def record_url(self, table: str, sys_id: str) -> str:
        """Address of a record's form in the instance's web interface.

        Args:
            table: Table name, such as ``incident``.
            sys_id: The record's ``sys_id``.

        Returns:
            The URL.
        """
        return f"{self.base_url}/{table}.do?sys_id={sys_id}"

    async def incident_categories(self) -> list[JsonObject]:
        """Read the incident form's category choices.

        Returns:
            The active choices, each with ``value`` and ``label``, in the form's order;
            one row per value.

        Raises:
            AgentToolsError: If the instance does not let the user read them.
        """
        rows = objects(value(await self._api.get(CHOICE_TABLE, CATEGORY_PARAMS), "result"))
        seen: set[str] = set()
        unique: list[JsonObject] = []
        for row in rows:
            choice = text(row, "value")
            if choice and choice not in seen:
                seen.add(choice)
                unique.append(row)
        return unique

    async def incident_category(self, category: str) -> str:
        """Match a category, by value or label in any case, against the form's choices.

        When the choices cannot be read (no access, or none listed), the category is
        sent as given, and the instance decides.

        Args:
            category: The category as the user or agent wrote it.

        Returns:
            The choice's value, or the category as given (trimmed) when unchecked.

        Raises:
            AgentToolsError: If the choices were read and none matches.
        """
        wanted = category.strip()
        try:
            choices = await self.incident_categories()
        except PermissionDeniedError, NotFoundError:
            return wanted
        folded = wanted.casefold()
        for row in choices:
            choice = text(row, "value") or ""
            if folded in (choice.casefold(), (text(row, "label") or "").casefold()):
                return choice
        if not choices:
            return wanted
        names = ", ".join(text(row, "label") or text(row, "value") or "" for row in choices)
        raise AgentToolsError(f"'{wanted}' is not an incident category here. Categories: {names}.")

    async def create_incident(self, draft: IncidentDraft) -> WriteOutcome:
        """Open an incident with the signed-in user as its caller.

        The user's own record is read first (even in dry-run) to fill the caller.

        Args:
            draft: What the incident says.

        Returns:
            The outcome; when sent, its response holds the new record under ``result``.

        Raises:
            AgentToolsError: If the session is not accepted or the instance refuses.
        """
        me = await self.me()
        caller = text(me, "sys_id")
        if not caller:
            raise ApiError(200, "ServiceNow returned your user record without its sys_id.")
        return await self._api.write("POST", INCIDENT_TABLE, draft.payload(caller))

    async def incident(self, number: str) -> JsonObject:
        """Read one incident by its number.

        Args:
            number: The incident number, such as ``INC0012345``.

        Returns:
            The record, with display values.

        Raises:
            AgentToolsError: If it is not found or not visible to the user.
        """
        tidy = normalize_incident_number(number)
        params: dict[str, str | int] = {
            "sysparm_query": f"number={tidy}",
            "sysparm_fields": INCIDENT_FIELDS,
            "sysparm_display_value": "true",
            "sysparm_exclude_reference_link": "true",
            "sysparm_limit": 1,
        }
        found = objects(value(await self._api.get(INCIDENT_TABLE, params), "result"))
        if not found:
            raise NotFoundError(f"No incident {tidy}, or it is not visible to your account.")
        return found[0]

    async def my_incidents(self, *, include_closed: bool, max_results: int) -> list[JsonObject]:
        """List incidents the user is the caller of or opened, newest first.

        Args:
            include_closed: Also list resolved, closed and cancelled incidents.
            max_results: Most incidents to return.

        Returns:
            The records, with display values.
        """
        # ^OR binds to the condition before it: (caller is me OR opened by me).
        query = f"caller_id={MY_ID}^ORopened_by={MY_ID}^ORDERBYDESCopened_at"
        if not include_closed:
            query = f"active=true^{query}"
        params: dict[str, str | int] = {
            "sysparm_query": query,
            "sysparm_fields": INCIDENT_SUMMARY_FIELDS,
            "sysparm_display_value": "true",
            "sysparm_exclude_reference_link": "true",
            "sysparm_limit": max_results,
        }
        return objects(value(await self._api.get(INCIDENT_TABLE, params), "result"))
