"""MCP tool definitions for the Obsidian vault. Every tool here only reads the vault."""

import asyncio
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from productivity_agent_tools.obsidian.edits import VaultEditor
from productivity_agent_tools.obsidian.models import (
    DailyNote,
    FoundTasks,
    Note,
    ObsidianStatus,
    SectionWrite,
    TaskList,
    TaskWrite,
)
from productivity_agent_tools.obsidian.sync import SyncReport, VaultSync
from productivity_agent_tools.obsidian.vault import TaskState, Vault
from productivity_agent_tools.products import ToolContext
from productivity_agent_tools.tooling import (
    LOCAL_IDEMPOTENT_WRITE,
    LOCAL_READ,
    LOCAL_WRITE,
    anticipated,
)
from productivity_agent_tools.toolsets import toolset


def register_obsidian_tools(server: MCPServer, vault: Vault, context: ToolContext) -> None:
    """Add the Obsidian tools to a server.

    Args:
        server: The MCP server to add tools to.
        vault: The vault the tools read.
        context: Dry-run, for the status tool.
    """
    editor = VaultEditor(vault)
    syncer = VaultSync(vault)
    writing = asyncio.Lock()

    @server.tool(annotations=LOCAL_READ)
    @anticipated
    async def obsidian_status() -> ObsidianStatus:
        """Check the Obsidian vault: where it is, how many notes and tasks it holds, dry-run.

        Call this first if another obsidian_ tool fails. It also lists notes whose
        properties (frontmatter) could not be read.
        """
        status = await vault.status()
        return status.model_copy(update={"dry_run": context.dry_run})

    @server.tool(annotations=LOCAL_READ)
    @anticipated
    async def obsidian_list_tasks(
        state: TaskState = "open",
        scheduled_on: Annotated[
            str | None, Field(description="Only tasks scheduled for this day, YYYY-MM-DD.")
        ] = None,
        due_by: Annotated[
            str | None, Field(description="Only tasks due on or before this day, YYYY-MM-DD.")
        ] = None,
        project: Annotated[
            str | None, Field(description="Only tasks with a project containing this text.")
        ] = None,
        context: Annotated[
            str | None, Field(description="Only tasks with a context containing this text.")
        ] = None,
        has_key: Annotated[
            str | None,
            Field(description="Only tasks tied to this source: jira, bitbucket, uid ..."),
        ] = None,
        max_results: int | None = None,
    ) -> TaskList:
        """List the user's task notes (TaskNotes), earliest scheduled or due first.

        Args:
            state: open (not finished, the default), done, or all.
            scheduled_on: Only tasks planned for this day.
            due_by: Only tasks due on or before this day.
            project: Only tasks in a matching project.
            context: Only tasks with a matching context.
            has_key: Only tasks carrying this source key.
            max_results: Most tasks to return (default from config, at most 500).
        """
        return await vault.list_tasks(
            state=state,
            scheduled_on=scheduled_on,
            due_by=due_by,
            project=project,
            context=context,
            has_key=has_key,
            max_results=max_results,
        )

    @server.tool(annotations=LOCAL_READ)
    @anticipated
    async def obsidian_find_task(
        key_field: Annotated[
            str | None, Field(description="Source key property: jira, bitbucket, uid ...")
        ] = None,
        key: Annotated[str | None, Field(description="The key's value, such as ABC-123.")] = None,
        title: Annotated[
            str | None, Field(description="A title to compare with existing tasks' titles.")
        ] = None,
        min_score: Annotated[float, Field(ge=0, le=1)] = 0.6,
        max_results: Annotated[int, Field(ge=1, le=25)] = 5,
    ) -> FoundTasks:
        """Look for a task note that already exists, before suggesting a new one.

        Matches by source key (exact) and by title (similar), finished tasks included.
        A task with the same key IS the same task; a similar title only might be, so
        show those to the user rather than deciding.

        Args:
            key_field: The key's property name.
            key: The key's value.
            title: The title to compare.
            min_score: Least title similarity to report, 0 to 1.
            max_results: Most similar tasks to report.
        """
        return await vault.find_tasks(
            key_field=key_field,
            key=key,
            title=title,
            min_score=min_score,
            max_results=max_results,
        )

    @server.tool(annotations=LOCAL_READ)
    @anticipated
    async def obsidian_get_note(
        note: Annotated[
            str,
            Field(
                description="Path in the vault (.md optional), a link such as [[ABC-123]], or "
                "a key: ical_uid:..., jira:ABC-123, bitbucket:PROJ/repo#42."
            ),
        ],
        max_chars: Annotated[int, Field(ge=1)] = 20_000,
    ) -> Note:
        """Read one note from the vault: its properties and its text.

        Args:
            note: The note's path relative to the vault, or its [[link]].
            max_chars: Longest text to return.
        """
        return await vault.get_note(note, max_chars)

    @server.tool(annotations=LOCAL_READ)
    @anticipated
    async def obsidian_get_daily_note(
        day: Annotated[
            str | None,
            Field(description="YYYY-MM-DD, today, yesterday or tomorrow; default today."),
        ] = None,
        max_chars: Annotated[int, Field(ge=1)] = 20_000,
    ) -> DailyNote:
        """Read a day's daily note, from the place Obsidian keeps it.

        Use it to see what was planned or written on a day, such as yesterday's
        unfinished items. When the note does not exist yet, the result says where it
        would be.

        Args:
            day: The day.
            max_chars: Longest text to return.
        """
        return await vault.get_daily_note(day, max_chars)

    @server.tool(annotations=LOCAL_WRITE, meta=toolset("tasks"))
    @anticipated
    async def obsidian_create_task(
        title: str,
        key_field: Annotated[
            str | None,
            Field(
                description="Source key property when the task comes from elsewhere: jira, "
                "bitbucket, servicenow."
            ),
        ] = None,
        key: Annotated[str | None, Field(description="The key's value, such as ABC-123.")] = None,
        status: str = "open",
        priority: Annotated[str, Field(description="none, low, normal or high.")] = "normal",
        due: Annotated[str | None, Field(description="YYYY-MM-DD, or with THH:MM.")] = None,
        scheduled: Annotated[
            str | None, Field(description="Day planned for: YYYY-MM-DD, or with THH:MM.")
        ] = None,
        contexts: list[str] | None = None,
        projects: list[str] | None = None,
        time_estimate: Annotated[int | None, Field(ge=1, description="Minutes.")] = None,
        body: Annotated[str | None, Field(description="Markdown under the properties.")] = None,
        allow_similar: Annotated[
            bool,
            Field(
                description="True only after the user said it differs from the similar "
                "tasks reported."
            ),
        ] = False,
    ) -> TaskWrite:
        """Make a task note (TaskNotes) for something the user is responsible for.

        Refuses when a task with the same key exists (returned in "existing"), and holds
        the task back when open tasks have a close title (returned in "similar"): ask the
        user, and call again with allow_similar=true only if it is a different task. A
        task made without a key gets its own uid.

        Args:
            title: The task's title.
            key_field: The source key's property.
            key: The key's value.
            status: TaskNotes status, open by default.
            priority: TaskNotes priority.
            due: Due day.
            scheduled: Day the user plans to do it.
            contexts: Contexts, such as @office.
            projects: Projects, often [[links]].
            time_estimate: Estimate in minutes.
            body: Text for the note.
            allow_similar: Create despite similar open tasks.
        """
        async with writing:
            return await asyncio.to_thread(
                lambda: editor.create_task(
                    title=title,
                    key_field=key_field,
                    key=key,
                    status=status,
                    priority=priority,
                    due=due,
                    scheduled=scheduled,
                    contexts=contexts,
                    projects=projects,
                    time_estimate=time_estimate,
                    body=body,
                    allow_similar=allow_similar,
                )
            )

    @server.tool(annotations=LOCAL_IDEMPOTENT_WRITE, meta=toolset("tasks"))
    @anticipated
    async def obsidian_update_task(
        task: Annotated[
            str,
            Field(description="The task's path, [[link]], or key as field:value (jira:ABC-123)."),
        ],
        status: str | None = None,
        priority: str | None = None,
        due: Annotated[str | None, Field(description="YYYY-MM-DD, or with THH:MM.")] = None,
        scheduled: Annotated[
            str | None, Field(description="Day planned for: YYYY-MM-DD, or with THH:MM.")
        ] = None,
        contexts: list[str] | None = None,
        projects: list[str] | None = None,
        time_estimate: Annotated[int | None, Field(ge=1, description="Minutes.")] = None,
        clear: Annotated[
            list[str] | None,
            Field(
                description="Fields to remove: status, priority, due, scheduled, contexts, "
                "projects, timeEstimate."
            ),
        ] = None,
    ) -> TaskWrite:
        """Change a task note: schedule it for a day, unschedule it, finish it, and so on.

        Plan a day by setting scheduled on the tasks chosen; clear scheduled to drop one.
        A task from Jira or Bitbucket has its title, status, priority and due date owned
        there: change those in that system (jira_transition_issue ...) and run
        obsidian_sync; here only scheduled, contexts, projects and timeEstimate change.

        Args:
            task: The task.
            status: New status (done finishes it).
            priority: New priority.
            due: New due day.
            scheduled: New day planned for.
            contexts: New contexts, replacing the old.
            projects: New projects, replacing the old.
            time_estimate: New estimate in minutes.
            clear: Fields to remove.
        """
        async with writing:
            return await asyncio.to_thread(
                lambda: editor.update_task(
                    task=task,
                    status=status,
                    priority=priority,
                    due=due,
                    scheduled=scheduled,
                    contexts=contexts,
                    projects=projects,
                    time_estimate=time_estimate,
                    clear=clear,
                )
            )

    @server.tool(annotations=LOCAL_IDEMPOTENT_WRITE, meta=toolset("planning"))
    @anticipated
    async def obsidian_write_section(
        section: Annotated[
            str, Field(description="Section name: lower-case letters, digits, hyphens (plan).")
        ],
        markdown: Annotated[str, Field(description="What the section should hold.")],
        day: Annotated[
            str | None,
            Field(description="A daily note's day: YYYY-MM-DD, today, yesterday, tomorrow."),
        ] = None,
        note: Annotated[
            str | None, Field(description="Another note's path or [[link]], such as a meeting.")
        ] = None,
    ) -> SectionWrite:
        """Write a named section of a daily note or another note, replacing it if present.

        The section sits between hidden markers, so writing it again (a revised plan, say)
        replaces it instead of adding a copy; the rest of the note is untouched. A daily
        note that does not exist yet is made from Obsidian's daily-note template. Give
        either day or note.

        Args:
            section: The section's name.
            markdown: Its new text.
            day: The daily note's day.
            note: The note.
        """
        async with writing:
            return await asyncio.to_thread(
                lambda: editor.write_section(section=section, markdown=markdown, note=note, day=day)
            )

    @server.tool(annotations=LOCAL_IDEMPOTENT_WRITE, meta=toolset("planning"))
    @anticipated
    async def obsidian_sync(
        days_ahead: Annotated[
            int | None,
            Field(
                ge=0,
                le=14,
                description="Days after today whose meetings to bring in; default from config.",
            ),
        ] = None,
    ) -> SyncReport:
        """Bring the user's open work and today's meetings into the vault, without duplicates.

        Open Jira issues and pull requests awaiting the user's review become task notes;
        meetings become meeting notes. Each note is found by its key, so running this
        again updates the notes it made; it only changes the fields that belong to the
        source (never scheduled, contexts or the note's text) and never deletes: finished
        work is marked done. Run it
        before planning the day. Sources that cannot be read are listed and skipped.

        Args:
            days_ahead: Days after today to include meetings for.
        """
        days = vault.settings.sync_days_ahead if days_ahead is None else days_ahead
        fetched, covered = await syncer.fetch(context, days)
        async with writing:
            return await asyncio.to_thread(syncer.apply, fetched, covered)
