"""Writing to the vault: properties, sections, safe writes, templates, and the write tools."""

import datetime
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from productivity_agent_tools.config import parse_settings
from productivity_agent_tools.errors import AgentToolsError, NotFoundError
from productivity_agent_tools.obsidian import edits as edits_module
from productivity_agent_tools.obsidian import layout, notes
from productivity_agent_tools.obsidian.edits import DRY_RUN, VaultEditor
from productivity_agent_tools.obsidian.frontmatter import parse
from productivity_agent_tools.obsidian.notes import (
    ChangedOnDiskError,
    Snapshot,
    replace_section,
    set_properties,
)
from productivity_agent_tools.obsidian.settings import ObsidianSettings
from productivity_agent_tools.obsidian.templates import daily_template, fill
from productivity_agent_tools.obsidian.vault import Vault
from productivity_agent_tools.server import Opened, build_server

DAY = datetime.date(2026, 11, 19)
NOW = datetime.datetime(2026, 11, 19, 9, 30, 15, tzinfo=datetime.UTC)


# --- properties ------------------------------------------------------------------------


def test_properties_keep_order_comments_and_quotes() -> None:
    text = "---\n# my note\ntitle: 'Quoted'\nstatus: open # still to do\nn: 1\n---\nBody\n"
    changed = set_properties(text, {"status": "done", "due": "2026-11-20", "n": None})
    assert changed == (
        "---\n# my note\ntitle: 'Quoted'\nstatus: done # still to do\ndue: 2026-11-20\n---\nBody\n"
    )
    assert parse(changed).frontmatter["due"] == "2026-11-20"


def test_properties_are_added_to_a_note_without_any() -> None:
    assert set_properties("Body\n", {"tags": ["task"], "when": "2026-11-19T09:30"}) == (
        "---\ntags:\n  - task\nwhen: 2026-11-19T09:30\n---\nBody\n"
    )
    assert set_properties("", {"a": 1}) == "---\na: 1\n---\n"
    assert set_properties("---\na: 1\n---\nx", {"a": None}) == "---\n---\nx"
    assert set_properties("---\n---\nx", {"b": True}) == "---\nb: true\n---\nx"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("---\na: [1\n---\n", "not valid YAML"),
        ("---\n- a\n---\n", "not a list of properties"),
    ],
)
def test_broken_properties_are_not_changed(text: str, message: str) -> None:
    with pytest.raises(AgentToolsError, match=message):
        set_properties(text, {"a": 2})


# --- sections --------------------------------------------------------------------------


def test_a_section_is_added_then_replaced() -> None:
    added, replaced = replace_section("# Day\n\nNotes.\n\n\n", "plan", "\n- one\n")
    assert replaced is False
    assert added == (
        "# Day\n\nNotes.\n\n<!-- productivity-agent-tools:plan:start -->\n- one\n"
        "<!-- productivity-agent-tools:plan:end -->\n"
    )
    again, replaced = replace_section(added + "After.\n", "plan", "- two")
    assert replaced is True
    assert again.count("plan:start") == 1
    assert "- two\n<!-- productivity-agent-tools:plan:end -->\nAfter.\n" in again
    assert "- one" not in again
    emptied, _ = replace_section(again, "plan", "")
    assert "plan:start -->\n<!-- productivity-agent-tools:plan:end" in emptied
    assert replace_section("", "plan", "x")[0].startswith("<!-- productivity")


@pytest.mark.parametrize(
    "text",
    [
        "<!-- productivity-agent-tools:plan:start -->\n",
        "<!-- productivity-agent-tools:plan:end -->\n<!-- productivity-agent-tools:plan:start -->",
        "<!-- productivity-agent-tools:plan:start -->\n<!-- productivity-agent-tools:plan:end -->"
        "\n<!-- productivity-agent-tools:plan:end -->",
    ],
)
def test_damaged_markers_are_left_alone(text: str) -> None:
    with pytest.raises(AgentToolsError, match="plan section markers are damaged"):
        replace_section(text, "plan", "x")


@pytest.mark.parametrize("name", ["", "Plan", "a b", "x" * 41, "-x"])
def test_section_names_are_checked(name: str) -> None:
    with pytest.raises(AgentToolsError, match="A section name is up to 40"):
        replace_section("", name, "x")


# --- safe writes -----------------------------------------------------------------------


def test_a_note_is_written_whole_and_only_if_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "a" / "b.md"
    snapshot = notes.read(path)
    assert snapshot == Snapshot("", None)
    notes.write(path, "one\n", snapshot)
    assert path.read_text() == "one\n"
    assert [p.name for p in path.parent.iterdir()] == ["b.md"]
    first = notes.read(path)
    notes.write(path, "two\n", first)
    with pytest.raises(ChangedOnDiskError, match="changed since it was read"):
        notes.write(path, "three\n", first)
    with pytest.raises(ChangedOnDiskError):
        notes.write(path, "new\n", Snapshot("", None))
    path.unlink()
    with pytest.raises(ChangedOnDiskError):
        notes.write(path, "gone\n", first)


def test_unreadable_and_unwritable_notes_are_explained(tmp_path: Path) -> None:
    bad = tmp_path / "bad.md"
    bad.write_bytes(b"\xff")
    with pytest.raises(AgentToolsError, match=r"bad\.md could not be read"):
        notes.read(bad)
    (tmp_path / "file").write_text("x")
    blocked = tmp_path / "file" / "note.md"
    assert notes.read(blocked) == Snapshot("", None)
    with pytest.raises(AgentToolsError, match=r"note\.md could not be written"):
        notes.write(blocked, "x", Snapshot("", None))


def test_a_failed_rename_leaves_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(source: object, target: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(notes.os, "replace", refuse)
    with pytest.raises(AgentToolsError, match="could not be written: Permission denied"):
        notes.write(tmp_path / "n.md", "x", Snapshot("", None))
    assert list(tmp_path.iterdir()) == []


# --- templates -------------------------------------------------------------------------


def _daily_settings(root: Path, **values: object) -> None:
    (root / ".obsidian").mkdir(parents=True, exist_ok=True)
    (root / ".obsidian/daily-notes.json").write_text(json.dumps(values), encoding="utf-8")


def test_the_daily_template_is_found(tmp_path: Path) -> None:
    assert daily_template(tmp_path) is None
    _daily_settings(tmp_path, template="Templates/Daily")
    assert daily_template(tmp_path) is None
    (tmp_path / "Templates").mkdir()
    (tmp_path / "Templates/Daily.md").write_text("# {{title}}\n")
    assert daily_template(tmp_path) == "# {{title}}\n"
    _daily_settings(tmp_path, template="Templates/Daily.md")
    assert daily_template(tmp_path) == "# {{title}}\n"
    _daily_settings(tmp_path, template="../outside")
    (tmp_path.parent / "outside.md").write_text("no")
    assert daily_template(tmp_path) is None
    _daily_settings(tmp_path, template=" ")
    assert daily_template(tmp_path) is None
    (tmp_path / ".obsidian/daily-notes.json").write_text("[]")
    assert daily_template(tmp_path) is None


def test_templates_are_filled_like_obsidian(tmp_path: Path) -> None:
    template = "{{title}} {{date}} {{ date:dddd, MMMM Do }} {{time}} {{DATE:YYYY}} {{date:HH}}"
    assert fill(template, tmp_path, "2026-11-19", NOW) == (
        "2026-11-19 2026-11-19 Thursday, November 19th 09:30 2026 {{date:HH}}"
    )
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian/templates.json").write_text(json.dumps({"dateFormat": "DD.MM.YYYY"}))
    assert fill("{{date}}", tmp_path, "t", NOW) == "19.11.2026"


# --- the editor ------------------------------------------------------------------------


EXISTING = """---
title: Fix login timeout
tags: [task]
status: open
priority: high
jira: ABC-123
---
Body stays.
"""

LOCAL = """---
title: Book the offsite venue
tags: [task]
status: open
uid: "20261101-ab12"
---
"""


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    root = tmp_path / "Notes"
    _daily_settings(root, folder="Journal", format="YYYY/MM/YYYY-MM-DD")
    tasks = root / "TaskNotes/Tasks"
    tasks.mkdir(parents=True)
    (tasks / "ABC-123.md").write_text(EXISTING, encoding="utf-8")
    (tasks / "Book the offsite venue.md").write_text(LOCAL, encoding="utf-8")
    (root / "Notes").mkdir()
    (root / "Notes/Plain.md").write_text("Just text.\n", encoding="utf-8")
    return root


@pytest.fixture
def make_editor(vault_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., VaultEditor]:
    monkeypatch.setattr(layout, "today", lambda settings: DAY)
    monkeypatch.setattr(VaultEditor, "_now", lambda self: NOW)

    def build(dry_run: bool = False, **overrides: Any) -> VaultEditor:
        return VaultEditor(Vault(ObsidianSettings(vault=vault_dir, **overrides), dry_run))

    return build


def test_a_task_is_created_with_tasknotes_fields(
    make_editor: Callable[..., VaultEditor], vault_dir: Path
) -> None:
    result = make_editor().create_task(
        title="  Review   PR: auth/login ",
        key_field="bitbucket",
        key="PROJ/web#42",
        priority="high",
        due="2026-11-21",
        scheduled="2026-11-19T14:00",
        contexts=["@desk", " "],
        projects=["[[Platform]]"],
        time_estimate=30,
        body="Check the token refresh.",
    )
    assert (result.created, result.written, result.dry_run) == (True, True, False)
    assert result.path == "TaskNotes/Tasks/PROJ-web-42 review-pr-auth-login.md"
    assert result.message == f"Created {result.path}."
    text = (vault_dir / result.path).read_text(encoding="utf-8")
    assert text == (
        "---\ntitle: 'Review PR: auth/login'\nstatus: open\npriority: high\ndue: 2026-11-21\n"
        "scheduled: 2026-11-19T14:00\ncontexts:\n  - '@desk'\nprojects:\n  - '[[Platform]]'\n"
        "timeEstimate: 30\ntags:\n  - task\ndateCreated: '2026-11-19T09:30:15+00:00'\n"
        "dateModified: '2026-11-19T09:30:15+00:00'\nbitbucket: PROJ/web#42\n---\n\n"
        "Check the token refresh.\n"
    )
    assert result.properties["due"] == "2026-11-21"


def test_a_task_with_an_existing_key_is_not_created_again(
    make_editor: Callable[..., VaultEditor], vault_dir: Path
) -> None:
    result = make_editor().create_task(title="Anything", key_field="jira", key="ABC-123")
    assert (result.created, result.written) == (False, False)
    assert result.existing is not None
    assert result.existing.path == "TaskNotes/Tasks/ABC-123.md"
    assert "already has jira: ABC-123" in result.message
    assert len(list((vault_dir / "TaskNotes/Tasks").iterdir())) == 2


def test_a_similar_open_task_holds_a_new_one_back(
    make_editor: Callable[..., VaultEditor], vault_dir: Path
) -> None:
    editor = make_editor()
    held = editor.create_task(title="Book the off-site venue")
    assert (held.created, held.written, held.path) == (False, False, None)
    assert [s.task.path for s in held.similar] == ["TaskNotes/Tasks/Book the offsite venue.md"]
    assert "allow_similar=true" in held.message
    made = editor.create_task(title="Book the off-site venue", allow_similar=True)
    assert made.path == "TaskNotes/Tasks/Book the off-site venue.md"
    properties = parse((vault_dir / made.path).read_text()).frontmatter
    assert properties["uid"] == "20261119093015-" + str(properties["uid"])[-4:]
    again = editor.create_task(title="Book the offsite venue", allow_similar=True)
    assert again.path == "TaskNotes/Tasks/Book the offsite venue 2.md"


def test_dry_run_describes_a_task_without_writing(
    make_editor: Callable[..., VaultEditor], vault_dir: Path
) -> None:
    result = make_editor(dry_run=True).create_task(title="Write the summary")
    assert (result.created, result.written, result.dry_run) == (True, False, True)
    assert result.message == DRY_RUN
    assert result.properties["title"] == "Write the summary"
    assert not (vault_dir / "TaskNotes/Tasks/Write the summary.md").exists()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"title": "  "}, "A task needs a title"),
        ({"title": "x", "key_field": "jira"}, "Give key_field and key together"),
        ({"title": "x", "key_field": "trello", "key": "1"}, "key_field must be one of"),
        ({"title": "x", "due": "Friday"}, "due must be YYYY-MM-DD"),
        ({"title": "x", "scheduled": "2026-13-01"}, "scheduled must be YYYY-MM-DD"),
    ],
)
def test_bad_task_arguments_are_explained(
    make_editor: Callable[..., VaultEditor], arguments: dict[str, Any], message: str
) -> None:
    with pytest.raises(AgentToolsError, match=message):
        make_editor().create_task(**arguments)


def test_a_new_task_folder_is_created(
    make_editor: Callable[..., VaultEditor], vault_dir: Path
) -> None:
    result = make_editor(tasks_folder="Inbox/New").create_task(title="Brand new thing")
    assert (vault_dir / "Inbox/New/Brand new thing.md").is_file()
    assert result.path == "Inbox/New/Brand new thing.md"


def test_a_task_is_scheduled_by_key_path_or_link(
    make_editor: Callable[..., VaultEditor], vault_dir: Path
) -> None:
    editor = make_editor()
    for reference in ("jira:ABC-123", "TaskNotes/Tasks/ABC-123", "[[ABC-123]]"):
        result = editor.update_task(task=reference, scheduled="2026-11-20", contexts=["@desk"])
        assert result.path == "TaskNotes/Tasks/ABC-123.md"
    text = (vault_dir / "TaskNotes/Tasks/ABC-123.md").read_text()
    assert "scheduled: 2026-11-20\n" in text
    assert "dateModified: '2026-11-19T09:30:15+00:00'\n" in text
    assert text.endswith("---\nBody stays.\n")
    cleared = editor.update_task(task="jira:ABC-123", clear=["scheduled"])
    assert cleared.properties["scheduled"] is None
    assert "scheduled" not in (vault_dir / "TaskNotes/Tasks/ABC-123.md").read_text()


def test_finishing_a_local_task_records_when(
    make_editor: Callable[..., VaultEditor], vault_dir: Path
) -> None:
    editor = make_editor()
    done = editor.update_task(task="uid:20261101-ab12", status="done", priority="low")
    assert done.properties["completedDate"] == "2026-11-19"
    path = vault_dir / "TaskNotes/Tasks/Book the offsite venue.md"
    assert "completedDate: 2026-11-19\n" in path.read_text()
    editor.update_task(task="uid:20261101-ab12", status="open")
    assert "completedDate" not in path.read_text()


def test_a_synced_tasks_own_fields_are_left_to_its_source(
    make_editor: Callable[..., VaultEditor],
) -> None:
    with pytest.raises(AgentToolsError, match=r"comes from jira \(ABC-123\).*status, due"):
        make_editor().update_task(task="jira:ABC-123", status="done", due="2026-12-01")


@pytest.mark.parametrize(
    ("task", "arguments", "error", "message"),
    [
        ("jira:ABC-999", {"scheduled": "2026-11-20"}, NotFoundError, "No task has jira: ABC-999"),
        ("Notes/Plain", {"scheduled": "2026-11-20"}, AgentToolsError, "is not a task note"),
        ("Missing", {"scheduled": "2026-11-20"}, NotFoundError, "No note Missing"),
        ("jira:ABC-123", {}, AgentToolsError, "Nothing to change"),
        ("jira:ABC-123", {"clear": ["title"]}, AgentToolsError, "clear takes status"),
    ],
)
def test_task_updates_that_cannot_happen(
    make_editor: Callable[..., VaultEditor],
    task: str,
    arguments: dict[str, Any],
    error: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error, match=message):
        make_editor().update_task(task=task, **arguments)


def test_a_key_on_several_tasks_must_be_named_by_path(
    make_editor: Callable[..., VaultEditor], vault_dir: Path
) -> None:
    (vault_dir / "TaskNotes/Tasks/Copy.md").write_text(EXISTING, encoding="utf-8")
    with pytest.raises(AgentToolsError, match="Several tasks have jira:ABC-123"):
        make_editor().update_task(task="jira:ABC-123", scheduled="2026-11-20")


def test_dry_run_and_a_concurrent_edit_leave_a_task_alone(
    make_editor: Callable[..., VaultEditor], vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = vault_dir / "TaskNotes/Tasks/ABC-123.md"
    dry = make_editor(dry_run=True).update_task(task="jira:ABC-123", scheduled="2026-11-20")
    assert (dry.written, dry.message) == (False, DRY_RUN)
    assert path.read_text() == EXISTING
    real_read = notes.read

    def read_then_edit(target: Path) -> Snapshot:
        snapshot = real_read(target)
        target.write_text(EXISTING + "Typed in Obsidian.\n", encoding="utf-8")
        os.utime(target, ns=(1, 1))
        return snapshot

    monkeypatch.setattr(edits_module.notes, "read", read_then_edit)
    with pytest.raises(ChangedOnDiskError):
        make_editor().update_task(task="jira:ABC-123", scheduled="2026-11-20")
    assert path.read_text().endswith("Typed in Obsidian.\n")


def test_todays_plan_starts_a_daily_note_from_the_template(
    make_editor: Callable[..., VaultEditor], vault_dir: Path
) -> None:
    _daily_settings(vault_dir, folder="Journal", format="YYYY/MM/YYYY-MM-DD", template="T/Day")
    (vault_dir / "T").mkdir()
    (vault_dir / "T/Day.md").write_text("# {{date:dddd D MMMM}}\n\n## Notes\n", encoding="utf-8")
    editor = make_editor()
    first = editor.write_section(section="plan", markdown="- [[ABC-123]]", day="today")
    assert (first.path, first.created_note, first.replaced, first.written) == (
        "Journal/2026/11/2026-11-19.md",
        True,
        False,
        True,
    )
    path = vault_dir / first.path
    assert path.read_text() == (
        "# Thursday 19 November\n\n## Notes\n\n<!-- productivity-agent-tools:plan:start -->\n"
        "- [[ABC-123]]\n<!-- productivity-agent-tools:plan:end -->\n"
    )
    second = editor.write_section(section="plan", markdown="- [[Other]]", day="2026-11-19")
    assert (second.created_note, second.replaced) == (False, True)
    assert path.read_text().count("plan:start") == 1
    assert "[[Other]]" in path.read_text()
    assert second.message == "Replaced the plan section of Journal/2026/11/2026-11-19.md."


def test_a_daily_note_without_a_template_starts_empty(
    make_editor: Callable[..., VaultEditor], vault_dir: Path
) -> None:
    result = make_editor().write_section(section="plan", markdown="x", day="tomorrow")
    assert result.message == "Added the plan section of Journal/2026/11/2026-11-20.md."
    assert (vault_dir / result.path).read_text().startswith("<!-- productivity-agent-tools")


def test_a_section_goes_into_an_existing_note(
    make_editor: Callable[..., VaultEditor], vault_dir: Path
) -> None:
    dry = make_editor(dry_run=True).write_section(section="prep", markdown="x", note="Plain")
    assert (dry.written, dry.message, dry.path) == (False, DRY_RUN, "Notes/Plain.md")
    assert (vault_dir / "Notes/Plain.md").read_text() == "Just text.\n"
    make_editor().write_section(section="prep", markdown="- Agenda", note="[[Plain]]")
    assert "- Agenda" in (vault_dir / "Notes/Plain.md").read_text()
    with pytest.raises(NotFoundError):
        make_editor().write_section(section="prep", markdown="x", note="Nope")


@pytest.mark.parametrize("arguments", [{}, {"day": "today", "note": "Plain"}])
def test_a_section_needs_one_place(
    make_editor: Callable[..., VaultEditor], arguments: dict[str, str]
) -> None:
    with pytest.raises(AgentToolsError, match="Name either a note or a day"):
        make_editor().write_section(section="plan", markdown="x", **arguments)


def test_the_editor_reports_dry_run_and_the_current_time(vault_dir: Path) -> None:
    editor = VaultEditor(Vault(ObsidianSettings(vault=vault_dir), True))
    assert editor.dry_run is True
    assert editor._now().tzinfo is not None
    assert editor._now().microsecond == 0


# --- the tools -------------------------------------------------------------------------


async def test_the_write_tools_are_served(
    make_editor: Callable[..., VaultEditor], vault_dir: Path
) -> None:
    settings = parse_settings({"obsidian": {"vault": str(vault_dir), "dry_run": False}})
    vault = make_editor().vault
    server = build_server(settings, {"obsidian": Opened(vault)}, integrations=[])

    async def call(name: str, arguments: dict[str, Any]) -> str:
        result = await server.call_tool(name, arguments)
        return str(getattr(result, "structured_content", result))

    assert "'created': True" in await call(
        "obsidian_create_task", {"title": "Prepare the demo", "scheduled": "2026-11-19"}
    )
    assert "'written': True" in await call(
        "obsidian_update_task", {"task": "jira:ABC-123", "scheduled": "2026-11-19"}
    )
    assert "'replaced': False" in await call(
        "obsidian_write_section", {"section": "plan", "markdown": "- x", "day": "today"}
    )
    with pytest.raises(ToolError, match="owns its status"):
        await call("obsidian_update_task", {"task": "jira:ABC-123", "status": "done"})
