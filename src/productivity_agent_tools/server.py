"""Assembles the MCP server from the enabled product modules."""

from mcp.server.mcpserver import MCPServer

from productivity_agent_tools.api import ApiClient
from productivity_agent_tools.bitbucket.client import BitbucketClient
from productivity_agent_tools.bitbucket.tools import register_bitbucket_tools
from productivity_agent_tools.config import Settings
from productivity_agent_tools.jira.client import JiraClient
from productivity_agent_tools.jira.tools import register_jira_tools

_JIRA_INSTRUCTIONS: str = (
    "Tools for the user's self-hosted Jira. Reads are safe to call freely. "
    "jira_add_comment and jira_transition_issue change team-visible data: confirm the exact "
    "comment text or transition with the user before calling them. When a write result says "
    "DRY RUN, nothing was changed; tell the user so and do not claim the update was made. "
    "Make each write call exactly once; if a script calls a tool, do not run the script's "
    "body twice. "
    "Jira text uses wiki markup, not Markdown."
)
_BITBUCKET_INSTRUCTIONS: str = (
    " The bitbucket_ tools read pull requests from the user's self-hosted Bitbucket and "
    "change nothing. Bitbucket text uses Markdown."
)


def build_server(
    settings: Settings,
    jira_api: ApiClient,
    token_source: str,
    *,
    bitbucket_api: ApiClient | None = None,
    bitbucket_token_source: str = "",
) -> MCPServer:
    """Create the MCP server with every enabled module's tools registered.

    Args:
        settings: Resolved settings.
        jira_api: API adapter connected to Jira, already holding the access token.
        token_source: Description of where the Jira access token came from.
        bitbucket_api: API adapter connected to Bitbucket, or None to leave its tools out.
        bitbucket_token_source: Description of where the Bitbucket access token came from.

    Returns:
        The MCP server, ready to run.
    """
    bitbucket = settings.bitbucket if bitbucket_api is not None else None
    instructions = _JIRA_INSTRUCTIONS + (_BITBUCKET_INSTRUCTIONS if bitbucket else "")
    server = MCPServer("productivity-agent-tools", instructions=instructions)
    register_jira_tools(server, JiraClient(jira_api, settings.jira), settings, token_source)
    if bitbucket is not None and bitbucket_api is not None:
        register_bitbucket_tools(
            server,
            BitbucketClient(bitbucket_api, bitbucket),
            bitbucket_token_source,
            list(settings.connection_for(bitbucket).cookies),
        )
    return server
