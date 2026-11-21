"""Reads of an Obsidian vault on disk: its notes, and the task notes among them.

The vault is a folder of Markdown files; Obsidian itself need not be running.
Task notes are the ones TaskNotes makes: a note whose ``tags`` property holds the
task tag (``task`` by default), with its fields (``status``, ``due``,
``scheduled`` ...) as properties. Notes made by this package also carry a stable
key tying them to their source (``jira: ABC-123``), which is how the same task is
recognized from one day to the next whatever its title or file name.

Folders whose names start with a dot (``.obsidian``, ``.trash``, ``.git``) are
never read, and neither are the template folders the Templates and Templater
plugins use: a template is not a note, and one tagged ``#task`` is not a task.
"""

import asyncio
import datetime
import difflib
import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import JsonValue

from productivity_agent_tools.errors import AgentToolsError, NotFoundError
from productivity_agent_tools.obsidian.frontmatter import ParsedNote, parse, parse_properties, split
from productivity_agent_tools.obsidian.layout import (
    daily_layout,
    describe_zone,
    meeting_pattern,
    parse_day,
    today,
)
from productivity_agent_tools.obsidian.models import (
    DailyNote,
    FoundTasks,
    Note,
    ObsidianStatus,
    SimilarTask,
    TaskList,
    TaskNote,
)
from productivity_agent_tools.obsidian.settings import ObsidianSettings

KEY_FIELDS: tuple[str, ...] = ("jira", "bitbucket", "servicenow", "ical_uid", "series_id", "uid")
"""Properties that tie a note to its source. A note is found by these, never by title."""

type TaskState = Literal["open", "done", "all"]

_NOT_WORD = re.compile(r"[\W_]+")


@dataclass(slots=True)
class _Cached:
    """What is kept of one note between calls, until its file changes.

    Attributes:
        stamp: The file's modification time (ns) and size when it was read.
        block: Its frontmatter block, or None when it has none.
        problem: Why the file could not be read, or None.
        properties: Its parsed properties, once something needed them.
    """

    stamp: tuple[int, int]
    block: str | None
    problem: str | None = None
    properties: ParsedNote | None = None


@dataclass(frozen=True, slots=True)
class _Survey:
    """The counts ``obsidian_status`` reports.

    Attributes:
        notes: Markdown notes in the vault.
        tasks: The task notes.
        unreadable: Notes that could not be read or whose properties are broken, each
            with the reason.
    """

    notes: int
    tasks: tuple[TaskNote, ...]
    unreadable: tuple[str, ...]


def _markdown_files(root: Path) -> Iterator[Path]:
    """List the vault's Markdown files, skipping dot folders and not following links.

    Args:
        root: The vault's folder.

    Yields:
        Each ``.md`` file, in path order.
    """
    for folder, subfolders, files in os.walk(root):
        subfolders[:] = sorted(name for name in subfolders if not name.startswith("."))
        for name in sorted(files):
            if name.endswith(".md") and not name.startswith("."):
                yield Path(folder) / name


TEMPLATE_SETTINGS: tuple[tuple[str, str], ...] = (
    (".obsidian/templates.json", "folder"),
    (".obsidian/plugins/templater-obsidian/data.json", "templates_folder"),
)
"""Where the Templates and Templater plugins keep their template folder setting."""


def template_folders(root: Path) -> tuple[str, ...]:
    """Find the folders the Templates and Templater plugins keep templates in.

    Args:
        root: The vault's folder.

    Returns:
        Each folder relative to the vault, without slashes at either end.
    """
    folders: list[str] = []
    for name, key in TEMPLATE_SETTINGS:
        try:
            settings = json.loads((root / name).read_text(encoding="utf-8"))
        except OSError, UnicodeDecodeError, json.JSONDecodeError:
            continue
        value = settings.get(key) if isinstance(settings, dict) else None
        folder = value.replace("\\", "/").strip().strip("/") if isinstance(value, str) else ""
        if folder and folder not in folders:
            folders.append(folder)
    return tuple(folders)


def _text_list(value: JsonValue) -> list[str]:
    """Read a property that may be one value or a list of them.

    Args:
        value: The property's value.

    Returns:
        Its values as text, leaving out empty ones.
    """
    items = value if isinstance(value, list) else [value]
    return [str(item).strip() for item in items if item is not None and str(item).strip()]


def _tags(value: JsonValue) -> list[str]:
    """Read the ``tags`` property: a list, or text separated by commas or spaces.

    Args:
        value: The property's value.

    Returns:
        The tags without a leading ``#``.
    """
    items = re.split(r"[,\s]+", value) if isinstance(value, str) else _text_list(value)
    return [tag.lstrip("#") for tag in items if tag.lstrip("#")]


def _scalar(value: JsonValue) -> str | None:
    """Read a property that should hold one value, as text.

    Args:
        value: The property's value.

    Returns:
        The text, or None when it is empty, a list or a mapping.
    """
    if value is None or isinstance(value, list | dict):
        return None
    return str(value).strip() or None


def _minutes(value: JsonValue) -> int | None:
    """Read a whole number of minutes, as TaskNotes writes ``timeEstimate``.

    Args:
        value: The property's value.

    Returns:
        The minutes, or None when it is missing, not a whole number, or not positive.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = _scalar(value)
    return int(text) if text and text.isdigit() and int(text) > 0 else None


def _normalized(title: str) -> str:
    """Reduce a title to lower-case words, for comparing titles.

    Args:
        title: The title.

    Returns:
        Its words in lower case, separated by single spaces.
    """
    return _NOT_WORD.sub(" ", title.lower()).strip()


def similar_tasks(title: str, tasks: list[TaskNote], min_score: float) -> list[SimilarTask]:
    """Score tasks by how close their titles are to a title, closest first.

    Args:
        title: The title to compare.
        tasks: The tasks to score.
        min_score: Least similarity (0 to 1) to keep.

    Returns:
        The tasks scoring at least ``min_score``.
    """
    wanted = _normalized(title)
    found = [
        SimilarTask(score=round(score, 3), task=task)
        for task in tasks
        if (score := difflib.SequenceMatcher(None, wanted, _normalized(task.title)).ratio())
        >= min_score
    ]
    return sorted(found, key=lambda item: (-item.score, item.task.path))


def _day(value: str | None, name: str) -> str | None:
    """Check a date argument.

    Args:
        value: A date as ``YYYY-MM-DD``, or None.
        name: The argument's name, for the error message.

    Returns:
        The date as given, or None.

    Raises:
        AgentToolsError: If it is not a date.
    """
    if value is None:
        return None
    try:
        return datetime.date.fromisoformat(value.strip()).isoformat()
    except ValueError as exc:
        raise AgentToolsError(f"{name} must be a date written YYYY-MM-DD, got {value!r}.") from exc


def _sort_key(task: TaskNote) -> tuple[str, str]:
    """Order tasks by their earliest scheduled or due day, then by title.

    Args:
        task: The task.

    Returns:
        The sort key; tasks with neither day come last.
    """
    days = [day[:10] for day in (task.scheduled, task.due) if day]
    return (min(days) if days else "~", task.title.lower())


class Vault:
    """The Obsidian vault named in the config, read from disk.

    Every call looks at the files again, so notes changed in Obsidian are seen at once.
    A note's properties are parsed once and kept until its file's modification time or
    size changes, and a note whose properties cannot name the task tag is not parsed
    for the task tools at all: parsing YAML, not reading files, is what takes the time.

    Attributes:
        settings: The ``[obsidian]`` settings.
        dry_run: When True, writes to the vault are blocked.
    """

    def __init__(self, settings: ObsidianSettings, dry_run: bool) -> None:
        """Create the client. Nothing is read until a method is called.

        Args:
            settings: The ``[obsidian]`` settings.
            dry_run: Whether writes to the vault are blocked.
        """
        self.settings = settings
        self.dry_run = dry_run
        self._cache: dict[str, _Cached] = {}
        self._tag = re.compile(rf"(?<![\w/-]){re.escape(settings.task_tag)}(?![\w/-])")

    @property
    def root(self) -> Path:
        """The vault's folder."""
        return self.settings.vault

    def checked_root(self) -> Path:
        """Make sure the vault's folder exists.

        Returns:
            The folder, resolved.

        Raises:
            AgentToolsError: If it does not exist or is not a folder.
        """
        if not self.root.is_dir():
            raise AgentToolsError(
                f"The Obsidian vault {self.root} does not exist or is not a folder. "
                "Check 'vault' in the [obsidian] table of the config."
            )
        return self.root.resolve()

    def _scan(self) -> dict[str, _Cached]:
        """Look at every note in the vault, reading only those changed since the last call.

        Returns:
            Each note by its path relative to the vault, in path order.

        Raises:
            AgentToolsError: If the vault's folder does not exist.
        """
        root = self.checked_root()
        before = self._cache
        templates = tuple(f"{folder}/" for folder in template_folders(root))
        notes: dict[str, _Cached] = {}
        for file in _markdown_files(root):
            path = file.relative_to(root).as_posix()
            if path.startswith(templates):
                continue
            try:
                info = file.stat()
                stamp = (info.st_mtime_ns, info.st_size)
                cached = before.get(path)
                if cached is None or cached.stamp != stamp:
                    cached = _Cached(stamp, split(file.read_text(encoding="utf-8"))[0])
            except (OSError, UnicodeDecodeError) as exc:
                cached = _Cached((-1, -1), None, f"{exc.__class__.__name__}: could not be read")
            notes[path] = cached
        self._cache = notes
        return notes

    @staticmethod
    def _properties(note: _Cached) -> ParsedNote:
        """Parse a note's properties the first time they are needed.

        Args:
            note: The note.

        Returns:
            Its properties, and why they could not be read if they could not.
        """
        if note.properties is None:
            note.properties = ParsedNote() if note.block is None else parse_properties(note.block)
        return note.properties

    def _task(self, path: str, note: _Cached) -> TaskNote | None:
        """Read one note as a task, if it is one.

        Args:
            path: The note's path relative to the vault.
            note: The note.

        Returns:
            The task, or None when the note does not carry the task tag.
        """
        if note.block is None or not self._tag.search(note.block):
            return None
        properties = self._properties(note).frontmatter
        tags = _tags(properties.get("tags"))
        if self.settings.task_tag not in tags:
            return None
        status = _scalar(properties.get("status"))
        due = _scalar(properties.get("due"))
        estimate = _minutes(properties.get("timeEstimate"))
        if estimate is None:
            estimate = _minutes(properties.get("source_estimate"))
        return TaskNote(
            path=path,
            title=_scalar(properties.get("title")) or Path(path).stem,
            status=status,
            done=status in self.settings.done_statuses,
            priority=_scalar(properties.get("priority")),
            due=due,
            scheduled=_scalar(properties.get("scheduled")),
            contexts=_text_list(properties.get("contexts")),
            projects=_text_list(properties.get("projects")),
            tags=tags,
            keys={
                name: value
                for name in KEY_FIELDS
                if (value := _scalar(properties.get(name))) is not None
            },
            estimate_minutes=estimate,
            block_minutes=(block := self._block(estimate)),
            break_minutes=(
                self.settings.long_break_minutes
                if block > self.settings.timeblock_minutes
                else self.settings.break_minutes
            ),
        )

    def _block(self, estimate: int | None) -> int:
        """Choose the length of one time block for a task.

        Args:
            estimate: Minutes still needed, if known (always more than 0).

        Returns:
            The estimate up to the longest block, else the default block. A due date
            does not change the length.
        """
        if estimate is not None:
            return min(estimate, self.settings.max_timeblock_minutes)
        return self.settings.timeblock_minutes

    def scan_tasks(self) -> list[TaskNote]:
        """Find every task note.

        Returns:
            The task notes, in path order.

        Raises:
            AgentToolsError: If the vault's folder does not exist.
        """
        return [task for path, note in self._scan().items() if (task := self._task(path, note))]

    def notes_by_key(self, field: str) -> dict[str, list[str]]:
        """Find every note carrying a key property, task or not.

        Args:
            field: The key's property, such as ``jira`` or ``ical_uid``.

        Returns:
            The paths of the notes holding each key value.

        Raises:
            AgentToolsError: If the vault's folder does not exist.
        """
        pattern = re.compile(rf"(?m)^{re.escape(field)}\s*:")
        found: dict[str, list[str]] = {}
        for path, note in self._scan().items():
            if note.block is None or not pattern.search(note.block):
                continue
            value = _scalar(self._properties(note).frontmatter.get(field))
            if value is not None:
                found.setdefault(value, []).append(path)
        return found

    def _survey(self) -> _Survey:
        """Count the notes and tasks, and find every note whose properties are unreadable.

        Returns:
            The counts.

        Raises:
            AgentToolsError: If the vault's folder does not exist.
        """
        notes = self._scan()
        unreadable = [
            f"{path}: {problem}"
            for path, note in notes.items()
            if (problem := note.problem or self._properties(note).error)
        ]
        tasks = tuple(task for path, note in notes.items() if (task := self._task(path, note)))
        readable = sum(note.problem is None for note in notes.values())
        return _Survey(notes=readable, tasks=tasks, unreadable=tuple(unreadable))

    async def status(self) -> ObsidianStatus:
        """Describe the vault and count its notes and tasks.

        Returns:
            The status.

        Raises:
            AgentToolsError: If the vault's folder does not exist.
        """
        survey = await asyncio.to_thread(self._survey)
        layout = await asyncio.to_thread(daily_layout, self.settings)
        return ObsidianStatus(
            vault=str(self.root),
            is_vault=(self.root / ".obsidian").is_dir(),
            tasks_folder=self.settings.tasks_folder,
            meetings_folder=self.settings.meetings_folder,
            meeting_notes=meeting_pattern(self.settings),
            daily_notes=layout.pattern,
            daily_notes_from=layout.source,
            today=today(self.settings).isoformat(),
            time_zone=describe_zone(self.settings),
            task_tag=self.settings.task_tag,
            timeblock_minutes=self.settings.timeblock_minutes,
            max_timeblock_minutes=self.settings.max_timeblock_minutes,
            break_minutes=self.settings.break_minutes,
            long_break_minutes=self.settings.long_break_minutes,
            notes=survey.notes,
            tasks=len(survey.tasks),
            open_tasks=sum(not task.done for task in survey.tasks),
            unreadable=list(survey.unreadable),
            dry_run=self.dry_run,
        )

    async def list_tasks(
        self,
        *,
        state: TaskState = "open",
        scheduled_on: str | None = None,
        due_by: str | None = None,
        project: str | None = None,
        context: str | None = None,
        has_key: str | None = None,
        max_results: int | None = None,
    ) -> TaskList:
        """List task notes matching every filter given.

        Args:
            state: ``open`` for unfinished tasks, ``done`` for finished ones, or ``all``.
            scheduled_on: Only tasks scheduled for this day (``YYYY-MM-DD``).
            due_by: Only tasks due on or before this day (``YYYY-MM-DD``).
            project: Only tasks with a project containing this text (any case).
            context: Only tasks with a context containing this text (any case).
            has_key: Only tasks carrying this source key, such as ``jira``.
            max_results: Most tasks to return; defaults to the configured number.

        Returns:
            The matching tasks, earliest scheduled or due first.

        Raises:
            AgentToolsError: If a date is malformed or the vault does not exist.
        """
        day = _day(scheduled_on, "scheduled_on")
        deadline = _day(due_by, "due_by")
        limit = max(1, min(max_results or self.settings.default_max_results, 500))
        project_text = (project or "").strip().lower()
        context_text = (context or "").strip().lower()

        def wanted(task: TaskNote) -> bool:
            checks = (
                state == "all" or task.done == (state == "done"),
                day is None or (task.scheduled or "")[:10] == day,
                deadline is None or bool(task.due and task.due[:10] <= deadline),
                not project_text or any(project_text in p.lower() for p in task.projects),
                not context_text or any(context_text in c.lower() for c in task.contexts),
                has_key is None or has_key.strip() in task.keys,
            )
            return all(checks)

        tasks = await asyncio.to_thread(self.scan_tasks)
        matched = sorted((t for t in tasks if wanted(t)), key=_sort_key)
        return TaskList(
            matched=len(matched),
            returned=min(len(matched), limit),
            more_available=len(matched) > limit,
            tasks=matched[:limit],
        )

    async def find_tasks(
        self,
        *,
        key_field: str | None = None,
        key: str | None = None,
        title: str | None = None,
        min_score: float = 0.6,
        max_results: int = 5,
    ) -> FoundTasks:
        """Find tasks that may already be the one described, finished or not.

        Args:
            key_field: The source key's property, such as ``jira``.
            key: The key's value, such as ``ABC-123``.
            title: A title to compare with every task's title.
            min_score: Least title similarity (0 to 1) to report.
            max_results: Most similar tasks to report.

        Returns:
            Tasks with the key, and tasks with a similar title.

        Raises:
            AgentToolsError: If neither a key nor a title is given, only half a key is,
                or the vault does not exist.
        """
        field = (key_field or "").strip()
        value = (key or "").strip()
        wanted_title = _normalized(title or "")
        if bool(field) != bool(value):
            raise AgentToolsError("Give key_field and key together, such as jira and ABC-123.")
        if not field and not wanted_title:
            raise AgentToolsError("Give a key (key_field and key), a title, or both.")
        tasks = await asyncio.to_thread(self.scan_tasks)
        exact = [task for task in tasks if field and task.keys.get(field) == value]
        others = [task for task in tasks if task not in exact]
        similar = similar_tasks(title or "", others, min_score) if wanted_title else []
        return FoundTasks(exact=exact, similar=similar[: max(1, max_results)])

    def resolve_note(self, reference: str) -> Path:
        """Find a note by its path in the vault, its name as a ``[[link]]`` gives it, or a key.

        Args:
            reference: A path relative to the vault (``.md`` optional), a link such as
                ``[[ABC-123]]`` or ``[[Folder/Note|shown text]]``, or a key written
                ``field:value``, such as ``ical_uid:040000...`` or ``jira:ABC-123``.

        Returns:
            The note's file.

        Raises:
            AgentToolsError: If the reference leaves the vault, reaches into a dot
                folder, or names several notes.
            NotFoundError: If no note matches.
        """
        root = self.checked_root()
        field, separator, value = reference.strip().partition(":")
        if separator and field.strip() in KEY_FIELDS and value.strip():
            found = self.notes_by_key(field.strip()).get(value.strip(), [])
            if not found:
                raise NotFoundError(f"No note has {field.strip()}: {value.strip()}.")
            if len(found) > 1:
                raise AgentToolsError(
                    f"Several notes have {reference.strip()}: {', '.join(found)}."
                )
            return root / found[0]
        name = reference.strip().removeprefix("[[").removesuffix("]]")
        name = name.split("|", 1)[0].split("#", 1)[0].strip().replace("\\", "/")
        if not name:
            raise AgentToolsError("Name a note: a path in the vault or a [[link]].")
        if not name.endswith(".md"):
            name += ".md"
        given = Path(name)
        if given.is_absolute():
            given = given.resolve()
            if not given.is_relative_to(root):
                raise AgentToolsError(f"{reference} is outside the vault.")
            given = given.relative_to(root)
        if any(part.startswith(".") and part != ".." for part in given.parts):
            raise AgentToolsError(f"{reference} is not a note this tool reads.")
        candidate = (root / given).resolve()
        if not candidate.is_relative_to(root):
            raise AgentToolsError(f"{reference} is outside the vault.")
        if candidate.is_file():
            return candidate
        if len(given.parts) == 1:
            found = [file for file in _markdown_files(root) if file.name == given.name]
            if len(found) == 1:
                return found[0]
            if found:
                paths = ", ".join(file.relative_to(root).as_posix() for file in found)
                raise AgentToolsError(f"Several notes are named {given.stem}: {paths}.")
        raise NotFoundError(f"No note {reference} in the vault.")

    def read_note(self, reference: str, max_chars: int) -> Note:
        """Read one note.

        Args:
            reference: The note's path or link.
            max_chars: Longest body to return.

        Returns:
            The note.
        """
        file = self.resolve_note(reference)
        try:
            parsed = parse(file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            raise AgentToolsError(f"{reference} could not be read: {exc}.") from exc
        limit = max(1, max_chars)
        return Note(
            path=file.relative_to(self.root.resolve()).as_posix(),
            frontmatter=parsed.frontmatter,
            frontmatter_error=parsed.error,
            body=parsed.body[:limit],
            body_chars=len(parsed.body),
            truncated=len(parsed.body) > limit,
        )

    async def get_note(self, reference: str, max_chars: int = 20_000) -> Note:
        """Read one note: its properties and its text.

        Args:
            reference: A path relative to the vault (``.md`` optional), or a ``[[link]]``.
            max_chars: Longest body to return.

        Returns:
            The note.

        Raises:
            AgentToolsError: If the note cannot be found or read, or is outside the vault.
        """
        return await asyncio.to_thread(self.read_note, reference, max_chars)

    def _read_daily(self, day: str | None, max_chars: int) -> DailyNote:
        """Find and read one day's daily note.

        Args:
            day: ``YYYY-MM-DD``, ``today``, ``yesterday`` or ``tomorrow``; None is today.
            max_chars: Longest body to return.

        Returns:
            The daily note, or where it would be when it does not exist.
        """
        when = parse_day(day, self.settings)
        layout = daily_layout(self.settings)
        path = layout.path(when)
        exists = (self.checked_root() / path).is_file()
        return DailyNote(
            day=when.isoformat(),
            path=path,
            exists=exists,
            layout_from=layout.source,
            note=self.read_note(path, max_chars) if exists else None,
        )

    async def get_daily_note(self, day: str | None = None, max_chars: int = 20_000) -> DailyNote:
        """Read one day's daily note, from where Obsidian keeps it.

        Args:
            day: ``YYYY-MM-DD``, ``today``, ``yesterday`` or ``tomorrow``; None is today.
            max_chars: Longest body to return.

        Returns:
            The note, or where it would be when it does not exist yet.

        Raises:
            AgentToolsError: If the day is malformed, the layout cannot be used, or the
                vault does not exist.
        """
        return await asyncio.to_thread(self._read_daily, day, max_chars)
