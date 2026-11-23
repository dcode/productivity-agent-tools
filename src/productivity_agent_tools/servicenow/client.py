"""ServiceNow client: the reads and writes the tools are built from."""

import dataclasses
import re

from pydantic import JsonValue

from productivity_agent_tools.api import ApiClient, WriteOutcome
from productivity_agent_tools.errors import AgentToolsError, ApiError, NotFoundError
from productivity_agent_tools.payload import JsonObject, as_object, objects, text, value
from productivity_agent_tools.servicenow.forms import CatalogForm, parse_form
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
CATALOG: str = "/api/sn_sc/servicecatalog"
_SYS_ID = re.compile(r"^[0-9a-f]{32}$")
_COLUMN = re.compile(r"^[a-z][a-z0-9_]*$")


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

    async def find_catalog_item(self, name: str) -> str:
        """Find a catalog item's ``sys_id`` by its exact name (any case), or take a sys_id.

        Args:
            name: The item's name, such as "Create an incident", or its ``sys_id``.

        Returns:
            The item's ``sys_id``.

        Raises:
            AgentToolsError: If no item has that name, naming the closest the search found,
                or several do, naming their sys_ids.
        """
        wanted = name.strip()
        if _SYS_ID.match(wanted):
            return wanted
        params: dict[str, str | int] = {"sysparm_text": wanted, "sysparm_limit": 20}
        found = objects(value(await self._api.get(f"{CATALOG}/items", params), "result"))
        named = [
            item
            for item in found
            if text(item, "sys_id")
            and (text(item, "name") or "").strip().casefold() == wanted.casefold()
        ]
        if len(named) == 1:
            return text(named[0], "sys_id") or ""
        if named:
            # Instances keep the stock item beside their own, often under the same name.
            listed = "; ".join(
                f"{text(item, 'sys_id')} ({text(item, 'short_description') or 'no description'})"
                for item in named
            )
            raise AgentToolsError(
                f"{len(named)} catalog items are named '{wanted}': {listed}. Set "
                "incident_form under [servicenow] to the sys_id of the one the portal uses "
                "(the sys_id= in its page address)."
            )
        names = "; ".join(text(item, "name") or "?" for item in found) or "none"
        raise NotFoundError(
            f"No catalog item is named '{wanted}'. A search for it found: {names}. Set "
            "incident_form under [servicenow] to the form's exact name or its sys_id."
        )

    async def catalog_form(self, name: str) -> CatalogForm:
        """Read a catalog item's form: its questions, choices and which are required.

        Args:
            name: The item's name or ``sys_id``.

        Returns:
            The form.

        Raises:
            AgentToolsError: If the item cannot be found or read.
        """
        item_id = await self.find_catalog_item(name)
        item = as_object(value(await self._api.get(f"{CATALOG}/items/{item_id}"), "result"), "form")
        form = parse_form(item)
        return form if form.sys_id else dataclasses.replace(form, sys_id=item_id)

    async def submit_form(self, form: CatalogForm, variables: dict[str, str]) -> WriteOutcome:
        """Submit a record producer's form, as the portal does.

        Args:
            form: The form, as read.
            variables: Checked answers by variable name.

        Returns:
            The outcome; when sent, its response names the new record under ``result``.
        """
        body: dict[str, JsonValue] = {"variables": dict(variables)}
        return await self._api.write("POST", f"{CATALOG}/items/{form.sys_id}/submit_producer", body)

    async def incident(self, number: str, extra_fields: tuple[str, ...] = ()) -> JsonObject:
        """Read one incident by its number.

        Args:
            number: The incident number, such as ``INC0012345``.
            extra_fields: More fields to read, by column name. When given, every field
                comes back as ``{"value": ..., "display_value": ...}``.

        Returns:
            The record: display values, or both forms when extra fields were asked for.

        Raises:
            AgentToolsError: If it is not found or not visible to the user, or a field
                name is not a column name.
        """
        tidy = normalize_incident_number(number)
        for name in extra_fields:
            if not _COLUMN.match(name):
                raise AgentToolsError(f"'{name}' is not a field name, such as u_site.")
        fields = ",".join(dict.fromkeys((*INCIDENT_FIELDS.split(","), *extra_fields)))
        params: dict[str, str | int] = {
            "sysparm_query": f"number={tidy}",
            "sysparm_fields": fields,
            "sysparm_display_value": "all" if extra_fields else "true",
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
