"""MCP tool definitions for ServiceNow. Every tool here is a read."""

from mcp.server.mcpserver import MCPServer

from productivity_agent_tools.payload import text
from productivity_agent_tools.products import ToolContext
from productivity_agent_tools.servicenow.client import ServiceNow
from productivity_agent_tools.servicenow.models import ServiceNowStatus
from productivity_agent_tools.tooling import READ, anticipated


def register_servicenow_tools(
    server: MCPServer, servicenow: ServiceNow, context: ToolContext
) -> None:
    """Add the ServiceNow tools to a server.

    Args:
        server: The MCP server to add tools to.
        servicenow: ServiceNow client the tools call.
        context: Dry-run and session source for the status tool.
    """

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
