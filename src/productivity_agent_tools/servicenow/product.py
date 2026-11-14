"""The ServiceNow product, as the server, configuration and CLI find it."""

from collections.abc import AsyncIterator

from productivity_agent_tools.payload import text
from productivity_agent_tools.products import Product
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
    "'productivity-agent-tools login servicenow' in a terminal, then continue."
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


PRODUCT: Product[ServiceNowSettings, ServiceNow] = Product(
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
