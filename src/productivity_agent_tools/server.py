"""Assembles the MCP server from the enabled product modules."""

from mcp.server.mcpserver import MCPServer

from productivity_agent_tools.config import Settings
from productivity_agent_tools.http import AtlassianClient
from productivity_agent_tools.jira.client import JiraClient
from productivity_agent_tools.jira.tools import register_jira_tools

_INSTRUCTIONS: str = (
    "Tools for the user's self-hosted Jira. Reads are safe to call freely. "
    "jira_add_comment and jira_transition_issue change team-visible data: confirm the exact "
    "comment text or transition with the user before calling them. When a write result says "
    "DRY RUN, nothing was changed; tell the user so and do not claim the update was made. "
    "Jira text uses wiki markup, not Markdown."
)


def build_server(settings: Settings, core: AtlassianClient, token_source: str) -> MCPServer:
    """Create the MCP server with every enabled module's tools registered.

    Args:
        settings: Resolved settings.
        core: Shared HTTP core, already holding the access token.
        token_source: Description of where the access token came from.

    Returns:
        The MCP server, ready to run.
    """
    server = MCPServer("productivity-agent-tools", instructions=_INSTRUCTIONS)
    register_jira_tools(server, JiraClient(core, settings.jira), settings, token_source)
    return server
