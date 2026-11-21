"""obsidian_sync: tasks and meetings from the other products, each made once and kept current."""

import datetime
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from mcp.server.mcpserver import MCPServer

from productivity_agent_tools.capabilities import SourcedMeeting, SourcedTask
from productivity_agent_tools.errors import AgentToolsError
from productivity_agent_tools.obsidian import layout
from productivity_agent_tools.obsidian import sync as sync_module
from productivity_agent_tools.obsidian.edits import DRY_RUN
from productivity_agent_tools.obsidian.frontmatter import parse
from productivity_agent_tools.obsidian.notes import ChangedOnDiskError
from productivity_agent_tools.obsidian.settings import ObsidianSettings
from productivity_agent_tools.obsidian.sync import MEETING_BODY, SyncReport, VaultSync
from productivity_agent_tools.obsidian.tools import register_obsidian_tools
from productivity_agent_tools.obsidian.vault import Vault
from productivity_agent_tools.products import ToolContext

DAY = datetime.date(2026, 11, 19)
UTC = datetime.UTC


class FakeTasks:
    def __init__(
        self,
        source: str,
        opened: list[SourcedTask],
        lookup: dict[str, SourcedTask] | None = None,
        fail: bool = False,
    ) -> None:
        self.source = source
        self.opened = opened
        self.lookup = lookup or {}
        self.fail = fail
        self.asked: list[list[str]] = []

    @property
    def task_source(self) -> str:
        return self.source

    async def my_open_tasks(self) -> Sequence[SourcedTask]:
        if self.fail:
            raise AgentToolsError("VPN is down")
        return self.opened

    async def tasks_by_key(self, keys: Sequence[str]) -> dict[str, SourcedTask]:
        self.asked.append(list(keys))
        return {key: task for key, task in self.lookup.items() if key in keys}


class FakeCalendar:
    """A calendar that, like Outlook's calendar view, returns only meetings in the range."""

    def __init__(
        self, meetings: list[SourcedMeeting], fail: bool = False, fail_after: int | None = None
    ) -> None:
        self.meetings = meetings
        self.fail = fail
        self.fail_after = fail_after
        self.windows: list[tuple[datetime.datetime, datetime.datetime]] = []

    async def my_meetings(
        self, start: datetime.datetime, end: datetime.datetime
    ) -> Sequence[SourcedMeeting]:
        if self.fail or (self.fail_after is not None and len(self.windows) >= self.fail_after):
            raise AgentToolsError("signed out")
        self.windows.append((start, end))
        return [m for m in self.meetings if start <= m.start < end]


def jira_task(key: str, **values: Any) -> SourcedTask:
    fields: dict[str, Any] = {
        "title": f"Issue {key}",
        "status": "open",
        "done": False,
        "priority": "normal",
        "url": f"https://jira/browse/{key}",
        "source_status": "To Do",
    }
    fields.update(values)
    return SourcedTask(source="jira", key=key, **fields)


def meeting(uid: str, hour: int, **values: Any) -> SourcedMeeting:
    start = datetime.datetime(2026, 11, 19, hour, tzinfo=UTC)
    fields: dict[str, Any] = {
        "title": f"Meeting {uid}",
        "start": start,
        "end": start + datetime.timedelta(minutes=30),
        "response": "Accepted",
        "show_as": "Busy",
        "organizer": "Sam",
    }
    fields.update(values)
    return SourcedMeeting(ical_uid=uid, **fields)


def _write(root: Path, path: str, text: str) -> None:
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding="utf-8")


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    root = tmp_path / "Notes"
    (root / ".obsidian").mkdir(parents=True)
    tasks = "TaskNotes/Tasks"
    _write(
        root,
        f"{tasks}/ABC-1.md",
        "---\ntitle: Old title\ntags: [task]\nstatus: open\nscheduled: 2026-11-19\n"
        "jira: ABC-1\n---\nMy notes on it.\n",
    )
    _write(
        root,
        f"{tasks}/ABC-9.md",
        "---\ntitle: Issue ABC-9\ntags: [task]\nstatus: open\njira: ABC-9\n---\n",
    )
    _write(
        root,
        f"{tasks}/ABC-8.md",
        "---\ntitle: Gone\ntags: [task]\nstatus: open\njira: ABC-8\n---\n",
    )
    _write(root, f"{tasks}/Seven a.md", "---\ntags: [task]\nstatus: open\njira: ABC-7\n---\n")
    _write(root, f"{tasks}/Seven b.md", "---\ntags: [task]\nstatus: open\njira: ABC-7\n---\n")
    _write(
        root,
        f"{tasks}/Write the release notes.md",
        "---\ntitle: Write the release notes\ntags: [task]\nstatus: open\nuid: x1\n---\n",
    )
    _write(
        root,
        "Meetings/2026/11/2026-11-18 Planning.md",
        "---\ntitle: Planning\ndate: 2026-11-18\ntags: [meeting]\nical_uid: m1\n---\n"
        "## Notes\nKeep me.\n",
    )
    _write(root, "Meetings/Retro.md", "---\ntitle: Retro\ntags: [meeting]\nical_uid: m4\n---\n")
    return root


@pytest.fixture
def make_sync(vault_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., VaultSync]:
    monkeypatch.setattr(layout, "today", lambda settings: DAY)
    monkeypatch.setattr(sync_module, "today", lambda settings: DAY)
    monkeypatch.setattr(VaultSync, "_now", lambda self: "2026-11-19T09:00:00+00:00")

    def build(dry_run: bool = False, **overrides: Any) -> VaultSync:
        values: dict[str, Any] = {
            "meeting_date_format": "YYYY/MM/YYYY-MM-DD",
            "time_zone": ZoneInfo("UTC"),
            **overrides,
        }
        settings = ObsidianSettings(vault=vault_dir, **values)
        return VaultSync(Vault(settings, dry_run))

    return build


def _sources() -> tuple[FakeTasks, FakeTasks, FakeCalendar]:
    jira = FakeTasks(
        "jira",
        [
            jira_task(
                "ABC-1", title="Fix the login timeout", status="in-progress", due="2026-11-21"
            ),
            jira_task("ABC-2", title="Write release notes"),
            jira_task("ABC-7"),
        ],
        lookup={"ABC-9": jira_task("ABC-9", status="done", done=True, source_status="Done")},
    )
    bitbucket = FakeTasks(
        "bitbucket",
        [
            SourcedTask(
                source="bitbucket",
                key="PROJ/web#42",
                title="Review: Change 42",
                status="open",
                done=False,
            )
        ],
    )
    calendar = FakeCalendar(
        [
            meeting("m1", 15, title="Planning"),
            meeting(
                "m2",
                14,
                title="Design review: auth",
                location="Room A",
                join_url="https://join/2",
                series_id="s2",
            ),
            meeting("m6", 16, title="Design review: auth"),
            meeting("m3", 17, response="Declined"),
            meeting("m4", 18, title="Retro", cancelled=True),
            meeting("m5", 0, all_day=True),
            meeting("m7", 19, show_as="Free"),
            meeting("m8", 20, cancelled=True),
        ]
    )
    return jira, bitbucket, calendar


def _context(*peers: object) -> ToolContext:
    return ToolContext(
        dry_run=False, token_source="", peers={f"p{n}": peer for n, peer in enumerate(peers)}
    )


async def _run(sync: VaultSync, *peers: object, days_ahead: int = 1) -> SyncReport:
    fetched, days = await sync.fetch(_context(*peers), days_ahead)
    return sync.apply(fetched, days)


def _props(root: Path, path: str) -> dict[str, Any]:
    return parse((root / path).read_text(encoding="utf-8")).frontmatter


async def test_a_sync_makes_each_note_once_and_keeps_it_current(
    make_sync: Callable[..., VaultSync], vault_dir: Path
) -> None:
    jira, bitbucket, calendar = _sources()
    report = await _run(make_sync(), jira, bitbucket, calendar)
    assert report.sources == ["jira", "bitbucket", "calendar"]
    assert report.meeting_days == ["2026-11-19", "2026-11-20"]
    assert jira.asked == [["ABC-8", "ABC-9"]]
    assert bitbucket.asked == []
    by_key = {change.key: change for change in report.changes}

    updated = by_key["jira:ABC-1"]
    assert (updated.action, updated.path) == ("updated", "TaskNotes/Tasks/ABC-1.md")
    text = (vault_dir / "TaskNotes/Tasks/ABC-1.md").read_text()
    assert text.endswith("---\nMy notes on it.\n")
    props = _props(vault_dir, "TaskNotes/Tasks/ABC-1.md")
    assert props["title"] == "Fix the login timeout"
    assert (props["status"], props["due"], props["scheduled"]) == (
        "in-progress",
        "2026-11-21",
        "2026-11-19",
    )
    assert props["dateModified"] == "2026-11-19T09:00:00+00:00"

    created = by_key["jira:ABC-2"]
    assert (created.action, created.path) == (
        "created",
        "TaskNotes/Tasks/ABC-2 write-release-notes.md",
    )
    assert created.note is not None
    assert "Possibly the same as TaskNotes/Tasks/Write the release notes.md" in created.note
    assert created.path is not None
    new = _props(vault_dir, created.path)
    assert (new["jira"], new["tags"], new["priority"], new["source_status"]) == (
        "ABC-2",
        ["task"],
        "normal",
        "To Do",
    )
    review = _props(vault_dir, "TaskNotes/Tasks/PROJ-web-42 review-change-42.md")
    assert (review["bitbucket"], review["priority"]) == ("PROJ/web#42", "normal")

    finished = _props(vault_dir, "TaskNotes/Tasks/ABC-9.md")
    assert (finished["status"], finished["completedDate"]) == ("done", "2026-11-19")
    assert _props(vault_dir, "TaskNotes/Tasks/ABC-8.md")["status"] == "open"
    duplicate = by_key["jira:ABC-7"]
    assert duplicate.action == "skipped"
    assert duplicate.note is not None and "Several notes have this key" in duplicate.note

    moved = by_key["ical_uid:m1"]
    assert moved.action == "updated"
    assert moved.note == "The meeting moved to another day; its note stays where it is."
    planning = vault_dir / "Meetings/2026/11/2026-11-18 Planning.md"
    assert planning.read_text().endswith("## Notes\nKeep me.\n")
    assert _props(vault_dir, "Meetings/2026/11/2026-11-18 Planning.md")["start"] == (
        "2026-11-19T15:00"
    )
    design = "Meetings/2026/11/2026-11-19 Design review auth.md"
    assert by_key["ical_uid:m2"].path == design
    assert by_key["ical_uid:m6"].path == "Meetings/2026/11/2026-11-19 Design review auth 2.md"
    made = _props(vault_dir, design)
    assert made == {
        "title": "Design review: auth",
        "date": "2026-11-19",
        "start": "2026-11-19T14:00",
        "end": "2026-11-19T14:30",
        "organizer": "Sam",
        "location": "Room A",
        "join": "https://join/2",
        "response": "Accepted",
        "series_id": "s2",
        "tags": ["meeting"],
        "ical_uid": "m2",
    }
    assert (vault_dir / design).read_text().endswith("---\n\n" + MEETING_BODY)
    assert _props(vault_dir, "Meetings/Retro.md")["cancelled"] is True
    for skipped in ("m3", "m5", "m7", "m8"):
        assert f"ical_uid:{skipped}" not in by_key
    assert (report.unchanged, report.ignored) == (0, 4)
    assert report.message == (
        "4 made, 4 updated, 0 already up to date, 4 without a note by design, "
        "1 left alone (see each note)."
    )

    again = await _run(make_sync(), *_sources())
    assert [c.key for c in again.changes] == ["jira:ABC-7"]
    assert (again.unchanged, again.ignored) == (7, 4)


async def test_a_finished_task_keeps_its_completion_day_and_a_reopened_one_loses_it(
    make_sync: Callable[..., VaultSync], vault_dir: Path
) -> None:
    _write(
        vault_dir,
        "TaskNotes/Tasks/ABC-9.md",
        "---\ntitle: Issue ABC-9\ntags: [task]\nstatus: done\ncompletedDate: 2026-11-02\n"
        "jira: ABC-9\nurl: https://jira/browse/ABC-9\npriority: normal\nsource_status: Done\n---\n",
    )
    done = FakeTasks(
        "jira",
        [],
        lookup={"ABC-9": jira_task("ABC-9", status="done", done=True, source_status="Done")},
    )
    report = await _run(make_sync(), done)
    assert report.changes == []
    reopened = FakeTasks("jira", [jira_task("ABC-9", source_status="Reopened")])
    await _run(make_sync(), reopened)
    props = _props(vault_dir, "TaskNotes/Tasks/ABC-9.md")
    assert (props["status"], "completedDate" in props) == ("open", False)


async def test_a_finished_task_is_not_made_new(make_sync: Callable[..., VaultSync]) -> None:
    source = FakeTasks("jira", [jira_task("ABC-50", status="done", done=True)])
    report = await _run(make_sync(), source)
    assert report.changes == []


async def test_dry_run_changes_nothing(
    make_sync: Callable[..., VaultSync], vault_dir: Path
) -> None:
    before = {p: p.read_text() for p in vault_dir.rglob("*.md")}
    report = await _run(make_sync(dry_run=True), *_sources())
    assert report.dry_run is True
    assert report.message.startswith(DRY_RUN)
    assert {c.action for c in report.changes} == {"created", "updated", "skipped"}
    assert {p: p.read_text() for p in vault_dir.rglob("*.md")} == before


async def test_a_source_that_fails_is_reported_and_the_rest_sync(
    make_sync: Callable[..., VaultSync],
) -> None:
    jira = FakeTasks("jira", [], fail=True)
    calendar = FakeCalendar([], fail=True)
    bitbucket = FakeTasks(
        "bitbucket",
        [
            SourcedTask(
                source="bitbucket", key="P/r#1", title="Review: one", status="open", done=False
            )
        ],
    )
    report = await _run(make_sync(), jira, calendar, bitbucket)
    assert report.unavailable == ["jira: VPN is down", "calendar: signed out"]
    assert report.sources == ["bitbucket"]
    assert report.meeting_days == []
    assert [c.key for c in report.changes] == ["bitbucket:P/r#1"]
    assert "Not read: jira: VPN is down; calendar: signed out" in report.message


async def test_a_note_edited_during_the_sync_is_left_alone(
    make_sync: Callable[..., VaultSync], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(path: Path, text: str, snapshot: object) -> None:
        raise ChangedOnDiskError(f"{path.name} changed")

    monkeypatch.setattr(sync_module.notes, "write", refuse)
    report = await _run(make_sync(), *_sources())
    assert {c.action for c in report.changes} == {"skipped"}
    assert any(c.note == "ABC-1.md changed" for c in report.changes)
    assert any(c.note == "2026-11-19 Design review auth.md changed" for c in report.changes)
    assert any(c.note == "PROJ-web-42 review-change-42.md changed" for c in report.changes)


async def test_the_meeting_window_is_local_days(
    make_sync: Callable[..., VaultSync],
) -> None:
    calendar = FakeCalendar([])
    sync = make_sync(time_zone=ZoneInfo("America/New_York"))
    report = await _run(sync, calendar, days_ahead=0)
    start, end = calendar.windows[0]
    assert start.isoformat() == "2026-11-19T00:00:00-05:00"
    assert end - start == datetime.timedelta(days=1)
    assert report.meeting_days == ["2026-11-19"]
    assert report.message == "0 made, 0 updated, 0 already up to date."


async def test_the_sync_tool_uses_the_configured_days(
    make_sync: Callable[..., VaultSync], vault_dir: Path
) -> None:
    calendar = FakeCalendar([meeting("m9", 9, title="Standup")])
    vault = make_sync(sync_days_ahead=3).vault
    server = MCPServer("t")
    register_obsidian_tools(server, vault, _context(calendar))
    result = await server.call_tool("obsidian_sync", {})
    shown = json.dumps(getattr(result, "structured_content", None))
    assert "Meetings/2026/11/2026-11-19 Standup.md" in shown
    assert len(calendar.windows) == 1
    assert calendar.windows[0][1] - calendar.windows[0][0] == datetime.timedelta(days=4)
    await server.call_tool("obsidian_sync", {"days_ahead": 0})
    assert calendar.windows[1][1] - calendar.windows[1][0] == datetime.timedelta(days=1)


def test_the_sync_settings_are_read() -> None:
    from productivity_agent_tools.errors import ConfigError
    from productivity_agent_tools.obsidian.settings import parse_obsidian_settings

    settings = parse_obsidian_settings({"vault": "/v", "sync_days_ahead": 3, "meeting_tag": "#mtg"})
    assert (settings.sync_days_ahead, settings.meeting_tag) == (3, "mtg")
    assert parse_obsidian_settings({"vault": "/v", "meeting_tag": " "}).meeting_tag == "meeting"
    for bad in (15, -1, True, "2"):
        with pytest.raises(ConfigError, match="sync_days_ahead"):
            parse_obsidian_settings({"vault": "/v", "sync_days_ahead": bad})


def test_the_sync_writes_the_time_it_ran(vault_dir: Path) -> None:
    sync = VaultSync(Vault(ObsidianSettings(vault=vault_dir), True))
    stamp = datetime.datetime.fromisoformat(sync._now())
    assert stamp.tzinfo is not None
    assert stamp.microsecond == 0


async def test_names_and_keys_that_clash_are_handled(
    make_sync: Callable[..., VaultSync], vault_dir: Path
) -> None:
    _write(vault_dir, "TaskNotes/Tasks/ABC-60 issue-abc-60.md", "A note of my own.\n")
    _write(vault_dir, "TaskNotes/Tasks/Empty key.md", "---\ntags: [task]\njira:\n---\n")
    _write(vault_dir, "Meetings/Copy of retro.md", "---\nical_uid: m4\n---\n")
    source = FakeTasks("jira", [jira_task("ABC-60")])
    calendar = FakeCalendar([meeting("m4", 9, title="Retro")])
    report = await _run(make_sync(), source, calendar)
    by_key = {change.key: change for change in report.changes}
    assert by_key["jira:ABC-60"].path == "TaskNotes/Tasks/ABC-60 issue-abc-60 2.md"
    own = vault_dir / "TaskNotes/Tasks/ABC-60 issue-abc-60.md"
    assert own.read_text() == "A note of my own.\n"
    clash = by_key["ical_uid:m4"]
    assert clash.action == "skipped"
    assert clash.note is not None and "Meetings/Copy of retro.md" in clash.note


async def test_the_server_offers_jira_to_the_vault_sync(
    vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    from productivity_agent_tools import cli
    from productivity_agent_tools.config import parse_settings
    from tests.conftest import FakeJira

    monkeypatch.setenv("JIRA_TOKEN", "good-token")
    fake = FakeJira()
    settings = parse_settings(
        {
            "base_url": "https://host.example",
            "cookies": {"consent": "yes"},
            "jira": {},
            "obsidian": {"vault": str(vault_dir), "dry_run": False},
        }
    )
    async with cli._open_server(settings, httpx.MockTransport(fake.handler)) as server:
        result = await server.call_tool("obsidian_sync", {})
    report = getattr(result, "structured_content", None)
    assert report is not None
    assert report["sources"] == ["jira"]
    assert [c["key"] for c in report["changes"]] == ["jira:ABC-1"]
    path = report["changes"][0]["path"]
    assert _props(vault_dir, path)["jira"] == "ABC-1"
    searches = [r.url.params["jql"] for r in fake.requests if r.url.path.endswith("/search")]
    assert searches == [
        "assignee = currentUser() AND resolution = Unresolved ORDER BY updated DESC",
        "key in (ABC-7, ABC-8, ABC-9)",
    ]


# --- meetings that leave the calendar --------------------------------------------------


def _meeting_note(root: Path, name: str, uid: str, day: str, extra: str = "") -> str:
    path = f"Meetings/{name}.md"
    _write(root, path, f"---\ntitle: {name}\ndate: {day}\nical_uid: {uid}\n{extra}---\nNotes.\n")
    return path


async def test_a_meeting_gone_from_the_calendar_is_marked_cancelled(
    make_sync: Callable[..., VaultSync], vault_dir: Path
) -> None:
    gone = _meeting_note(vault_dir, "Gone", "g1", "2026-11-19")
    moved = _meeting_note(vault_dir, "Moved", "mv", "2026-11-20")
    _meeting_note(vault_dir, "Done before", "old", "2026-11-19", "cancelled: true\n")
    _meeting_note(vault_dir, "Long ago", "past", "2026-10-01")
    later = meeting(
        "mv",
        10,
        title="Moved",
        start=datetime.datetime(2026, 11, 25, 10, tzinfo=UTC),
        end=datetime.datetime(2026, 11, 25, 11, tzinfo=UTC),
    )
    calendar = FakeCalendar([meeting("kept", 9, title="Kept"), later])
    report = await _run(make_sync(), calendar)
    start, end = calendar.windows[1]
    assert (start.date().isoformat(), end.date().isoformat()) == ("2026-11-12", "2026-11-28")
    by_key = {change.key: change for change in report.changes}
    assert by_key["ical_uid:g1"].note == sync_module.GONE_NOTE
    assert by_key["ical_uid:g1"].fields == {"cancelled": True}
    assert _props(vault_dir, gone)["cancelled"] is True
    assert (vault_dir / gone).read_text().endswith("---\nNotes.\n")
    assert (
        by_key["ical_uid:mv"].note
        == "The meeting moved to another day; its note stays where it is."
    )
    assert _props(vault_dir, moved)["date"] == "2026-11-25"
    assert "ical_uid:old" not in by_key and "ical_uid:past" not in by_key

    again = await _run(make_sync(), FakeCalendar([meeting("kept", 9, title="Kept"), later]))
    assert "ical_uid:g1" not in {c.key for c in again.changes}


async def test_nothing_is_marked_gone_when_a_calendar_fails(
    make_sync: Callable[..., VaultSync], vault_dir: Path
) -> None:
    gone = _meeting_note(vault_dir, "Gone", "g1", "2026-11-19")
    second = FakeCalendar([], fail_after=1)
    report = await _run(make_sync(), second)
    assert report.unavailable == ["calendar (missing meetings): signed out"]
    assert "cancelled" not in _props(vault_dir, gone)
    first = FakeCalendar([], fail=True)
    working = FakeCalendar([])
    report = await _run(make_sync(), working, first)
    assert len(working.windows) == 1
    assert "cancelled" not in _props(vault_dir, gone)


async def test_a_gone_meeting_whose_note_changed_meanwhile(
    make_sync: Callable[..., VaultSync], vault_dir: Path
) -> None:
    removed = _meeting_note(vault_dir, "Removed", "r1", "2026-11-19")
    cancelled = _meeting_note(vault_dir, "Cancelled", "c1", "2026-11-19")
    _meeting_note(vault_dir, "Twin a", "tw", "2026-11-19")
    _meeting_note(vault_dir, "Twin b", "tw", "2026-11-19")
    sync = make_sync()
    fetched, days = await sync.fetch(_context(FakeCalendar([])), 1)
    assert fetched.gone == ["c1", "r1"]
    (vault_dir / removed).unlink()
    _meeting_note(vault_dir, "Cancelled", "c1", "2026-11-19", "cancelled: true\n")
    report = sync.apply(fetched, days)
    assert report.changes == []
    assert report.unchanged == 1
    assert _props(vault_dir, cancelled)["cancelled"] is True


async def test_new_meeting_notes_start_from_the_meeting_template(
    make_sync: Callable[..., VaultSync], vault_dir: Path
) -> None:
    _write(
        vault_dir,
        "Templates/Meeting (sync).md",
        "---\ntype: meeting\ntags: [old]\n---\n# {{title}}\n{{date:dddd D MMMM}} at {{time}}\n\n"
        "## Action items\n- [ ] \n",
    )
    calendar = FakeCalendar([meeting("t1", 14, title="Design review")])
    sync = make_sync(meeting_template="Templates/Meeting (sync)")
    report = await _run(sync, calendar)
    [change] = [c for c in report.changes if c.key == "ical_uid:t1"]
    assert change.note is None
    assert change.path is not None
    text = (vault_dir / change.path).read_text()
    properties = _props(vault_dir, change.path)
    assert properties["type"] == "meeting"
    assert (properties["tags"], properties["ical_uid"]) == (["meeting"], "t1")
    assert text.endswith(
        "---\n# Design review\nThursday 19 November at 14:00\n\n## Action items\n- [ ] \n"
    )


async def test_a_missing_meeting_template_falls_back_to_the_default(
    make_sync: Callable[..., VaultSync], vault_dir: Path
) -> None:
    calendar = FakeCalendar([meeting("t2", 14, title="Sync")])
    report = await _run(make_sync(meeting_template="Templates/Nope.md"), calendar)
    [change] = [c for c in report.changes if c.key == "ical_uid:t2"]
    assert change.note == (
        "meeting_template Templates/Nope.md could not be read, so the default layout was used."
    )
    assert change.path is not None
    text = (vault_dir / change.path).read_text()
    assert text.endswith("---\n\n" + MEETING_BODY)
    assert "productivity-agent-tools:prep:start" in text


def test_the_meeting_template_setting_stays_in_the_vault() -> None:
    from productivity_agent_tools.errors import ConfigError
    from productivity_agent_tools.obsidian.settings import parse_obsidian_settings

    assert parse_obsidian_settings({"vault": "/v"}).meeting_template is None
    parsed = parse_obsidian_settings({"vault": "/v", "meeting_template": "/T/Meeting/"})
    assert parsed.meeting_template == "T/Meeting"
    with pytest.raises(ConfigError, match="meeting_template"):
        parse_obsidian_settings({"vault": "/v", "meeting_template": "../x"})


async def test_the_sync_brings_in_the_sources_estimate(
    make_sync: Callable[..., VaultSync], vault_dir: Path
) -> None:
    source = FakeTasks("jira", [jira_task("ABC-70", estimate_minutes=90), jira_task("ABC-71")])
    report = await _run(make_sync(), source)
    paths = {c.key: c.path for c in report.changes}
    assert _props(vault_dir, str(paths["jira:ABC-70"]))["source_estimate"] == 90
    assert "source_estimate" not in _props(vault_dir, str(paths["jira:ABC-71"]))
    again = FakeTasks("jira", [jira_task("ABC-70", estimate_minutes=30), jira_task("ABC-71")])
    changed = await _run(make_sync(), again)
    assert [c.fields.get("source_estimate") for c in changed.changes if c.key == "jira:ABC-70"] == [
        30
    ]


async def test_a_status_changed_in_obsidian_is_kept_until_the_source_moves(
    make_sync: Callable[..., VaultSync], vault_dir: Path
) -> None:
    working = jira_task("ABC-80", status="in-progress", source_status="In Progress")
    first = await _run(make_sync(), FakeTasks("jira", [working]))
    path = next(c.path for c in first.changes if c.key == "jira:ABC-80")
    assert path is not None
    note = vault_dir / path
    note.write_text(
        note.read_text().replace("status: in-progress", "status: done\ncompletedDate: 2026-11-18"),
        encoding="utf-8",
    )

    kept = await _run(make_sync(), FakeTasks("jira", [working]))
    [change] = kept.changes
    assert (change.action, change.path) == ("skipped", path)
    assert change.note is not None
    assert change.note.startswith("Changed in Obsidian to done, but jira still says in-progress")
    assert (_props(vault_dir, path)["status"], _props(vault_dir, path)["completedDate"]) == (
        "done",
        "2026-11-18",
    )

    renamed = jira_task(
        "ABC-80", title="Renamed", status="in-progress", source_status="In Progress"
    )
    [change] = (await _run(make_sync(), FakeTasks("jira", [renamed]))).changes
    assert change.action == "updated"
    assert change.note is not None and "left as you set it" in change.note
    assert (_props(vault_dir, path)["title"], _props(vault_dir, path)["status"]) == (
        "Renamed",
        "done",
    )

    closed = jira_task("ABC-80", title="Renamed", status="done", done=True, source_status="Done")
    [change] = (await _run(make_sync(), FakeTasks("jira", [closed]))).changes
    assert change.note is None
    assert (_props(vault_dir, path)["status"], _props(vault_dir, path)["completedDate"]) == (
        "done",
        "2026-11-18",
    )


async def test_a_source_that_moved_wins_over_the_note(
    make_sync: Callable[..., VaultSync], vault_dir: Path
) -> None:
    _write(
        vault_dir,
        "TaskNotes/Tasks/ABC-81.md",
        "---\ntitle: Issue ABC-81\ntags: [task]\nstatus: done\ncompletedDate: 2026-11-18\n"
        "source_status: In Progress\njira: ABC-81\n---\n",
    )
    _write(
        vault_dir,
        "TaskNotes/Tasks/ABC-82.md",
        "---\ntitle: Issue ABC-82\ntags: [task]\nstatus: done\njira: ABC-82\n---\n",
    )
    source = FakeTasks(
        "jira",
        [
            jira_task("ABC-81", status="in-progress", source_status="In Review"),
            jira_task("ABC-82", status="in-progress", source_status="In Progress"),
        ],
    )
    report = await _run(make_sync(), source)
    assert all(change.note is None for change in report.changes)
    moved = _props(vault_dir, "TaskNotes/Tasks/ABC-81.md")
    assert (moved["status"], "completedDate" in moved) == ("in-progress", False)
    assert _props(vault_dir, "TaskNotes/Tasks/ABC-82.md")["status"] == "in-progress"
