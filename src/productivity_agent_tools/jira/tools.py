"""MCP tool definitions for Jira."""

import functools
from collections.abc import Awaitable, Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from productivity_agent_tools.api import WriteOutcome
from productivity_agent_tools.config import Settings
from productivity_agent_tools.errors import AtlassianError
from productivity_agent_tools.jira.client import JiraClient, normalize_issue_key
from productivity_agent_tools.jira.models import (
    IssueDetail,
    Preset,
    SearchResult,
    ServerStatus,
    Transition,
    WriteRequest,
    WriteResult,
)

_READ = ToolAnnotations(read_only_hint=True, open_world_hint=True)
_WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True
)


def _anticipated[**P, R](tool: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Report this package's own errors to the model with their message intact.

    The MCP SDK hides the text of unexpected exceptions from the model. Errors
    raised on purpose here carry plain-language guidance (for example "check
    your network or VPN connection"), so they are re-raised as the SDK's anticipated
    ``ToolError``, which is passed through.

    Args:
        tool: The async tool function to wrap.

    Returns:
        The wrapped tool, with the same signature.
    """

    @functools.wraps(tool)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await tool(*args, **kwargs)
        except AtlassianError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


def _write_result(action: str, issue_key: str, outcome: WriteOutcome, done: str) -> WriteResult:
    """Convert a write outcome into the tool's result model.

    Args:
        action: Short name of the action.
        issue_key: Issue the action applies to.
        outcome: Outcome from the API adapter.
        done: Sentence describing the change when it was sent.

    Returns:
        The tool result.
    """
    message = (
        done
        if outcome.sent
        else (
            "DRY RUN: nothing was sent to Jira. The request shown is what would be sent. "
            "The user must restart the server with --no-dry-run (or dry_run = false in the "
            "config) to allow writes."
        )
    )
    return WriteResult(
        action=action,
        issue_key=issue_key,
        sent=outcome.sent,
        dry_run=not outcome.sent,
        request=WriteRequest(method=outcome.method, path=outcome.path, body=outcome.payload),
        message=message,
    )


def register_jira_tools(
    server: MCPServer, jira: JiraClient, settings: Settings, token_source: str
) -> None:
    """Register every Jira tool on an MCP server.

    Args:
        server: The MCP server to add tools to.
        jira: Jira client the tools call.
        settings: Resolved settings (presets and safety switches).
        token_source: Description of where the access token came from.
    """

    @server.tool(annotations=_READ)
    @_anticipated
    async def jira_status() -> ServerStatus:
        """Check the Jira connection and report who is logged in and whether writes are blocked.

        Call this first if another Jira tool fails, to see what is wrong.
        """
        profile = await jira.myself()
        name = profile.get("name")
        display = profile.get("displayName")
        return ServerStatus(
            base_url=jira.base_url,
            jira_version=await jira.server_version(),
            username=name if isinstance(name, str) else None,
            display_name=display if isinstance(display, str) else None,
            token_source=token_source,
            dry_run=settings.dry_run,
            extra_cookies=list(settings.connection_for(settings.jira).cookies),
        )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
    @_anticipated
    async def jira_list_presets() -> list[Preset]:
        """List the user's named JQL presets (saved queries) from local configuration."""
        return [Preset(name=name, jql=jql) for name, jql in settings.jira.presets.items()]

    @server.tool(annotations=_READ)
    @_anticipated
    async def jira_run_preset(name: str, max_results: int | None = None) -> SearchResult:
        """Run one of the user's named JQL presets and return compact issue rows.

        Use this for daily views such as the user's open issues.

        Args:
            name: Preset name, as listed by jira_list_presets.
            max_results: Maximum rows to return (default from config, at most 100).
        """
        jql = settings.jira.presets.get(name)
        if jql is None:
            known = ", ".join(sorted(settings.jira.presets)) or "none configured"
            raise AtlassianError(f"Unknown preset '{name}'. Available presets: {known}.")
        return await jira.search(jql, max_results)

    @server.tool(annotations=_READ)
    @_anticipated
    async def jira_search(jql: str, max_results: int | None = None) -> SearchResult:
        """Search Jira with an ad-hoc JQL query and return compact issue rows.

        Args:
            jql: A JQL query, for example 'project = ABC AND status = "In Progress"'.
            max_results: Maximum rows to return (default from config, at most 100).
        """
        return await jira.search(jql, max_results)

    @server.tool(annotations=_READ)
    @_anticipated
    async def jira_get_issue(key: str, max_comments: int = 10) -> IssueDetail:
        """Get one issue in full: description, labels, people, and its most recent comments.

        Text fields are in Jira wiki markup, not Markdown.

        Args:
            key: Issue key, such as ABC-123.
            max_comments: How many of the newest comments to include (0 for none).
        """
        return await jira.get_issue(key, max_comments)

    @server.tool(annotations=_READ)
    @_anticipated
    async def jira_list_transitions(key: str) -> list[Transition]:
        """List the workflow transitions currently available for an issue.

        Call this before jira_transition_issue to see valid transition names.

        Args:
            key: Issue key, such as ABC-123.
        """
        return await jira.list_transitions(key)

    @server.tool(annotations=_WRITE)
    @_anticipated
    async def jira_add_comment(key: str, body: str) -> WriteResult:
        """Add a comment to an issue. Visible to the whole team and attributed to the user.

        Write the body in Jira wiki markup, NOT Markdown: *bold*, _italic_,
        {{monospace}}, {code}...{code}, [link text|https://url], "h3. " for
        headings, "* " for bullets, "# " for numbered items.

        In dry-run mode nothing is sent and the result shows the request that
        would have been made.

        Args:
            key: Issue key, such as ABC-123.
            body: Comment text in Jira wiki markup.
        """
        issue_key = normalize_issue_key(key)
        outcome = await jira.add_comment(issue_key, body)
        return _write_result("add_comment", issue_key, outcome, f"Comment added to {issue_key}.")

    @server.tool(annotations=_WRITE)
    @_anticipated
    async def jira_transition_issue(key: str, transition: str) -> WriteResult:
        """Move an issue to a new workflow status. Visible to the whole team.

        In dry-run mode nothing is sent and the result shows the request that
        would have been made; the transition name is still validated.

        Args:
            key: Issue key, such as ABC-123.
            transition: Transition name or id from jira_list_transitions.
        """
        issue_key = normalize_issue_key(key)
        match, outcome = await jira.transition_issue(issue_key, transition)
        target = f" (now '{match.to_status}')" if match.to_status else ""
        return _write_result(
            "transition_issue",
            issue_key,
            outcome,
            f"{issue_key} moved via '{match.name}'{target}.",
        )
