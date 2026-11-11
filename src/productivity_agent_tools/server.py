"""Assembles the MCP server from the configured product modules."""

from mcp.server.mcpserver import MCPServer

from productivity_agent_tools.api import ApiClient
from productivity_agent_tools.bitbucket.client import BitbucketClient
from productivity_agent_tools.bitbucket.tools import register_bitbucket_tools
from productivity_agent_tools.config import Settings
from productivity_agent_tools.jira.client import JiraClient
from productivity_agent_tools.jira.tools import register_jira_tools

_JIRA_INSTRUCTIONS: str = (
    "The jira_ tools work with the user's self-hosted Jira. Reads are safe to call freely. "
    "jira_add_comment, jira_transition_issue, jira_log_work and jira_assign_issue change "
    "team-visible data: confirm the exact comment text, transition, time or person with the "
    "user before calling them. When a write result says "
    "DRY RUN, nothing was changed; tell the user so and do not claim the update was made. "
    "Make each write call exactly once; if a script calls a tool, do not run the script's "
    "body twice. "
    "Jira text uses wiki markup, not Markdown."
)
_BITBUCKET_INSTRUCTIONS: str = (
    "The bitbucket_ tools work with the user's self-hosted Bitbucket. Reads are safe to "
    "call freely. bitbucket_add_pull_request_comment and bitbucket_review_pull_request "
    "change team-visible data: confirm the exact comment text or review with the user "
    "before calling them, and treat a DRY RUN result as nothing changed. Most take a "
    "project key and a "
    "repository slug; when the user names the project they work in, call "
    "bitbucket_set_default_project once and leave project out afterwards. To say what "
    "still blocks a pull request, use bitbucket_get_pull_request_merge_status. Bitbucket "
    "text uses Markdown."
)


def build_server(
    settings: Settings,
    jira_api: ApiClient | None = None,
    token_source: str = "",
    *,
    bitbucket_api: ApiClient | None = None,
    bitbucket_token_source: str = "",
) -> MCPServer:
    """Create the MCP server with the tools of every configured, connected product.

    A product's tools are registered only when its table is in the config and
    an adapter for it is given, so an agent is never offered a tool that
    cannot work.

    Args:
        settings: Resolved settings.
        jira_api: API adapter connected to Jira, or None to leave its tools out.
        token_source: Description of where the Jira access token came from.
        bitbucket_api: API adapter connected to Bitbucket, or None to leave its tools out.
        bitbucket_token_source: Description of where the Bitbucket access token came from.

    Returns:
        The MCP server, ready to run.
    """
    jira = settings.jira if jira_api is not None else None
    bitbucket = settings.bitbucket if bitbucket_api is not None else None
    instructions = " ".join(
        text
        for product, text in ((jira, _JIRA_INSTRUCTIONS), (bitbucket, _BITBUCKET_INSTRUCTIONS))
        if product is not None
    )
    server = MCPServer("productivity-agent-tools", instructions=instructions)
    if jira is not None and jira_api is not None:
        register_jira_tools(server, JiraClient(jira_api, jira), settings, token_source)
    if bitbucket is not None and bitbucket_api is not None:
        register_bitbucket_tools(
            server,
            BitbucketClient(bitbucket_api, bitbucket),
            bitbucket_token_source,
            list(settings.connection_for(bitbucket).cookies),
        )
    return server
