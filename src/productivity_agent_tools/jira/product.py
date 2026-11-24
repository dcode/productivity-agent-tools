"""The Jira product, as the server, configuration and CLI find it."""

from collections.abc import AsyncIterator

from productivity_agent_tools.http import AtlassianClient
from productivity_agent_tools.jira.client import JiraClient
from productivity_agent_tools.jira.settings import JiraSettings, parse_jira_settings
from productivity_agent_tools.jira.tools import register_jira_tools
from productivity_agent_tools.products import RemoteProduct

INSTRUCTIONS: str = (
    "The jira_ tools work with the user's self-hosted Jira. Reads are safe to call freely. "
    "jira_add_comment, jira_transition_issue, jira_log_work, jira_assign_issue, "
    "jira_edit_issue, jira_create_issue and jira_link_issues change team-visible data: "
    "confirm exactly what will change with the user before calling them. When a write result says "
    "DRY RUN, nothing was changed; tell the user so and do not claim the update was made. "
    "Make each write call exactly once; if a script calls a tool, do not run the script's "
    "body twice. "
    "When a jira_ tool reports that the access token was rejected or cannot be found, "
    "do not retry: ask the user to store a new one with "
    "'productivity-agent-tools set-token jira' in a terminal, then continue. "
    "Jira text uses wiki markup, not Markdown. An Epic is created with epic_name; other "
    "issues join an epic with epic (the epic's key)."
)


async def doctor(jira: JiraClient) -> AsyncIterator[str]:
    """Check login, the server version and search.

    Args:
        jira: The Jira client.

    Yields:
        One line per check that passed.

    Raises:
        AgentToolsError: When a check fails.
    """
    profile = await jira.myself()
    yield f"authenticated to {jira.base_url} as {profile.get('displayName')}"
    yield f"Jira version {await jira.server_version()}"
    result = await jira.search(jira.settings.presets.get("mine-open", ""), 1)
    yield f"search works ({result.total} open issue(s) assigned to you)"


PRODUCT: RemoteProduct[JiraSettings, JiraClient] = RemoteProduct(
    name="jira",
    title="Jira",
    instructions=INSTRUCTIONS,
    settings_type=JiraSettings,
    adapter=AtlassianClient.from_connection,
    parse_settings=parse_jira_settings,
    make_client=JiraClient,
    register_tools=register_jira_tools,
    toolsets={
        "edit": "Create and edit issues, read their create and edit screens, and link issues.",
        "worklog": "Log work on an issue and list its worklogs.",
    },
    doctor=doctor,
)
