"""The ServiceNow product, as the server, configuration and CLI find it."""

from collections.abc import AsyncIterator

from productivity_agent_tools.payload import text
from productivity_agent_tools.products import RemoteProduct
from productivity_agent_tools.servicenow.client import ServiceNow
from productivity_agent_tools.servicenow.http import ServiceNowClient
from productivity_agent_tools.servicenow.login import sign_in
from productivity_agent_tools.servicenow.session import browser_session
from productivity_agent_tools.servicenow.settings import (
    ServiceNowSettings,
    parse_servicenow_settings,
)
from productivity_agent_tools.servicenow.tools import register_servicenow_tools

INSTRUCTIONS: str = (
    "The servicenow_ tools work with the user's ServiceNow helpdesk. It is signed in with "
    "a browser session that expires after a period of inactivity. When a servicenow_ tool "
    "reports that the session has expired, do not retry: ask the user to run "
    "'productivity-agent-tools login servicenow' in a terminal, then continue. "
    "servicenow_create_incident opens a ticket the helpdesk staff will see and act on: "
    "check servicenow_list_incidents for one already open about the same problem, read "
    "the form's questions with servicenow_get_incident_form, ask the user for required "
    "answers you do not know, and confirm the answers with them before creating it. "
    "servicenow_order_item requests a catalog item (such as software) for the user: find "
    "it with servicenow_search_catalog (or, when nothing listed fits, use the result's "
    "unlisted_item), agree the item with the user, check "
    "servicenow_list_requests for one already open, read its questions with "
    "servicenow_get_catalog_form, and confirm the answers before ordering."
)


async def doctor(servicenow: ServiceNow) -> AsyncIterator[str]:
    """Check that the stored browser session is accepted.

    Args:
        servicenow: The ServiceNow client.

    Yields:
        One line per check that passed.

    Raises:
        AgentToolsError: When a check fails.
    """
    me = await servicenow.me()
    who = text(me, "name") or text(me, "user_name")
    yield f"authenticated to {servicenow.base_url} as {who}"


PRODUCT: RemoteProduct[ServiceNowSettings, ServiceNow] = RemoteProduct(
    name="servicenow",
    title="ServiceNow",
    instructions=INSTRUCTIONS,
    settings_type=ServiceNowSettings,
    adapter=ServiceNowClient.from_connection,
    parse_settings=parse_servicenow_settings,
    make_client=ServiceNow,
    register_tools=register_servicenow_tools,
    doctor=doctor,
    resolve_credential=browser_session,
    login=sign_in,
)
