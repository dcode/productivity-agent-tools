"""ServiceNow client: the reads the tools are built from."""

from productivity_agent_tools.api import ApiClient
from productivity_agent_tools.errors import ApiError
from productivity_agent_tools.payload import JsonObject, objects, value
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
