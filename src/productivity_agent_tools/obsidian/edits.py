"""Changes the agent may make to the vault: new task notes, task fields, marked sections.

Each change keeps the rules that stop the vault filling with duplicates:

- A new task with a source key is refused when a task with that key exists, and a
  new task is held back when an open task has a close title, until the user says
  it is different (``allow_similar``). A task made here gets a ``uid`` key.
- A task tied to another system (``jira``, ``bitbucket`` ...) has its title,
  status, priority and due date owned by that system: they are changed there and
  synced, never here.
- A section is replaced between its markers, never appended twice.

Every change is described but not made while the vault's dry-run is on, and is
written through ``notes.write``, which leaves a note alone if it changed since it
was read.
"""

import datetime
import uuid
from collections.abc import Iterable
from pathlib import Path

from pydantic import JsonValue

from productivity_agent_tools.errors import AgentToolsError, NotFoundError
from productivity_agent_tools.obsidian import notes
from productivity_agent_tools.obsidian.frontmatter import to_json
from productivity_agent_tools.obsidian.layout import daily_layout, parse_day, zone
from productivity_agent_tools.obsidian.models import SectionWrite, TaskNote, TaskWrite
from productivity_agent_tools.obsidian.notes import Property, Snapshot
from productivity_agent_tools.obsidian.paths import keyed_name, safe_name
from productivity_agent_tools.obsidian.templates import daily_template, fill
from productivity_agent_tools.obsidian.vault import KEY_FIELDS, Vault, similar_tasks

DRY_RUN: str = (
    "DRY RUN: nothing was written to the vault; this is what would be written. To allow "
    "writes to the vault only, the user sets dry_run = false in the [obsidian] table of "
    "the config and restarts the agent."
)
SIMILAR_SCORE: float = 0.8
"""Title similarity at which a new task is held back for an open one."""

SOURCE_OWNED: tuple[str, ...] = ("title", "status", "priority", "due")
"""Fields of a task from another system that only that system changes."""

UPDATABLE: tuple[str, ...] = (
    "status",
    "priority",
    "due",
    "scheduled",
    "contexts",
    "projects",
    "timeEstimate",
)


def _when(value: str | None, name: str) -> str | None:
    """Check a day, or a day and time, given for a task.

    Args:
        value: ``YYYY-MM-DD`` or ``YYYY-MM-DDTHH:MM``, or None.
        name: The argument's name, for the error message.

    Returns:
        The value as given, trimmed, or None.

    Raises:
        AgentToolsError: If it is neither.
    """
    if value is None:
        return None
    text = value.strip()
    try:
        (datetime.date if len(text) == 10 else datetime.datetime).fromisoformat(text)
    except ValueError as exc:
        raise AgentToolsError(
            f"{name} must be YYYY-MM-DD or YYYY-MM-DDTHH:MM, got {value!r}."
        ) from exc
    return text


def _texts(values: Iterable[str] | None) -> list[str] | None:
    """Trim a list of texts, dropping empty ones.

    Args:
        values: The texts, or None.

    Returns:
        The trimmed texts, or None when none were given.
    """
    if values is None:
        return None
    return [value.strip() for value in values if value.strip()]


def _shown(changes: dict[str, Property]) -> dict[str, JsonValue]:
    """Show property changes as JSON values.

    Args:
        changes: The changes.

    Returns:
        The same, with dates as ISO text.
    """
    shown = to_json(dict(changes))
    assert isinstance(shown, dict)
    return shown


class VaultEditor:
    """Makes the agent's changes to the vault. Calls run in a worker thread, one at a time.

    Attributes:
        vault: The vault, for its settings and its reads.
    """

    def __init__(self, vault: Vault) -> None:
        """Create the editor.

        Args:
            vault: The vault it changes.
        """
        self.vault = vault

    @property
    def dry_run(self) -> bool:
        """True while writes to the vault are blocked."""
        return self.vault.dry_run

    def _now(self) -> datetime.datetime:
        """The current time in the vault's time zone.

        Returns:
            An aware time, to the second.
        """
        return datetime.datetime.now(zone(self.vault.settings)).replace(microsecond=0)

    def _new_path(self, title: str, key: str | None = None) -> Path:
        """Choose a file for a new task note, numbered if the name is taken.

        Args:
            title: The task's title.
            key: Its source key, when it comes from another system: the note is then
                named ``<key> <short title>``, as the sync names it.

        Returns:
            A path that does not exist yet.
        """
        settings = self.vault.settings
        name = keyed_name(key, title, settings.task_title_length) if key else safe_name(title)
        folder = self.vault.checked_root() / settings.tasks_folder
        return notes.free_path(folder / f"{name}.md")

    def _relative(self, path: Path) -> str:
        """A path relative to the vault, with forward slashes.

        Args:
            path: A path inside the vault.

        Returns:
            The relative path.
        """
        return path.relative_to(self.vault.checked_root()).as_posix()

    def _write(self, path: Path, text: str, snapshot: Snapshot) -> bool:
        """Write a note unless dry-run is on.

        Args:
            path: The note's file.
            text: Its new text.
            snapshot: What was read.

        Returns:
            True if it was written.
        """
        if self.dry_run:
            return False
        notes.write(path, text, snapshot)
        return True

    def create_task(
        self,
        *,
        title: str,
        key_field: str | None = None,
        key: str | None = None,
        status: str = "open",
        priority: str = "normal",
        due: str | None = None,
        scheduled: str | None = None,
        contexts: list[str] | None = None,
        projects: list[str] | None = None,
        time_estimate: int | None = None,
        body: str | None = None,
        allow_similar: bool = False,
    ) -> TaskWrite:
        """Make a task note, unless the task already exists.

        Args:
            title: The task's title.
            key_field: The source key's property (``jira``, ``bitbucket`` ...), if the task
                comes from another system.
            key: The key's value, such as ``ABC-123``.
            status: TaskNotes status.
            priority: TaskNotes priority.
            due: Due day, ``YYYY-MM-DD`` or with a time.
            scheduled: Day planned for, ``YYYY-MM-DD`` or with a time.
            contexts: Contexts, such as ``@office``.
            projects: Projects, often ``[[links]]``.
            time_estimate: Estimate in minutes.
            body: Text for the note under its properties.
            allow_similar: True once the user has said the task differs from the similar
                open tasks reported last time.

        Returns:
            What was done: the new note, the task that already has the key, or the
            similar open tasks the new one was held back for.

        Raises:
            AgentToolsError: If an argument is malformed or the vault does not exist.
        """
        name = " ".join(title.split())
        field = (key_field or "").strip()
        value = (key or "").strip()
        if not name:
            raise AgentToolsError("A task needs a title.")
        if bool(field) != bool(value):
            raise AgentToolsError("Give key_field and key together, such as jira and ABC-123.")
        if field and field not in KEY_FIELDS:
            raise AgentToolsError(f"key_field must be one of {', '.join(KEY_FIELDS)}.")
        due_at = _when(due, "due")
        scheduled_at = _when(scheduled, "scheduled")
        tasks = self.vault.scan_tasks()
        if field and (same := [task for task in tasks if task.keys.get(field) == value]):
            return TaskWrite(
                action="create_task",
                path=same[0].path,
                written=False,
                dry_run=self.dry_run,
                existing=same[0],
                message=f"Not created: {same[0].path} already has {field}: {value}. "
                "Update that task instead.",
            )
        open_tasks = [task for task in tasks if not task.done]
        similar = similar_tasks(name, open_tasks, SIMILAR_SCORE)
        if similar and not allow_similar:
            return TaskWrite(
                action="create_task",
                written=False,
                dry_run=self.dry_run,
                similar=similar[:5],
                message="Not created: open tasks with a close title exist (listed). Ask the "
                "user whether this is one of them; if it is a different task, call again "
                "with allow_similar=true.",
            )
        source_key = value or None
        if not field:
            field, value = "uid", f"{self._now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:4]}"
        stamp = self._now().isoformat()
        properties: dict[str, Property] = {
            "title": name,
            "status": status.strip() or "open",
            "priority": priority.strip() or "normal",
            "due": due_at,
            "scheduled": scheduled_at,
            "contexts": _texts(contexts) or None,
            "projects": _texts(projects) or None,
            "timeEstimate": time_estimate,
            "tags": [self.vault.settings.task_tag],
            "dateCreated": stamp,
            "dateModified": stamp,
            field: value,
        }
        properties = {k: v for k, v in properties.items() if v is not None}
        text = notes.set_properties("", properties)
        if body and body.strip():
            text += f"\n{body.strip()}\n"
        path = self._new_path(name, source_key)
        written = self._write(path, text, Snapshot("", None))
        relative = self._relative(path)
        return TaskWrite(
            action="create_task",
            path=relative,
            created=True,
            written=written,
            dry_run=not written,
            properties=_shown(properties),
            message=f"Created {relative}." if written else DRY_RUN,
        )

    def _find(self, task: str) -> TaskNote:
        """Find one task by its path, its ``[[link]]``, or ``key_field:key``.

        Args:
            task: How the task is named.

        Returns:
            The task.

        Raises:
            NotFoundError: If no task matches.
            AgentToolsError: If several do, or the note is not a task.
        """
        tasks = self.vault.scan_tasks()
        field, separator, value = task.partition(":")
        if separator and field.strip() in KEY_FIELDS:
            found = [t for t in tasks if t.keys.get(field.strip()) == value.strip()]
            if not found:
                raise NotFoundError(f"No task has {field.strip()}: {value.strip()}.")
            if len(found) > 1:
                paths = ", ".join(t.path for t in found)
                raise AgentToolsError(f"Several tasks have {task}: {paths}. Name one by path.")
            return found[0]
        path = self._relative(self.vault.resolve_note(task))
        for candidate in tasks:
            if candidate.path == path:
                return candidate
        raise AgentToolsError(
            f"{path} is not a task note (it has no #{self.vault.settings.task_tag} tag)."
        )

    def update_task(
        self,
        *,
        task: str,
        status: str | None = None,
        priority: str | None = None,
        due: str | None = None,
        scheduled: str | None = None,
        contexts: list[str] | None = None,
        projects: list[str] | None = None,
        time_estimate: int | None = None,
        clear: list[str] | None = None,
    ) -> TaskWrite:
        """Change a task note's fields.

        Args:
            task: The task: its path, its ``[[link]]``, or ``key_field:key``.
            status: New status.
            priority: New priority.
            due: New due day.
            scheduled: New day planned for.
            contexts: New contexts, replacing the old.
            projects: New projects, replacing the old.
            time_estimate: New estimate in minutes.
            clear: Fields to remove, from status, priority, due, scheduled, contexts,
                projects and timeEstimate.

        Returns:
            What was changed.

        Raises:
            AgentToolsError: If nothing would change, a field is not one this changes,
                the task comes from another system and the field belongs to it, or the
                note changed since it was read.
            NotFoundError: If the task cannot be found.
        """
        found = self._find(task)
        changes: dict[str, Property] = {
            "status": status.strip() if status else None,
            "priority": priority.strip() if priority else None,
            "due": _when(due, "due"),
            "scheduled": _when(scheduled, "scheduled"),
            "contexts": _texts(contexts),
            "projects": _texts(projects),
            "timeEstimate": time_estimate,
        }
        changes = {k: v for k, v in changes.items() if v is not None}
        for name in clear or []:
            if name not in UPDATABLE:
                raise AgentToolsError(f"clear takes {', '.join(UPDATABLE)}; not {name}.")
            changes[name] = None
        if not changes:
            raise AgentToolsError("Nothing to change: give a field to set or clear.")
        sources = [name for name in found.keys if name != "uid"]
        owned = [name for name in changes if name in SOURCE_OWNED]
        if sources and owned:
            raise AgentToolsError(
                f"{found.path} comes from {sources[0]} ({found.keys[sources[0]]}), which owns "
                f"its {', '.join(owned)}. Change it there and sync; here only scheduled, "
                "contexts, projects and timeEstimate change."
            )
        if "status" in changes:
            finished = changes["status"] in self.vault.settings.done_statuses
            changes["completedDate"] = self._now().date() if finished else None
        changes["dateModified"] = self._now().isoformat()
        path = self.vault.checked_root() / found.path
        snapshot = notes.read(path)
        text = notes.set_properties(snapshot.text, changes)
        written = self._write(path, text, snapshot)
        return TaskWrite(
            action="update_task",
            path=found.path,
            written=written,
            dry_run=not written,
            properties=_shown(changes),
            message=f"Updated {found.path}." if written else DRY_RUN,
        )

    def write_section(
        self, *, section: str, markdown: str, note: str | None = None, day: str | None = None
    ) -> SectionWrite:
        """Put Markdown in a named section of a note or of a day's daily note.

        Args:
            section: The section's name, such as ``plan``.
            markdown: What the section should hold.
            note: An existing note: its path or ``[[link]]``.
            day: A daily note's day (``YYYY-MM-DD``, ``today`` ...); it is made from
                Obsidian's daily-note template if it does not exist.

        Returns:
            What was written.

        Raises:
            AgentToolsError: If both or neither of note and day are given, the section
                name or markers are bad, or the note changed since it was read.
            NotFoundError: If ``note`` does not exist.
        """
        name = notes.check_section_name(section)
        if (note is None) == (day is None):
            raise AgentToolsError("Name either a note or a day, not both.")
        root = self.vault.checked_root()
        created = False
        if day is not None:
            when = parse_day(day, self.vault.settings)
            path = root / daily_layout(self.vault.settings).path(when)
            snapshot = notes.read(path)
            start = snapshot.text
            if snapshot.stamp is None:
                created = True
                template = daily_template(root)
                moment = datetime.datetime.combine(when, self._now().time())
                start = fill(template, root, path.stem, moment) if template else ""
        else:
            path = self.vault.resolve_note(note or "")
            snapshot = notes.read(path)
            start = snapshot.text
        text, replaced = notes.replace_section(start, name, markdown)
        written = self._write(path, text, snapshot)
        relative = self._relative(path)
        done = f"{'Replaced' if replaced else 'Added'} the {name} section of {relative}."
        return SectionWrite(
            path=relative,
            section=name,
            created_note=created,
            replaced=replaced,
            written=written,
            dry_run=not written,
            message=done if written else DRY_RUN,
        )
