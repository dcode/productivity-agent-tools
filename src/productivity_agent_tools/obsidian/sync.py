"""Bringing tasks and meetings from the other products into the vault, once each.

``obsidian_sync`` asks every other product that is on for what it offers:
``TaskSource`` (Jira issues, pull requests awaiting review) and
``MeetingSource`` (the calendar). It never names a product; a new source joins
by implementing the protocol.

Every note is found by its key (``jira: ABC-123``, ``ical_uid: ...``), never by
title or path, so running the sync again, or after a meeting moves or an issue is
renamed, updates the note it made before. The sync writes only the fields it owns
(title, status, priority, due date and the source's own fields for a task; title,
time, place and links for a meeting) and never the body, ``scheduled`` or anything
else the user or the agent set. It never deletes: a finished task gets
``status: done``, a cancelled meeting ``cancelled: true``.
"""

import asyncio
import datetime
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, JsonValue

from productivity_agent_tools.capabilities import (
    MeetingSource,
    SourcedMeeting,
    SourcedTask,
    TaskSource,
)
from productivity_agent_tools.errors import AgentToolsError
from productivity_agent_tools.obsidian import notes
from productivity_agent_tools.obsidian.edits import DRY_RUN
from productivity_agent_tools.obsidian.frontmatter import parse, to_json
from productivity_agent_tools.obsidian.layout import meeting_path, today, zone
from productivity_agent_tools.obsidian.notes import ChangedOnDiskError, Property, Snapshot
from productivity_agent_tools.obsidian.paths import keyed_name
from productivity_agent_tools.obsidian.templates import fill
from productivity_agent_tools.obsidian.vault import Vault, similar_tasks
from productivity_agent_tools.products import ToolContext

MEETING_BODY: str = (
    "## Prep\n<!-- productivity-agent-tools:prep:start -->\n"
    "<!-- productivity-agent-tools:prep:end -->\n\n## Agenda\n\n## Notes\n\n## Action items\n"
)
LOOKAROUND_DAYS: int = 7
"""How far either side of a missing meeting's day to look for it before calling it gone."""

GONE_NOTE: str = (
    "No longer on your calendar (cancelled, declined or deleted), so it is marked cancelled."
)
"""What a new meeting note holds under its properties."""

type Action = Literal["created", "updated", "skipped"]


class SyncChange(BaseModel):
    """One note the sync made, changed, or had to leave alone."""

    kind: Literal["task", "meeting"] = Field(description="What the note is for.")
    key: str = Field(description="Its key, such as jira:ABC-123.")
    path: str | None = Field(default=None, description="The note's path in the vault.")
    action: Action = Field(description="created, updated or skipped.")
    fields: dict[str, JsonValue] = Field(
        default_factory=dict, description="Properties set (null for one removed)."
    )
    note: str | None = Field(default=None, description="Why it was skipped, or worth knowing.")


class SyncReport(BaseModel):
    """What obsidian_sync did, or would do."""

    dry_run: bool = Field(description="True if dry-run blocked every change.")
    sources: list[str] = Field(description="What was read: jira, bitbucket, calendar ...")
    unavailable: list[str] = Field(description="Sources that could not be read, with why.")
    meeting_days: list[str] = Field(description="The days whose meetings were read.")
    changes: list[SyncChange] = Field(description="Notes made, changed or left alone.")
    unchanged: int = Field(description="Notes already up to date.")
    ignored: int = Field(
        description="Finished tasks and declined, free, all-day or cancelled meetings that "
        "have no note; none is made for them."
    )
    message: str = Field(description="Plain-language summary.")


@dataclass(slots=True)
class _Fetched:
    """What the sources returned.

    Attributes:
        tasks: Each source's open tasks, by key field.
        finished: Tasks looked up because they were no longer open, by key field.
        meetings: The meetings in the window, and missing ones found elsewhere.
        gone: Keys of meetings that have a note dated in the window but are no longer on
            any calendar.
        sources: Names of the sources read.
        unavailable: Sources that failed, with why.
    """

    tasks: dict[str, list[SourcedTask]] = field(default_factory=dict)
    finished: dict[str, dict[str, SourcedTask]] = field(default_factory=dict)
    meetings: list[SourcedMeeting] = field(default_factory=list)
    gone: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)


def _same(current: JsonValue, wanted: Property) -> bool:
    """Tell whether a property already holds a value.

    Args:
        current: The property as read.
        wanted: The value the sync would set.

    Returns:
        True when nothing needs writing.
    """
    return to_json(current) == to_json(wanted)


def _local(moment: datetime.datetime, tz: datetime.tzinfo) -> str:
    """Write a time as Obsidian's date-and-time properties do, in the vault's zone.

    Args:
        moment: An aware time.
        tz: The vault's time zone.

    Returns:
        ``YYYY-MM-DDTHH:MM``.
    """
    return moment.astimezone(tz).strftime("%Y-%m-%dT%H:%M")


def _worth_a_note(meeting: SourcedMeeting) -> bool:
    """Tell whether a meeting with no note yet should get one.

    Args:
        meeting: The meeting.

    Returns:
        False for a cancelled or all-day event, one the user declined, and one that
        shows the user's time as free.
    """
    return (
        not meeting.cancelled
        and not meeting.all_day
        and (meeting.response or "").lower() != "declined"
        and (meeting.show_as or "").lower() != "free"
    )


class VaultSync:
    """Brings the other products' tasks and meetings into the vault.

    Attributes:
        vault: The vault.
    """

    def __init__(self, vault: Vault) -> None:
        """Create the sync.

        Args:
            vault: The vault it writes to.
        """
        self.vault = vault

    def window(self, days_ahead: int) -> tuple[datetime.datetime, datetime.datetime, list[str]]:
        """The time range whose meetings are read: today and the days after it.

        Args:
            days_ahead: Days after today to include.

        Returns:
            Start and end (aware, local midnight) and the days covered.
        """
        tz = zone(self.vault.settings)
        first = today(self.vault.settings)
        start = datetime.datetime.combine(first, datetime.time(), tz)
        end = datetime.datetime.combine(
            first + datetime.timedelta(days=days_ahead + 1), datetime.time(), tz
        )
        days = [(first + datetime.timedelta(days=n)).isoformat() for n in range(days_ahead + 1)]
        return start, end, days

    async def fetch(self, context: ToolContext, days_ahead: int) -> tuple[_Fetched, list[str]]:
        """Ask every source for its tasks and meetings.

        A source that fails is reported and skipped; the others still sync.

        Args:
            context: The tool context, whose peers are the other products' clients.
            days_ahead: Days after today whose meetings to read.

        Returns:
            What was fetched, and the days whose meetings were read.
        """
        fetched = _Fetched()
        known: dict[str, set[str]] = {}
        for source in context.peers_of(TaskSource):
            name = source.task_source
            try:
                opened = list(await source.my_open_tasks())
                if not known:
                    known = await self._open_keys()
                gone = sorted(known.get(name, set()) - {task.key for task in opened})
                fetched.finished[name] = await source.tasks_by_key(gone) if gone else {}
            except AgentToolsError as exc:
                fetched.unavailable.append(f"{name}: {exc}")
                continue
            fetched.tasks[name] = opened
            fetched.sources.append(name)
        start, end, days = self.window(days_ahead)
        calendars = context.peers_of(MeetingSource)
        read = 0
        for calendar in calendars:
            try:
                fetched.meetings.extend(await calendar.my_meetings(start, end))
            except AgentToolsError as exc:
                fetched.unavailable.append(f"calendar: {exc}")
                continue
            fetched.sources.append("calendar")
            read += 1
        if calendars and read == len(calendars):
            await self._find_missing(fetched, calendars, days)
        return fetched, days

    async def _find_missing(
        self, fetched: _Fetched, calendars: Sequence[MeetingSource], days: list[str]
    ) -> None:
        """Account for meeting notes in the window whose meeting did not come back.

        A meeting that is cancelled, declined or deleted leaves the calendar view, so
        its note would never be revisited. Each such note's meeting is looked for a
        week either side of its day: found, it moved and its note follows; not found,
        it is recorded as gone.

        Args:
            fetched: What the window returned; found and gone meetings are added.
            calendars: The calendars, all of which answered for the window.
            days: The days of the window.
        """
        seen = {meeting.ical_uid for meeting in fetched.meetings}
        dated = await asyncio.to_thread(self._meeting_notes_on, set(days))
        missing = {key: day for key, day in dated.items() if key not in seen}
        if not missing:
            return
        tz = zone(self.vault.settings)
        reach = datetime.timedelta(days=LOOKAROUND_DAYS)
        start = datetime.datetime.combine(min(missing.values()) - reach, datetime.time(), tz)
        end = datetime.datetime.combine(
            max(missing.values()) + reach + datetime.timedelta(days=1), datetime.time(), tz
        )
        found: dict[str, SourcedMeeting] = {}
        for calendar in calendars:
            try:
                meetings = await calendar.my_meetings(start, end)
            except AgentToolsError as exc:
                fetched.unavailable.append(f"calendar (missing meetings): {exc}")
                return
            found.update((m.ical_uid, m) for m in meetings if m.ical_uid in missing)
        fetched.meetings.extend(found.values())
        fetched.gone = sorted(set(missing) - set(found))

    def _meeting_notes_on(self, days: set[str]) -> dict[str, datetime.date]:
        """Find the meeting notes dated on some days and not yet marked cancelled.

        Args:
            days: The days, ``YYYY-MM-DD``.

        Returns:
            Each note's meeting key and day; keys on several notes are left out.
        """
        root = self.vault.checked_root()
        dated: dict[str, datetime.date] = {}
        for key, paths in self.vault.notes_by_key("ical_uid").items():
            if len(paths) != 1:
                continue
            properties = parse(notes.read(root / paths[0]).text).frontmatter
            day = properties.get("date")
            if isinstance(day, str) and day in days and properties.get("cancelled") is not True:
                dated[key] = datetime.date.fromisoformat(day)
        return dated

    async def _open_keys(self) -> dict[str, set[str]]:
        """The keys of the vault's unfinished tasks, by key field.

        Returns:
            The keys.
        """
        tasks = await asyncio.to_thread(self.vault.scan_tasks)
        keys: dict[str, set[str]] = {}
        for task in tasks:
            if not task.done:
                for name, value in task.keys.items():
                    keys.setdefault(name, set()).add(value)
        return keys

    def apply(self, fetched: _Fetched, days: list[str]) -> SyncReport:
        """Make or update the notes for what was fetched.

        Args:
            fetched: What the sources returned.
            days: The days whose meetings were read.

        Returns:
            What was done.
        """
        changes: list[SyncChange] = []
        unchanged = ignored = 0
        for name in fetched.sources:
            if name == "calendar":
                continue
            index = self.vault.notes_by_key(name)
            for task in [*fetched.tasks[name], *fetched.finished[name].values()]:
                paths = index.get(task.key, [])
                if not paths and task.done:
                    ignored += 1
                elif change := self._task(task, paths):
                    changes.append(change)
                else:
                    unchanged += 1
        if fetched.meetings or fetched.gone:
            index = self.vault.notes_by_key("ical_uid")
            for meeting in fetched.meetings:
                paths = index.get(meeting.ical_uid, [])
                if not paths and not _worth_a_note(meeting):
                    ignored += 1
                elif change := self._meeting(meeting, paths):
                    changes.append(change)
                else:
                    unchanged += 1
            for key in fetched.gone:
                paths = index.get(key, [])
                if len(paths) != 1:
                    continue
                change = self._update("meeting", f"ical_uid:{key}", paths[0], {"cancelled": True})
                if change is None:
                    unchanged += 1
                    continue
                change.note = change.note or GONE_NOTE
                changes.append(change)
        made = sum(change.action == "created" for change in changes)
        updated = sum(change.action == "updated" for change in changes)
        skipped = sum(change.action == "skipped" for change in changes)
        summary = f"{made} made, {updated} updated, {unchanged} already up to date"
        summary += f", {ignored} without a note by design" if ignored else ""
        summary += f", {skipped} left alone (see each note)" if skipped else ""
        if fetched.unavailable:
            summary += f". Not read: {'; '.join(fetched.unavailable)}"
        return SyncReport(
            dry_run=self.vault.dry_run,
            sources=fetched.sources,
            unavailable=fetched.unavailable,
            meeting_days=days if "calendar" in fetched.sources else [],
            changes=changes,
            unchanged=unchanged,
            ignored=ignored,
            message=f"{DRY_RUN} {summary}." if self.vault.dry_run else f"{summary}.",
        )

    def _write(
        self, path: Path, snapshot: Snapshot, changes: dict[str, Property], start: str = ""
    ) -> str | None:
        """Write property changes, unless dry-run is on or the note changed meanwhile.

        Args:
            path: The note's file.
            snapshot: What was read.
            changes: Properties to set or remove.
            start: For a new note, the text it starts from (a filled template, or a
                body); the properties are merged into any it already has.

        Returns:
            None when written (or dry-run), or why it was left alone.
        """
        base = snapshot.text if snapshot.stamp is not None else start
        text = notes.set_properties(base, changes)
        if self.vault.dry_run:
            return None
        try:
            notes.write(path, text, snapshot)
        except ChangedOnDiskError as exc:
            return str(exc)
        return None

    def _now(self) -> str:
        """The current time for ``dateModified``, in the vault's zone.

        Returns:
            ISO 8601 with offset, to the second.
        """
        moment = datetime.datetime.now(zone(self.vault.settings)).replace(microsecond=0)
        return moment.isoformat()

    def _update(
        self, kind: Literal["task", "meeting"], key: str, path: str, owned: dict[str, Property]
    ) -> SyncChange | None:
        """Bring an existing note's owned fields up to date.

        Args:
            kind: What the note is for.
            key: Its key, for the report.
            path: Its path in the vault.
            owned: The values the sync owns.

        Returns:
            The change, or None when it was already up to date.
        """
        file = self.vault.checked_root() / path
        snapshot = notes.read(file)
        current = parse(snapshot.text).frontmatter
        changes = {
            name: value for name, value in owned.items() if not _same(current.get(name), value)
        }
        if not changes:
            return None
        if kind == "task":
            changes["dateModified"] = self._now()
        problem = self._write(file, snapshot, changes)
        shown = to_json(dict(changes))
        assert isinstance(shown, dict)
        if problem:
            return SyncChange(kind=kind, key=key, path=path, action="skipped", note=problem)
        return SyncChange(kind=kind, key=key, path=path, action="updated", fields=shown)

    def _task(self, task: SourcedTask, paths: Sequence[str]) -> SyncChange | None:
        """Make or update the note for one task (a finished one only if it has a note).

        Args:
            task: The task.
            paths: Notes already carrying its key.

        Returns:
            What was done, or None when its note was already up to date.
        """
        key = f"{task.source}:{task.key}"
        if len(paths) > 1:
            return SyncChange(
                kind="task",
                key=key,
                action="skipped",
                note=f"Several notes have this key ({', '.join(paths)}); merge them, then sync.",
            )
        owned: dict[str, Property] = {
            "title": task.title,
            "status": task.status,
            "priority": task.priority,
            "due": task.due,
            "source_status": task.source_status,
            "url": task.url,
            "completedDate": None,
        }
        if task.priority is None:
            del owned["priority"]  # A source without priorities leaves the user's alone.
        if task.estimate_minutes is not None:
            owned["source_estimate"] = task.estimate_minutes
        if not paths:
            return self._create_task(task, key, owned)
        current = parse(notes.read(self.vault.checked_root() / paths[0]).text).frontmatter
        local, seen = current.get("status"), current.get("source_status")
        # The source has not moved since the last sync (its own status name is the one
        # this note recorded), yet the note's status differs: the user changed it here.
        pending = isinstance(seen, str) and seen == task.source_status and local != task.status
        note = None
        if pending:
            for name in ("status", "completedDate"):
                del owned[name]
            note = (
                f"Changed in Obsidian to {local}, but {task.source} still says {task.status}"
                f" ({task.source_status}); left as you set it. Change it in {task.source}"
                " (wrap-day offers to), or set it back here."
            )
        elif task.done:
            done_on = current.get("completedDate")
            owned["completedDate"] = (
                done_on if isinstance(done_on, str) and done_on else today(self.vault.settings)
            )
        change = self._update("task", key, paths[0], owned)
        if note is None:
            return change
        if change is None:
            return SyncChange(kind="task", key=key, path=paths[0], action="skipped", note=note)
        change.note = change.note or note
        return change

    def _create_task(self, task: SourcedTask, key: str, owned: dict[str, Property]) -> SyncChange:
        """Make a new task note.

        Args:
            task: The task.
            key: Its key, for the report.
            owned: The fields the sync owns.

        Returns:
            What was done.
        """
        root = self.vault.checked_root()
        folder = root / self.vault.settings.tasks_folder
        name = keyed_name(task.key, task.title, self.vault.settings.task_title_length)
        file = notes.free_path(folder / f"{name}.md")
        stamp = self._now()
        properties: dict[str, Property] = {
            **{name: value for name, value in owned.items() if value is not None},
            "tags": [self.vault.settings.task_tag],
            "dateCreated": stamp,
            "dateModified": stamp,
            task.source: task.key,
        }
        if "priority" not in properties:
            properties["priority"] = "normal"
        path = file.relative_to(root).as_posix()
        close = similar_tasks(
            task.title,
            [t for t in self.vault.scan_tasks() if not t.done and task.source not in t.keys],
            0.8,
        )
        problem = self._write(file, Snapshot("", None), properties)
        shown = to_json(dict(properties))
        assert isinstance(shown, dict)
        if problem:
            return SyncChange(kind="task", key=key, path=path, action="skipped", note=problem)
        note = None
        if close:
            note = (
                f"Possibly the same as {close[0].task.path}, made by hand; if so, move its "
                "notes here and delete it."
            )
        return SyncChange(
            kind="task", key=key, path=path, action="created", fields=shown, note=note
        )

    def _meeting(self, meeting: SourcedMeeting, paths: Sequence[str]) -> SyncChange | None:
        """Make or update the note for one meeting.

        Only a meeting worth a note (``_worth_a_note``) is given a new one; one that
        already has a note is always updated.

        Args:
            meeting: The meeting.
            paths: Notes already carrying its key.

        Returns:
            What was done, or None when nothing was needed.
        """
        key = f"ical_uid:{meeting.ical_uid}"
        if len(paths) > 1:
            return SyncChange(
                kind="meeting",
                key=key,
                action="skipped",
                note=f"Several notes have this key ({', '.join(paths)}); merge them, then sync.",
            )
        tz = zone(self.vault.settings)
        owned: dict[str, Property] = {
            "title": meeting.title,
            "date": meeting.start.astimezone(tz).date(),
            "start": _local(meeting.start, tz),
            "end": _local(meeting.end, tz),
            "organizer": meeting.organizer,
            "location": meeting.location or None,
            "join": meeting.join_url,
            "url": meeting.url,
            "response": meeting.response,
            "cancelled": True if meeting.cancelled else None,
            "series_id": meeting.series_id,
        }
        if paths:
            change = self._update("meeting", key, paths[0], owned)
            if change and "date" in change.fields:
                change.note = "The meeting moved to another day; its note stays where it is."
            return change
        root = self.vault.checked_root()
        day = meeting.start.astimezone(tz).date()
        file = notes.free_path(root / meeting_path(self.vault.settings, day, meeting.title))
        properties: dict[str, Property] = {
            **{name: value for name, value in owned.items() if value is not None},
            "tags": [self.vault.settings.meeting_tag],
            "ical_uid": meeting.ical_uid,
        }
        path = file.relative_to(root).as_posix()
        start, note = self._meeting_start(meeting)
        problem = self._write(file, Snapshot("", None), properties, start)
        shown = to_json(dict(properties))
        assert isinstance(shown, dict)
        if problem:
            return SyncChange(kind="meeting", key=key, path=path, action="skipped", note=problem)
        return SyncChange(
            kind="meeting", key=key, path=path, action="created", fields=shown, note=note
        )

    def _meeting_start(self, meeting: SourcedMeeting) -> tuple[str, str | None]:
        """The text a new meeting note starts from: the configured template, or the default.

        Args:
            meeting: The meeting.

        Returns:
            The text, and a note for the report when the template could not be read.
        """
        name = self.vault.settings.meeting_template
        if not name:
            return f"\n{MEETING_BODY}", None
        root = self.vault.checked_root()
        file = root / (name if name.endswith(".md") else f"{name}.md")
        try:
            template = file.read_text(encoding="utf-8")
        except OSError, UnicodeDecodeError:
            return (
                f"\n{MEETING_BODY}",
                f"meeting_template {name} could not be read, so the default layout was used.",
            )
        moment = meeting.start.astimezone(zone(self.vault.settings))
        return fill(template, root, meeting.title, moment), None
