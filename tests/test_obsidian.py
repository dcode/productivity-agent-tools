"""The Obsidian vault product: settings, frontmatter, task notes, notes, tools and doctor."""

import datetime
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from productivity_agent_tools import cli
from productivity_agent_tools.config import parse_settings
from productivity_agent_tools.errors import AgentToolsError, ConfigError, NotFoundError
from productivity_agent_tools.obsidian import frontmatter
from productivity_agent_tools.obsidian.layout import describe_zone, today
from productivity_agent_tools.obsidian.product import PRODUCT, doctor, open_vault
from productivity_agent_tools.obsidian.settings import ObsidianSettings, parse_obsidian_settings
from productivity_agent_tools.obsidian.vault import Vault
from productivity_agent_tools.server import Opened, build_server

FIX_LOGIN = """---
title: Fix login timeout
tags: [task]
status: open
priority: high
due: 2026-11-20
scheduled: 2026-11-19
contexts: ["@office"]
projects: ["[[Platform]]"]
jira: ABC-123
---
Users are logged out after five minutes.
"""

WRITE_REPORT = """---
tags: task
status: done
due: 2026-11-10
---
"""

CALL_VENDOR = """---
title: Call the vendor about licences
tags: ["#task", call]
status: in-progress
scheduled: 2026-11-19T09:30
contexts: "@phone"
uid: "2026111901"
priority:
---
Ask about seat counts.
"""

NOT_A_TASK = """---
tags: [meeting]
title: Standup
---
Notes from standup.
"""


def _write(root: Path, path: str, text: str | bytes) -> None:
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        file.write_bytes(text)
    else:
        file.write_text(text, encoding="utf-8")


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    root = tmp_path / "Notes"
    (root / ".obsidian").mkdir(parents=True)
    _write(root, "TaskNotes/Tasks/ABC-123.md", FIX_LOGIN)
    _write(root, "TaskNotes/Tasks/Write report.md", WRITE_REPORT)
    _write(root, "Inbox/Call vendor.md", CALL_VENDOR)
    _write(root, "Meetings/Standup.md", NOT_A_TASK)
    _write(root, "Notes/Broken.md", "---\ntags: [task\n---\nbody\n")
    _write(root, "Notes/List.md", "---\n- a\n- b\n---\nbody\n")
    _write(root, "Notes/Unclosed.md", "---\ntitle: x\nno end\n")
    _write(root, "Notes/Empty.md", "---\n---\nJust text.\n")
    _write(root, "Notes/Plain.md", "﻿No properties here.\n")
    _write(root, "Notes/dup.md", "one\n")
    _write(root, "Other/dup.md", "two\n")
    _write(root, "Notes/latin.md", b"caf\xe9\n")
    _write(root, ".trash/Old task.md", "---\ntags: [task]\n---\n")
    _write(root, "Notes/.hidden.md", "---\ntags: [task]\n---\n")
    _write(root, "Notes/picture.png", b"\x89PNG")
    return root


@pytest.fixture
def make_vault(vault_dir: Path) -> Callable[..., Vault]:
    def build(dry_run: bool = True, **overrides: Any) -> Vault:
        return Vault(ObsidianSettings(vault=vault_dir, **overrides), dry_run)

    return build


# --- settings --------------------------------------------------------------------------


def test_settings_have_tasknotes_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/pat")
    settings = parse_obsidian_settings({"vault": "~/Notes"})
    assert settings.vault == Path("/home/pat/Notes")
    assert settings.tasks_folder == "TaskNotes/Tasks"
    assert settings.meetings_folder == "Meetings"
    assert (settings.task_tag, settings.done_statuses) == ("task", ("done",))
    assert (settings.default_max_results, settings.dry_run, settings.remote) == (50, None, False)


def test_settings_read_every_key() -> None:
    settings = parse_obsidian_settings(
        {
            "vault": "/v",
            "tasks_folder": "/Work\\Tasks/",
            "meetings_folder": "",
            "task_tag": "#todo",
            "done_statuses": ["done", " cancelled "],
            "default_max_results": 10,
            "dry_run": False,
        }
    )
    assert settings.tasks_folder == "Work/Tasks"
    assert settings.meetings_folder == ""
    assert settings.task_tag == "todo"
    assert settings.done_statuses == ("done", "cancelled")
    assert (settings.default_max_results, settings.dry_run) == (10, False)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({}, r"'obsidian\.vault' is required"),
        ({"vault": "  "}, r"'obsidian\.vault' is required"),
        ({"vault": "/v", "tasks_folder": "../out"}, "must be a folder inside the vault"),
        ({"vault": "/v", "task_tag": "#"}, "must not be empty"),
        ({"vault": "/v", "done_statuses": []}, "must be a list of statuses"),
        ({"vault": "/v", "done_statuses": "done"}, "must be a list of statuses"),
        ({"vault": "/v", "done_statuses": [" "]}, "must be a list of statuses"),
    ],
)
def test_bad_settings_are_explained(raw: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_obsidian_settings(raw)


def test_the_vault_is_configured_without_a_base_url(vault_dir: Path) -> None:
    settings = parse_settings({"obsidian": {"vault": str(vault_dir), "dry_run": False}})
    assert isinstance(settings.products["obsidian"], ObsidianSettings)
    assert settings.dry_run_for(settings.products["obsidian"]) is False


# --- frontmatter -----------------------------------------------------------------------


def test_split_finds_the_block_between_fences() -> None:
    assert frontmatter.split("---\na: 1\n...\nbody") == ("a: 1\n", "body")
    assert frontmatter.split("---  \n---\nbody") == ("", "body")
    assert frontmatter.split("no fence") == (None, "no fence")
    assert frontmatter.split("") == (None, "")
    assert frontmatter.split("---\nnever closed") == (None, "---\nnever closed")


def test_to_json_handles_every_yaml_shape() -> None:
    when = datetime.datetime(2026, 11, 19, 9, 30)
    value = {1: [datetime.date(2026, 11, 19), when, None, True, 1.5], "s": {"x"}}
    assert frontmatter.to_json(value) == {
        "1": ["2026-11-19", "2026-11-19T09:30:00", None, True, 1.5],
        "s": "{'x'}",
    }
    assert frontmatter.to_json(("a",)) == ["a"]


def test_parse_reports_broken_frontmatter_instead_of_failing() -> None:
    assert frontmatter.parse("---\ntags: [task\n---\nb").error is not None
    assert frontmatter.parse("---\ntags: [task\n---\nb").body == "b"
    assert frontmatter.parse("---\n- a\n---\n").error == "frontmatter is not a list of properties"
    assert frontmatter.parse("---\n---\nb") == frontmatter.ParsedNote(body="b")
    assert frontmatter.parse("---\nn: 1\n---\n").frontmatter == {"n": 1}


# --- the vault -------------------------------------------------------------------------


async def test_status_counts_notes_and_tasks(make_vault: Callable[..., Vault]) -> None:
    status = await make_vault(dry_run=False).status()
    assert status.is_vault is True
    assert (status.notes, status.tasks, status.open_tasks) == (11, 3, 2)
    assert status.dry_run is False
    problems = "\n".join(status.unreadable)
    assert "Notes/Broken.md: frontmatter is not valid YAML" in problems
    assert "Notes/List.md: frontmatter is not a list of properties" in problems
    assert "Notes/latin.md: UnicodeDecodeError: could not be read" in problems
    assert ".trash" not in problems


async def test_a_missing_vault_is_explained(tmp_path: Path) -> None:
    vault = Vault(ObsidianSettings(vault=tmp_path / "nope"), True)
    with pytest.raises(AgentToolsError, match=r"does not exist or is not a folder.*\[obsidian\]"):
        await vault.status()


async def test_tasks_carry_their_tasknotes_fields(make_vault: Callable[..., Vault]) -> None:
    listed = await make_vault().list_tasks(state="all")
    by_path = {task.path: task for task in listed.tasks}
    fix = by_path["TaskNotes/Tasks/ABC-123.md"]
    assert fix.title == "Fix login timeout"
    assert (fix.status, fix.done, fix.priority) == ("open", False, "high")
    assert (fix.due, fix.scheduled) == ("2026-11-20", "2026-11-19")
    assert (fix.contexts, fix.projects, fix.tags) == (["@office"], ["[[Platform]]"], ["task"])
    assert fix.keys == {"jira": "ABC-123"}
    report = by_path["TaskNotes/Tasks/Write report.md"]
    assert (report.title, report.done, report.contexts) == ("Write report", True, [])
    call = by_path["Inbox/Call vendor.md"]
    assert (call.tags, call.contexts, call.priority) == (["task", "call"], ["@phone"], None)
    assert call.keys == {"uid": "2026111901"}


async def test_list_tasks_filters_and_sorts(make_vault: Callable[..., Vault]) -> None:
    vault = make_vault()

    async def titles(**filters: Any) -> list[str]:
        return [task.title for task in (await vault.list_tasks(**filters)).tasks]

    assert await titles() == ["Call the vendor about licences", "Fix login timeout"]
    assert await titles(state="done") == ["Write report"]
    assert await titles(state="all") == [
        "Write report",
        "Call the vendor about licences",
        "Fix login timeout",
    ]
    assert await titles(scheduled_on="2026-11-19") == [
        "Call the vendor about licences",
        "Fix login timeout",
    ]
    assert await titles(state="all", due_by="2026-11-15") == ["Write report"]
    assert await titles(project="platform") == ["Fix login timeout"]
    assert await titles(context="PHONE") == ["Call the vendor about licences"]
    assert await titles(has_key=" jira ") == ["Fix login timeout"]
    limited = await vault.list_tasks(state="all", max_results=1)
    assert (limited.matched, limited.returned, limited.more_available) == (3, 1, True)
    with pytest.raises(AgentToolsError, match="due_by must be a date written YYYY-MM-DD"):
        await vault.list_tasks(due_by="tomorrow")


async def test_another_tag_and_done_status_can_be_configured(
    make_vault: Callable[..., Vault],
) -> None:
    vault = make_vault(task_tag="call", done_statuses=("in-progress",))
    listed = await vault.list_tasks(state="done")
    assert [task.title for task in listed.tasks] == ["Call the vendor about licences"]


async def test_find_task_by_key_and_by_title(make_vault: Callable[..., Vault]) -> None:
    vault = make_vault()
    found = await vault.find_tasks(key_field="jira", key="ABC-123", title="Fix login timeout")
    assert [task.path for task in found.exact] == ["TaskNotes/Tasks/ABC-123.md"]
    assert found.similar == []
    close = await vault.find_tasks(title="fix the login time-out!")
    assert found.exact[0] == close.similar[0].task
    assert 0.6 <= close.similar[0].score < 1
    report = await vault.find_tasks(title="Write report")
    assert report.similar[0].score == 1.0
    assert report.similar[0].task.done is True
    nothing = await vault.find_tasks(key_field="jira", key="ABC-999")
    assert (nothing.exact, nothing.similar) == ([], [])
    loose = await vault.find_tasks(title="report", min_score=0.0, max_results=2)
    assert len(loose.similar) == 2


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({}, "Give a key"),
        ({"title": "  !! "}, "Give a key"),
        ({"key_field": "jira"}, "Give key_field and key together"),
        ({"key": "ABC-1"}, "Give key_field and key together"),
    ],
)
async def test_find_task_needs_a_key_or_title(
    make_vault: Callable[..., Vault], arguments: dict[str, Any], message: str
) -> None:
    with pytest.raises(AgentToolsError, match=message):
        await make_vault().find_tasks(**arguments)


@pytest.mark.parametrize(
    "reference",
    [
        "TaskNotes/Tasks/ABC-123",
        "TaskNotes/Tasks/ABC-123.md",
        "[[ABC-123]]",
        "[[ABC-123|the login bug]]",
        "[[TaskNotes/Tasks/ABC-123#Notes]]",
        "TaskNotes\\Tasks\\ABC-123.md",
    ],
)
async def test_get_note_by_path_or_link(make_vault: Callable[..., Vault], reference: str) -> None:
    note = await make_vault().get_note(reference)
    assert note.path == "TaskNotes/Tasks/ABC-123.md"
    assert note.frontmatter["jira"] == "ABC-123"
    assert note.body == "Users are logged out after five minutes.\n"
    assert (note.truncated, note.frontmatter_error) == (False, None)


async def test_get_note_by_absolute_path_and_truncated(
    make_vault: Callable[..., Vault], vault_dir: Path
) -> None:
    note = await make_vault().get_note(str(vault_dir / "Inbox/Call vendor.md"), max_chars=4)
    assert (note.path, note.body, note.body_chars, note.truncated) == (
        "Inbox/Call vendor.md",
        "Ask ",
        23,
        True,
    )
    broken = await make_vault().get_note("Notes/Broken")
    assert broken.frontmatter == {}
    assert broken.frontmatter_error is not None


@pytest.mark.parametrize(
    ("reference", "error", "message"),
    [
        ("", AgentToolsError, "Name a note"),
        ("[[|x]]", AgentToolsError, "Name a note"),
        ("../outside", AgentToolsError, "outside the vault"),
        ("/etc/passwd", AgentToolsError, "outside the vault"),
        (".obsidian/app", AgentToolsError, "not a note this tool reads"),
        ("dup", AgentToolsError, "Several notes are named dup: Notes/dup.md, Other/dup.md"),
        ("Notes/latin", AgentToolsError, "could not be read"),
        ("Missing", NotFoundError, "No note Missing in the vault"),
        ("Notes/Missing", NotFoundError, "No note Notes/Missing in the vault"),
    ],
)
async def test_get_note_refuses_what_it_cannot_read(
    make_vault: Callable[..., Vault], reference: str, error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message):
        await make_vault().get_note(reference)


async def test_get_note_does_not_follow_a_link_out_of_the_vault(
    make_vault: Callable[..., Vault], vault_dir: Path, tmp_path: Path
) -> None:
    (tmp_path / "secret.md").write_text("secret")
    (vault_dir / "escape.md").symlink_to(tmp_path / "secret.md")
    with pytest.raises(AgentToolsError, match="outside the vault"):
        await make_vault().get_note("escape")


# --- tools, product and doctor ---------------------------------------------------------


def _text(result: object) -> str:
    return str(getattr(result, "structured_content", result))


async def test_the_tools_are_served(vault_dir: Path) -> None:
    settings = parse_settings({"obsidian": {"vault": str(vault_dir), "dry_run": False}})
    product_settings = settings.product("obsidian", ObsidianSettings)
    assert product_settings is not None
    async with open_vault(product_settings, False) as vault:
        server = build_server(settings, {"obsidian": Opened(vault)}, integrations=[])
    assert server.instructions == PRODUCT.instructions
    status = _text(await server.call_tool("obsidian_status", {}))
    assert "'tasks': 3" in status
    assert "'dry_run': False" in status
    listed = _text(await server.call_tool("obsidian_list_tasks", {"state": "done"}))
    assert "Write report" in listed
    found = _text(
        await server.call_tool("obsidian_find_task", {"key_field": "jira", "key": "ABC-123"})
    )
    assert "Fix login timeout" in found
    note = _text(await server.call_tool("obsidian_get_note", {"note": "[[Standup]]"}))
    assert "Notes from standup." in note
    with pytest.raises(ToolError, match="No note Nope in the vault"):
        await server.call_tool("obsidian_get_note", {"note": "Nope"})


async def test_doctor_checks_the_vault(make_vault: Callable[..., Vault], vault_dir: Path) -> None:
    vault = make_vault()
    day = today(vault.settings).isoformat()
    lines = [line async for line in doctor(vault)]
    assert lines == [
        f"vault {vault_dir} holds 11 note(s)",
        "3 task note(s) tagged #task, 2 not finished",
        "tasks folder TaskNotes/Tasks exists",
        f"today is {day} in {describe_zone(vault.settings)}",
        f"daily notes go to YYYY-MM-DD (Obsidian's defaults); today's, {day}.md, not written yet",
        f"meeting notes go to Meetings/{day} <title>.md",
        "3 note(s) have unreadable properties; see obsidian_status",
    ]
    lines = [line async for line in doctor(make_vault(tasks_folder="New"))]
    assert "tasks folder New will be created with the first new task" in lines
    lines = [line async for line in doctor(make_vault(tasks_folder=""))]
    assert "tasks folder (vault top) exists" in lines


async def test_doctor_needs_the_vaults_top_folder(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("x")
    with pytest.raises(ConfigError, match=r"has no \.obsidian folder"):
        [line async for line in doctor(Vault(ObsidianSettings(vault=tmp_path), True))]
    clean = tmp_path / "clean"
    (clean / ".obsidian").mkdir(parents=True)
    lines = [line async for line in doctor(Vault(ObsidianSettings(vault=clean), True))]
    assert not any("unreadable" in line for line in lines)


async def test_the_doctor_command_checks_the_vault(
    vault_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = parse_settings({"obsidian": {"vault": str(vault_dir), "dry_run": False}})
    assert await cli._doctor(Path("/cfg.toml"), settings) is True
    output = capsys.readouterr().out
    assert "[info] obsidian dry_run=False (its own setting)" in output
    assert "[ok]   3 task note(s) tagged #task, 2 not finished" in output


# --- reading only what changed ---------------------------------------------------------


def _count_parses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from productivity_agent_tools.obsidian import vault as vault_module

    parsed: list[str] = []
    real = vault_module.parse_properties

    def counting(block: str) -> frontmatter.ParsedNote:
        parsed.append(block)
        return real(block)

    monkeypatch.setattr(vault_module, "parse_properties", counting)
    return parsed


async def test_task_tools_parse_only_notes_that_can_be_tasks(
    make_vault: Callable[..., Vault], monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = _count_parses(monkeypatch)
    await make_vault().list_tasks(state="all")
    assert len(parsed) == 4
    assert not any("meeting" in block for block in parsed)


async def test_properties_are_parsed_once_until_the_note_changes(
    make_vault: Callable[..., Vault], vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = make_vault()
    parsed = _count_parses(monkeypatch)
    await vault.status()
    first = len(parsed)
    await vault.status()
    assert await vault.list_tasks(state="all")
    assert len(parsed) == first
    note = vault_dir / "TaskNotes/Tasks/ABC-123.md"
    note.write_text(FIX_LOGIN.replace("status: open", "status: done"), encoding="utf-8")
    listed = await vault.list_tasks(has_key="jira", state="all")
    assert [task.status for task in listed.tasks] == ["done"]
    assert len(parsed) == first + 1
    note.unlink()
    assert (await vault.list_tasks(has_key="jira", state="all")).tasks == []


async def test_a_tag_that_only_starts_like_the_task_tag_is_not_a_task(
    vault_dir: Path,
) -> None:
    _write(vault_dir, "Notes/Tasks list.md", "---\ntags: [tasks, my-task, task/sub]\n---\n")
    vault = Vault(ObsidianSettings(vault=vault_dir), True)
    paths = [task.path for task in (await vault.list_tasks(state="all")).tasks]
    assert "Notes/Tasks list.md" not in paths


async def test_get_note_reads_a_note_without_properties(make_vault: Callable[..., Vault]) -> None:
    note = await make_vault().get_note("Notes/Plain")
    assert (note.frontmatter, note.body) == ({}, "No properties here.\n")


async def test_get_note_by_key(make_vault: Callable[..., Vault], vault_dir: Path) -> None:
    _write(vault_dir, "Meetings/Sync.md", "---\nical_uid: m1\nbitbucket: P/r#4\n---\nAgenda\n")
    vault = make_vault()
    assert (await vault.get_note("ical_uid:m1")).path == "Meetings/Sync.md"
    assert (await vault.get_note(" bitbucket:P/r#4 ")).body == "Agenda\n"
    assert (await vault.get_note("jira:ABC-123")).path == "TaskNotes/Tasks/ABC-123.md"
    with pytest.raises(NotFoundError, match="No note has ical_uid: m2"):
        await vault.get_note("ical_uid:m2")
    _write(vault_dir, "Meetings/Copy.md", "---\nical_uid: m1\n---\n")
    with pytest.raises(AgentToolsError, match="Several notes have ical_uid:m1"):
        await vault.get_note("ical_uid:m1")


async def test_templates_are_not_notes_or_tasks(vault_dir: Path) -> None:
    import json

    from productivity_agent_tools.obsidian.vault import template_folders

    templater = "---\ntitle: <% tp.file.title %>\ntags: [task]\n---\n"
    _write(vault_dir, "Templates/Meeting Template.md", templater)
    _write(vault_dir, "Tpl/Task.md", "---\ntags: [task]\nstatus: open\n---\n")
    _write(vault_dir, ".obsidian/templates.json", json.dumps({"folder": "/Templates/"}))
    plugin = ".obsidian/plugins/templater-obsidian/data.json"
    _write(vault_dir, plugin, json.dumps({"templates_folder": "Tpl"}))
    assert template_folders(vault_dir) == ("Templates", "Tpl")
    status = await Vault(ObsidianSettings(vault=vault_dir), True).status()
    assert (status.notes, status.tasks) == (11, 3)
    assert not any("Templates/" in problem for problem in status.unreadable)
    note = await Vault(ObsidianSettings(vault=vault_dir), True).get_note("Tpl/Task")
    assert note.frontmatter["status"] == "open"

    _write(vault_dir, ".obsidian/templates.json", "not json")
    _write(vault_dir, plugin, json.dumps(["Tpl"]))
    assert template_folders(vault_dir) == ()
    _write(vault_dir, ".obsidian/templates.json", json.dumps({"folder": "Tpl"}))
    _write(vault_dir, plugin, json.dumps({"templates_folder": "Tpl/"}))
    assert template_folders(vault_dir) == ("Tpl",)


async def test_each_task_gets_a_time_block(vault_dir: Path) -> None:
    notes = {
        "plain": "",
        "due": "due: 2026-11-21\n",
        "short": "timeEstimate: 20\n",
        "long": "timeEstimate: 120\ndue: 2026-11-21\n",
        "jira": "source_estimate: 30\n",
        "both": "timeEstimate: 15\nsource_estimate: 45\n",
        "junk": "timeEstimate: soon\nsource_estimate: 0\n",
        "text": "timeEstimate: '40'\n",
        "flag": "timeEstimate: true\n",
    }
    for name, extra in notes.items():
        _write(vault_dir, f"Blocks/{name}.md", f"---\ntags: [task]\nstatus: open\n{extra}---\n")
    listed = await Vault(ObsidianSettings(vault=vault_dir), True).list_tasks(project=None)
    breaks = {Path(t.path).stem: t.break_minutes for t in listed.tasks}
    assert (breaks["plain"], breaks["short"], breaks["jira"], breaks["long"]) == (5, 5, 10, 10)
    blocks = {
        Path(t.path).stem: (t.estimate_minutes, t.block_minutes)
        for t in listed.tasks
        if t.path.startswith("Blocks/")
    }
    assert blocks == {
        "plain": (None, 25),
        "due": (None, 25),
        "short": (20, 20),
        "long": (120, 50),
        "jira": (30, 30),
        "both": (15, 15),
        "junk": (None, 25),
        "text": (40, 40),
        "flag": (None, 25),
    }
    custom = Vault(
        ObsidianSettings(vault=vault_dir, timeblock_minutes=30, max_timeblock_minutes=90), True
    )
    status = await custom.status()
    assert (status.timeblock_minutes, status.max_timeblock_minutes) == (30, 90)
    assert (status.break_minutes, status.long_break_minutes) == (5, 10)
    long_task = await custom.find_tasks(title="long", min_score=0.9)
    assert long_task.similar[0].task.block_minutes == 90
    due_only = await custom.find_tasks(title="due", min_score=0.9)
    assert due_only.similar[0].task.block_minutes == 30


def test_the_time_block_settings_are_checked() -> None:
    settings = parse_obsidian_settings({"vault": "/v"})
    assert (settings.timeblock_minutes, settings.max_timeblock_minutes) == (25, 50)
    custom = parse_obsidian_settings(
        {"vault": "/v", "timeblock_minutes": 30, "max_timeblock_minutes": 90}
    )
    assert (custom.timeblock_minutes, custom.max_timeblock_minutes) == (30, 90)
    for bad in (
        {"timeblock_minutes": 4},
        {"max_timeblock_minutes": 481},
        {"timeblock_minutes": True},
    ):
        with pytest.raises(ConfigError, match="whole number of minutes"):
            parse_obsidian_settings({"vault": "/v", **bad})
    with pytest.raises(ConfigError, match=r"timeblock_minutes' \(60\) must not be more than"):
        parse_obsidian_settings({"vault": "/v", "timeblock_minutes": 60})


def test_the_break_settings_are_checked() -> None:
    settings = parse_obsidian_settings({"vault": "/v"})
    assert (settings.break_minutes, settings.long_break_minutes) == (5, 10)
    custom = parse_obsidian_settings({"vault": "/v", "break_minutes": 0, "long_break_minutes": 15})
    assert (custom.break_minutes, custom.long_break_minutes) == (0, 15)
    for bad in ({"break_minutes": -1}, {"long_break_minutes": 61}, {"break_minutes": True}):
        with pytest.raises(ConfigError, match="whole number of minutes, 0 to 60"):
            parse_obsidian_settings({"vault": "/v", **bad})
    with pytest.raises(ConfigError, match=r"break_minutes' \(12\) must not be more than"):
        parse_obsidian_settings({"vault": "/v", "break_minutes": 12})
