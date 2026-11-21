"""Shapes returned by the Obsidian tools."""

from pydantic import BaseModel, Field, JsonValue


class ObsidianStatus(BaseModel):
    """The vault and its settings, reported by obsidian_status."""

    vault: str = Field(description="The vault's folder.")
    is_vault: bool = Field(description="True if the folder holds Obsidian's .obsidian settings.")
    tasks_folder: str = Field(description="Folder new task notes go in, relative to the vault.")
    meetings_folder: str = Field(description="Folder meeting notes go in, relative to the vault.")
    meeting_notes: str = Field(
        description="Where meeting notes go, such as Meetings/YYYY/MM/YYYY-MM-DD <title>."
    )
    daily_notes: str = Field(
        description="Where daily notes go, such as Journal/YYYY/MM/YYYY-MM-DD."
    )
    daily_notes_from: str = Field(description="Where the daily-note layout came from.")
    today: str = Field(description="Today's date in the vault's time zone, YYYY-MM-DD.")
    time_zone: str = Field(description="The time zone that decides today, and where it came from.")
    task_tag: str = Field(description="Tag that marks a note as a task.")
    timeblock_minutes: int = Field(
        description="Block planned for a task with neither an estimate nor a due date."
    )
    max_timeblock_minutes: int = Field(description="Longest block planned for one task.")
    break_minutes: int = Field(description="Break after a block of the default length or less.")
    long_break_minutes: int = Field(description="Break after a longer block.")
    notes: int = Field(description="Markdown notes in the vault.")
    tasks: int = Field(description="Task notes in the vault, finished or not.")
    open_tasks: int = Field(description="Task notes not yet finished.")
    unreadable: list[str] = Field(
        description="Notes whose frontmatter could not be read, with the reason."
    )
    dry_run: bool = Field(description="True if writes to the vault are blocked.")


class TaskNote(BaseModel):
    """One task note, with the TaskNotes fields and its source keys."""

    path: str = Field(description="Path relative to the vault, used by obsidian_get_note.")
    title: str = Field(description="The task's title, or the file name when it has none.")
    status: str | None = Field(default=None, description="Status, such as open or done.")
    done: bool = Field(description="True if the status counts as finished.")
    priority: str | None = Field(default=None, description="Priority, such as normal or high.")
    due: str | None = Field(default=None, description="Due date, ISO 8601.")
    scheduled: str | None = Field(default=None, description="Day it is planned for, ISO 8601.")
    contexts: list[str] = Field(default_factory=list, description="Contexts, such as @office.")
    projects: list[str] = Field(default_factory=list, description="Projects, often [[links]].")
    tags: list[str] = Field(default_factory=list, description="Tags, without #.")
    keys: dict[str, str] = Field(
        default_factory=dict,
        description="Stable keys tying the task to its source, such as jira: ABC-123.",
    )
    estimate_minutes: int | None = Field(
        default=None,
        description="Time still needed: the note's timeEstimate, else its source's estimate.",
    )
    block_minutes: int = Field(
        description="Length of one time block to plan for it today: the estimate up to the "
        "longest block, else the default block."
    )
    break_minutes: int = Field(
        description="Break to leave after its block before the next one: the long break "
        "after a block longer than the default one."
    )


class TaskList(BaseModel):
    """Task notes matching a filter."""

    matched: int = Field(description="Task notes matching the filter.")
    returned: int = Field(description="Task notes in this result.")
    more_available: bool = Field(description="True if more matched than were returned.")
    tasks: list[TaskNote] = Field(description="The tasks, earliest scheduled or due first.")


class SimilarTask(BaseModel):
    """A task whose title is close to the one looked for."""

    score: float = Field(description="Title similarity from 0 to 1; 1 is the same title.")
    task: TaskNote = Field(description="The task.")


class FoundTasks(BaseModel):
    """Existing tasks that may be the one looked for."""

    exact: list[TaskNote] = Field(description="Tasks with the key looked for.")
    similar: list[SimilarTask] = Field(description="Tasks with a similar title, closest first.")


class Note(BaseModel):
    """One note: its frontmatter and its text."""

    path: str = Field(description="Path relative to the vault.")
    frontmatter: dict[str, JsonValue] = Field(description="The note's YAML frontmatter.")
    frontmatter_error: str | None = Field(
        default=None, description="Why the frontmatter could not be read, if it could not."
    )
    body: str = Field(description="The note's text after the frontmatter.")
    body_chars: int = Field(description="Length of the full body in characters.")
    truncated: bool = Field(description="True if the body was cut at max_chars.")


class DailyNote(BaseModel):
    """One day's daily note, whether or not it exists yet."""

    day: str = Field(description="The day, YYYY-MM-DD.")
    path: str = Field(description="Where the day's note is, or would be, relative to the vault.")
    exists: bool = Field(description="True if the note exists.")
    layout_from: str = Field(description="Where the daily-note layout came from.")
    note: Note | None = Field(default=None, description="The note, when it exists.")


class TaskWrite(BaseModel):
    """What obsidian_create_task or obsidian_update_task did, or would do."""

    action: str = Field(description="create_task or update_task.")
    path: str | None = Field(default=None, description="The task note's path in the vault.")
    created: bool = Field(default=False, description="True if a new note was (or would be) made.")
    written: bool = Field(description="True if the vault was changed.")
    dry_run: bool = Field(description="True if dry-run blocked the change.")
    message: str = Field(description="Plain-language summary of the outcome.")
    properties: dict[str, JsonValue] = Field(
        default_factory=dict, description="Properties set (null for one removed)."
    )
    existing: TaskNote | None = Field(
        default=None, description="The task that already has this key, when nothing was made."
    )
    similar: list[SimilarTask] = Field(
        default_factory=list,
        description="Open tasks with a close title, when the new one was held back for them.",
    )


class SectionWrite(BaseModel):
    """What obsidian_write_section did, or would do."""

    path: str = Field(description="The note's path in the vault.")
    section: str = Field(description="The section's name.")
    created_note: bool = Field(
        description="True if the note did not exist and was (or would be) made."
    )
    replaced: bool = Field(description="True if the section existed and its text was replaced.")
    written: bool = Field(description="True if the vault was changed.")
    dry_run: bool = Field(description="True if dry-run blocked the change.")
    message: str = Field(description="Plain-language summary of the outcome.")
