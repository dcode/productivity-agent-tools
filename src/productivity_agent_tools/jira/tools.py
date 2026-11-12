"""MCP tool definitions for Jira."""

from mcp.server.mcpserver import MCPServer

from productivity_agent_tools.api import WriteOutcome
from productivity_agent_tools.config import Settings
from productivity_agent_tools.errors import AtlassianError
from productivity_agent_tools.jira.client import (
    FieldChanges,
    JiraClient,
    normalize_duration,
    normalize_issue_key,
    normalize_project_key,
)
from productivity_agent_tools.jira.models import (
    CreateFields,
    EditFields,
    IssueDetail,
    LinkType,
    Preset,
    SearchResult,
    ServerStatus,
    Transition,
    WorklogList,
    WriteResult,
)
from productivity_agent_tools.repeats import RepeatGuard
from productivity_agent_tools.tooling import IDEMPOTENT_WRITE as _IDEMPOTENT_WRITE
from productivity_agent_tools.tooling import LOCAL_READ as _LOCAL_READ
from productivity_agent_tools.tooling import READ as _READ
from productivity_agent_tools.tooling import WRITE as _WRITE
from productivity_agent_tools.tooling import anticipated as _anticipated
from productivity_agent_tools.writes import once as _once
from productivity_agent_tools.writes import report


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
    created = outcome.response.get("id") if isinstance(outcome.response, dict) else None
    return WriteResult.model_validate(
        {
            **report(action, "Jira", outcome, done),
            "issue_key": issue_key,
            "created_id": str(created) if isinstance(created, str | int) else None,
        }
    )


def register_jira_tools(
    server: MCPServer,
    jira: JiraClient,
    settings: Settings,
    token_source: str,
    repeats: RepeatGuard[WriteResult] | None = None,
) -> None:
    """Register every Jira tool on an MCP server.

    Args:
        server: The MCP server to add tools to.
        jira: Jira client the tools call.
        settings: Resolved settings (presets and safety switches).
        token_source: Description of where the access token came from.
        repeats: Guard against identical repeated writes; a default one is made when None.
    """
    guard = repeats if repeats is not None else RepeatGuard[WriteResult]()

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
            extra_cookies=list(settings.connection_for(jira.settings).cookies),
        )

    @server.tool(annotations=_LOCAL_READ)
    @_anticipated
    async def jira_list_presets() -> list[Preset]:
        """List the user's named JQL presets (saved queries) from local configuration."""
        return [Preset(name=name, jql=jql) for name, jql in jira.settings.presets.items()]

    @server.tool(annotations=_READ)
    @_anticipated
    async def jira_run_preset(name: str, max_results: int | None = None) -> SearchResult:
        """Run one of the user's named JQL presets and return compact issue rows.

        Use this for daily views such as the user's open issues.

        Args:
            name: Preset name, as listed by jira_list_presets.
            max_results: Maximum rows to return (default from config, at most 100).
        """
        jql = jira.settings.presets.get(name)
        if jql is None:
            known = ", ".join(sorted(jira.settings.presets)) or "none configured"
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
        would have been made. Call this once per comment: an identical comment
        on the same issue within 30 seconds is not sent again.

        Args:
            key: Issue key, such as ABC-123.
            body: Comment text in Jira wiki markup.
        """
        issue_key = normalize_issue_key(key)

        async def send() -> WriteResult:
            outcome = await jira.add_comment(issue_key, body)
            return _write_result(
                "add_comment", issue_key, outcome, f"Comment added to {issue_key}."
            )

        return await _once(guard, ("add_comment", issue_key, body), send)

    @server.tool(annotations=_WRITE)
    @_anticipated
    async def jira_transition_issue(
        key: str, transition: str, resolution: str | None = None
    ) -> WriteResult:
        """Move an issue to a new workflow status. Visible to the whole team.

        Status and resolution change only through transitions. If the
        transition's screen asks for a resolution (see jira_list_transitions),
        pass one of the resolutions it lists.

        In dry-run mode nothing is sent and the result shows the request that
        would have been made; the transition name is still validated. Call this
        once per move: an identical transition of the same issue within 30
        seconds is not sent again.

        Args:
            key: Issue key, such as ABC-123.
            transition: Transition name or id from jira_list_transitions.
            resolution: Resolution to set, such as Fixed, when the transition offers one.
        """
        issue_key = normalize_issue_key(key)

        async def send() -> WriteResult:
            match, outcome = await jira.transition_issue(issue_key, transition, resolution)
            target = f" (now '{match.to_status}')" if match.to_status else ""
            return _write_result(
                "transition_issue",
                issue_key,
                outcome,
                f"{issue_key} moved via '{match.name}'{target}.",
            )

        return await _once(
            guard,
            (
                "transition_issue",
                issue_key,
                transition.strip().casefold(),
                (resolution or "").strip().casefold(),
            ),
            send,
        )

    @server.tool(annotations=_READ)
    @_anticipated
    async def jira_list_worklogs(key: str, max_results: int = 10) -> WorklogList:
        """List the time logged on an issue, with the total across all worklogs.

        Args:
            key: Issue key, such as ABC-123.
            max_results: How many of the newest worklogs to include (0 for totals only).
        """
        return await jira.list_worklogs(key, max_results)

    @server.tool(annotations=_WRITE)
    @_anticipated
    async def jira_log_work(
        key: str, time_spent: str, comment: str | None = None, started: str | None = None
    ) -> WriteResult:
        """Log time spent on an issue, as the user. Visible to the team.

        Jira lowers the remaining estimate by the same amount. Write any
        comment in Jira wiki markup, not Markdown.

        In dry-run mode nothing is sent and the result shows the request that
        would have been made. Call this once per worklog: an identical worklog
        on the same issue within 30 seconds is not sent again.

        Args:
            key: Issue key, such as ABC-123.
            time_spent: Duration in Jira's syntax: numbers with w, d, h or m, such as
                "1h 30m", "2d" or "45m".
            comment: What the work was, in Jira wiki markup.
            started: When the work started, in ISO 8601 such as 2026-11-11T09:30 (local
                time unless an offset is given) or a date; leave out for now.
        """
        issue_key = normalize_issue_key(key)
        duration = normalize_duration(time_spent)

        async def send() -> WriteResult:
            outcome = await jira.log_work(issue_key, duration, comment, started)
            return _write_result(
                "log_work", issue_key, outcome, f"Logged {duration} on {issue_key}."
            )

        return await _once(
            guard,
            ("log_work", issue_key, duration, (started or "").strip(), (comment or "").strip()),
            send,
        )

    @server.tool(annotations=_IDEMPOTENT_WRITE)
    @_anticipated
    async def jira_assign_issue(key: str, assignee: str) -> WriteResult:
        """Assign an issue to someone, to the user, or to nobody. Visible to the team.

        The person must be able to be assigned the issue. A name that matches
        several people is refused with the candidates listed: ask the user which
        one, then call again with that username. The person is looked up even
        in dry-run mode, where nothing is sent and the result shows the request
        that would have been made.

        Args:
            key: Issue key, such as ABC-123.
            assignee: "me" for the user, "none" to unassign, or a username, display
                name or email address.
        """
        issue_key = normalize_issue_key(key)
        who = await jira.find_assignee(issue_key, assignee)

        async def send() -> WriteResult:
            outcome = await jira.assign_issue(issue_key, who)
            target = (
                f"assigned to {who.display_name or who.username}" if who.username else "unassigned"
            )
            return _write_result(
                "assign_issue", issue_key, outcome, f"{issue_key} is now {target}."
            )

        return await _once(guard, ("assign_issue", issue_key, who.username), send)

    @server.tool(annotations=_READ)
    @_anticipated
    async def jira_get_edit_fields(key: str) -> EditFields:
        """List the fields an issue can be edited with, and the values they accept.

        Call this before jira_edit_issue when unsure whether a field can be
        changed or which values (priorities, components, versions) are allowed.

        Args:
            key: Issue key, such as ABC-123.
        """
        return await jira.edit_fields(key)

    @server.tool(annotations=_WRITE)
    @_anticipated
    async def jira_edit_issue(
        key: str,
        summary: str | None = None,
        description: str | None = None,
        issue_type: str | None = None,
        priority: str | None = None,
        reporter: str | None = None,
        due_date: str | None = None,
        original_estimate: str | None = None,
        remaining_estimate: str | None = None,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
        add_components: list[str] | None = None,
        remove_components: list[str] | None = None,
        add_fix_versions: list[str] | None = None,
        remove_fix_versions: list[str] | None = None,
        add_affects_versions: list[str] | None = None,
        remove_affects_versions: list[str] | None = None,
    ) -> WriteResult:
        """Change an issue's fields. Visible to the whole team; give only what should change.

        Every field is checked against the issue's edit screen and values are
        matched by name, so a refused field or value is reported with the
        choices. Labels, components and versions are added or removed one by
        one; the rest stay. Status and resolution change through
        jira_transition_issue, the assignee through jira_assign_issue, time
        spent through jira_log_work, and links through jira_link_issues.

        In dry-run mode nothing is sent and the result shows the request that
        would have been made. An identical edit within 30 seconds is not sent again.

        Args:
            key: Issue key, such as ABC-123.
            summary: New one-line title.
            description: New description in Jira wiki markup (replaces the old one).
            issue_type: New issue type name, if the edit screen allows changing it.
            priority: Priority name, such as High.
            reporter: "me", or a username, display name or email address.
            due_date: Date such as 2026-11-30, or "none" to clear it.
            original_estimate: Duration in Jira's syntax, such as 2d or 4h 30m.
            remaining_estimate: Duration in Jira's syntax.
            add_labels: Labels to add (single words).
            remove_labels: Labels to remove.
            add_components: Component names to add.
            remove_components: Component names to remove.
            add_fix_versions: Fix version names to add.
            remove_fix_versions: Fix version names to remove.
            add_affects_versions: Affects version names to add.
            remove_affects_versions: Affects version names to remove.
        """
        issue_key = normalize_issue_key(key)
        changes = FieldChanges(
            summary=summary,
            description=description,
            issue_type=issue_type,
            priority=priority,
            reporter=reporter,
            due_date=due_date,
            original_estimate=original_estimate,
            remaining_estimate=remaining_estimate,
            add_labels=add_labels,
            remove_labels=remove_labels,
            add_components=add_components,
            remove_components=remove_components,
            add_fix_versions=add_fix_versions,
            remove_fix_versions=remove_fix_versions,
            add_affects_versions=add_affects_versions,
            remove_affects_versions=remove_affects_versions,
        )

        async def send() -> WriteResult:
            changed, outcome = await jira.edit_issue(issue_key, changes)
            return _write_result(
                "edit_issue", issue_key, outcome, f"Updated {issue_key}: {', '.join(changed)}."
            )

        return await _once(guard, ("edit_issue", issue_key, repr(changes)), send)

    @server.tool(annotations=_READ)
    @_anticipated
    async def jira_get_create_fields(project: str, issue_type: str | None = None) -> CreateFields:
        """List the issue types a project offers and, for one of them, the fields to fill.

        Call this before jira_create_issue to see which fields are required and
        which values they accept.

        Args:
            project: Project key, such as ABC.
            issue_type: An issue type name, such as Bug, to list its fields.
        """
        return await jira.create_fields(project, issue_type)

    @server.tool(annotations=_WRITE)
    @_anticipated
    async def jira_create_issue(
        project: str,
        issue_type: str,
        summary: str,
        description: str | None = None,
        priority: str | None = None,
        assignee: str | None = None,
        reporter: str | None = None,
        due_date: str | None = None,
        original_estimate: str | None = None,
        labels: list[str] | None = None,
        components: list[str] | None = None,
        fix_versions: list[str] | None = None,
        affects_versions: list[str] | None = None,
        parent: str | None = None,
    ) -> WriteResult:
        """Create a new issue. Visible to the whole team; confirm the details with the user first.

        Fields are checked against the project's create screen for that issue
        type. If the screen requires a field this tool cannot fill (such as a
        custom field), it says so and nothing is created.

        In dry-run mode nothing is sent and the result shows the request that
        would have been made. An identical create within 30 seconds is not sent again.

        Args:
            project: Project key, such as ABC.
            issue_type: Issue type name, such as Bug, Story or Task.
            summary: One-line title.
            description: Description in Jira wiki markup.
            priority: Priority name, such as High.
            assignee: "me", or a person who can be assigned issues in the project.
            reporter: "me", or a username, display name or email address; defaults to the user.
            due_date: Date such as 2026-11-30.
            original_estimate: Duration in Jira's syntax, such as 2d.
            labels: Labels (single words).
            components: Component names.
            fix_versions: Fix version names.
            affects_versions: Affects version names.
            parent: Parent issue key, required for a sub-task type.
        """
        project_key = normalize_project_key(project)
        changes = FieldChanges(
            summary=summary,
            description=description,
            priority=priority,
            reporter=reporter,
            due_date=due_date,
            original_estimate=original_estimate,
            add_labels=labels,
            add_components=components,
            add_fix_versions=fix_versions,
            add_affects_versions=affects_versions,
        )

        async def send() -> WriteResult:
            chosen, outcome = await jira.create_issue(
                project_key, issue_type, changes, assignee, parent
            )
            new_key = outcome.response.get("key") if isinstance(outcome.response, dict) else None
            issue_key = new_key if isinstance(new_key, str) else f"{project_key} (new)"
            result = _write_result(
                "create_issue", issue_key, outcome, f"Created {chosen.name} {issue_key}."
            )
            return result.model_copy(update={"created_id": new_key}) if new_key else result

        return await _once(
            guard,
            (
                "create_issue",
                project_key,
                issue_type.strip().casefold(),
                repr(changes),
                (assignee or "").strip().casefold(),
                (parent or "").strip().upper(),
            ),
            send,
        )

    @server.tool(annotations=_READ)
    @_anticipated
    async def jira_list_link_types() -> list[LinkType]:
        """List the kinds of links between issues, with both of their wordings."""
        return await jira.link_types()

    @server.tool(annotations=_WRITE)
    @_anticipated
    async def jira_link_issues(
        key: str, relation: str, other_key: str, comment: str | None = None
    ) -> WriteResult:
        """Link two issues, read as a sentence: key, relation, other_key. Visible to the team.

        For example key=ABC-1, relation="blocks", other_key=ABC-2 means ABC-1
        blocks ABC-2. The relation can be a link type's name or either wording
        from jira_list_link_types, such as "is blocked by" or "relates to".

        In dry-run mode nothing is sent and the result shows the request that
        would have been made. An identical link within 30 seconds is not sent again.

        Args:
            key: The issue the sentence starts with, such as ABC-1.
            relation: How it relates to the other, such as "blocks" or "is duplicated by".
            other_key: The issue the sentence ends with.
            comment: Optional comment, in wiki markup, added along with the link.
        """
        first = normalize_issue_key(key)
        second = normalize_issue_key(other_key)

        async def send() -> WriteResult:
            sentence, outcome = await jira.link_issues(first, relation, second, comment)
            return _write_result("link_issues", first, outcome, f"Linked: {sentence}.")

        return await _once(
            guard,
            ("link_issues", first, " ".join(relation.split()).casefold(), second, comment or ""),
            send,
        )
