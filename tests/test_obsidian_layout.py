"""Where dated notes go: date formats, file names, the daily-note layout and its tool."""

import datetime
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from productivity_agent_tools.config import parse_settings
from productivity_agent_tools.errors import AgentToolsError, ConfigError
from productivity_agent_tools.obsidian import layout
from productivity_agent_tools.obsidian import product as product_module
from productivity_agent_tools.obsidian import vault as vault_module
from productivity_agent_tools.obsidian.layout import (
    FROM_CONFIG,
    FROM_DEFAULT,
    FROM_OBSIDIAN,
    daily_layout,
    meeting_example,
    meeting_path,
    meeting_pattern,
    parse_day,
)
from productivity_agent_tools.obsidian.paths import check_format, format_date, note_path, safe_name
from productivity_agent_tools.obsidian.product import doctor
from productivity_agent_tools.obsidian.settings import ObsidianSettings, parse_obsidian_settings
from productivity_agent_tools.obsidian.vault import Vault
from productivity_agent_tools.server import Opened, build_server

DAY = datetime.date(2026, 11, 19)


# --- date formats and file names -------------------------------------------------------


@pytest.mark.parametrize(
    ("date_format", "expected"),
    [
        ("YYYY/MM/YYYY-MM-DD", "2026/11/2026-11-19"),
        ("YY M D", "26 11 19"),
        ("MMMM MMM", "November Nov"),
        ("DDDD DDD", "323 323"),
        ("Do", "19th"),
        ("dddd ddd d", "Thursday Thu 4"),
        ("GGGG-[W]WW W", "2026-W47 47"),
        ("[Daily] YYYY-MM-DD", "Daily 2026-11-19"),
        ("YYYY_MM_DD, -", "2026_11_19, -"),
        ("[x", "[x"),
    ],
)
def test_dates_are_written_as_obsidian_writes_them(date_format: str, expected: str) -> None:
    assert format_date(date_format, DAY) == expected


@pytest.mark.parametrize(
    ("day", "ordinal"),
    [
        (1, "1st"),
        (2, "2nd"),
        (3, "3rd"),
        (4, "4th"),
        (11, "11th"),
        (12, "12th"),
        (13, "13th"),
        (21, "21st"),
        (22, "22nd"),
        (23, "23rd"),
        (31, "31st"),
    ],
)
def test_ordinal_days(day: int, ordinal: str) -> None:
    assert format_date("Do", datetime.date(2026, 1, day)) == ordinal


def test_a_format_is_checked_and_trimmed() -> None:
    assert check_format(" /YYYY/MM/YYYY-MM-DD/ ", "k") == "YYYY/MM/YYYY-MM-DD"
    with pytest.raises(
        ConfigError,
        match=r"k must be a date format.*\(it uses H, m, which no daily format needs\)",
    ):
        check_format("YYYY-MM-DD HH:mm", "k")
    with pytest.raises(ConfigError, match=r"\(it uses Y, which no daily format needs\)"):
        check_format("Y", "k")
    with pytest.raises(ConfigError, match="k must be a date format such as"):
        check_format(" / ", "k")
    with pytest.raises(ConfigError, match="k must stay inside its folder"):
        check_format("[..]/YYYY", "k")


@pytest.mark.parametrize(
    ("title", "name"),
    [
        ("Weekly sync: Platform / Infra", "Weekly sync Platform Infra"),
        ('Q&A "ask me anything" #1 [draft]', "Q&A ask me anything 1 draft"),
        ("..hidden?", "hidden"),
        ("a|b^c*d<e>f\\g", "a b c d e f g"),
        ("  ", "Untitled"),
        ("x" * 200, "x" * 150),
    ],
)
def test_titles_become_safe_file_names(title: str, name: str) -> None:
    assert safe_name(title) == name


def test_note_paths() -> None:
    assert note_path("Journal", "YYYY/MM/YYYY-MM-DD", DAY) == "Journal/2026/11/2026-11-19.md"
    assert note_path("", "YYYY-MM-DD", DAY) == "2026-11-19.md"
    assert (
        note_path("Meetings", "YYYY/MM/YYYY-MM-DD", DAY, "Standup: team")
        == "Meetings/2026/11/2026-11-19 Standup team.md"
    )


# --- settings --------------------------------------------------------------------------


def test_the_users_layout_is_read_from_the_config() -> None:
    settings = parse_obsidian_settings(
        {
            "vault": "/v",
            "meeting_date_format": "YYYY/MM/YYYY-MM-DD",
            "daily_folder": "Journal/",
            "daily_format": "YYYY/MM/YYYY-MM-DD",
            "time_zone": "America/New_York",
        }
    )
    assert settings.meeting_date_format == "YYYY/MM/YYYY-MM-DD"
    assert (settings.daily_folder, settings.daily_format) == ("Journal", "YYYY/MM/YYYY-MM-DD")
    assert settings.time_zone == ZoneInfo("America/New_York")
    assert meeting_pattern(settings) == "Meetings/YYYY/MM/YYYY-MM-DD <title>"
    assert meeting_path(settings, DAY, "Standup") == "Meetings/2026/11/2026-11-19 Standup.md"
    assert meeting_example(settings, DAY) == "Meetings/2026/11/2026-11-19 <title>.md"


def test_layout_settings_default_to_following_obsidian() -> None:
    settings = parse_obsidian_settings({"vault": "/v", "daily_folder": "", "time_zone": " "})
    assert (settings.daily_folder, settings.daily_format, settings.time_zone) == ("", None, None)
    assert parse_obsidian_settings({"vault": "/v"}).daily_folder is None
    plain = ObsidianSettings(vault=Path("/v"), meetings_folder="")
    assert meeting_pattern(plain) == "YYYY-MM-DD <title>"


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"time_zone": "Mars/Olympus"}, r"'obsidian\.time_zone' must be a time zone name"),
        ({"time_zone": "../etc"}, r"'obsidian\.time_zone' must be a time zone name"),
        ({"daily_format": "YYYY-MM-DD HH"}, r"'obsidian\.daily_format' must be a date format"),
        ({"meeting_date_format": ""}, r"'obsidian\.meeting_date_format' must be a date format"),
        ({"daily_folder": "../x"}, r"'obsidian\.daily_folder' must be a folder inside"),
    ],
)
def test_bad_layout_settings_are_explained(raw: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_obsidian_settings({"vault": "/v", **raw})


# --- the daily-note layout -------------------------------------------------------------


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    root = tmp_path / "Notes"
    (root / ".obsidian").mkdir(parents=True)
    return root


def _obsidian_daily(root: Path, settings: object) -> None:
    (root / ".obsidian/daily-notes.json").write_text(json.dumps(settings), encoding="utf-8")


def test_the_layout_follows_obsidian_when_the_config_is_silent(vault_dir: Path) -> None:
    settings = ObsidianSettings(vault=vault_dir)
    assert daily_layout(settings).source == FROM_DEFAULT
    assert daily_layout(settings).pattern == "YYYY-MM-DD"
    _obsidian_daily(vault_dir, {"folder": "/Journal/", "format": "YYYY/MM/YYYY-MM-DD"})
    found = daily_layout(settings)
    assert (found.folder, found.date_format, found.source) == (
        "Journal",
        "YYYY/MM/YYYY-MM-DD",
        FROM_OBSIDIAN,
    )
    assert found.path(DAY) == "Journal/2026/11/2026-11-19.md"
    assert found.differs_from_obsidian is False
    _obsidian_daily(vault_dir, {"folder": 3, "format": ""})
    assert daily_layout(settings).pattern == "YYYY-MM-DD"


@pytest.mark.parametrize("content", ["not json", "[1, 2]"])
def test_unreadable_obsidian_settings_fall_back_to_defaults(vault_dir: Path, content: str) -> None:
    (vault_dir / ".obsidian/daily-notes.json").write_text(content, encoding="utf-8")
    assert daily_layout(ObsidianSettings(vault=vault_dir)).source == FROM_DEFAULT


def test_the_config_wins_and_a_mix_says_so(vault_dir: Path) -> None:
    _obsidian_daily(vault_dir, {"folder": "Daily", "format": "YYYY-MM-DD"})
    both = daily_layout(
        ObsidianSettings(vault=vault_dir, daily_folder="Journal", daily_format="YYYY/MM/YYYY-MM-DD")
    )
    assert (both.pattern, both.source) == ("Journal/YYYY/MM/YYYY-MM-DD", FROM_CONFIG)
    assert both.differs_from_obsidian is True
    mixed = daily_layout(ObsidianSettings(vault=vault_dir, daily_folder="Journal"))
    assert mixed.pattern == "Journal/YYYY-MM-DD"
    assert mixed.source == f"{FROM_CONFIG} and {FROM_OBSIDIAN}"


def test_an_obsidian_format_this_cannot_write_must_be_replaced(vault_dir: Path) -> None:
    _obsidian_daily(vault_dir, {"folder": "", "format": "YYYY-MM-DD HHmm"})
    with pytest.raises(
        AgentToolsError,
        match=r"Obsidian's Daily notes settings \(YYYY-MM-DD HHmm\) must be.*Set daily_folder",
    ):
        daily_layout(ObsidianSettings(vault=vault_dir))
    fixed = ObsidianSettings(vault=vault_dir, daily_format="YYYY-MM-DD")
    assert daily_layout(fixed).pattern == "YYYY-MM-DD"


def test_days_are_read_in_words_or_dates(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = ObsidianSettings(vault=Path("/v"))
    monkeypatch.setattr(layout, "today", lambda settings: DAY)
    assert parse_day(None, settings) == DAY
    assert parse_day(" Yesterday ", settings) == datetime.date(2026, 11, 18)
    assert parse_day("tomorrow", settings) == datetime.date(2026, 11, 20)
    assert parse_day("2026-01-02", settings) == datetime.date(2026, 1, 2)
    with pytest.raises(AgentToolsError, match="day must be YYYY-MM-DD, today, yesterday"):
        parse_day("next week", settings)


def test_today_follows_the_time_zone() -> None:
    east = layout.today(
        ObsidianSettings(vault=Path("/v"), time_zone=ZoneInfo("Pacific/Kiritimati"))
    )
    west = layout.today(ObsidianSettings(vault=Path("/v"), time_zone=ZoneInfo("Pacific/Niue")))
    assert (east - west).days in (1, 2)
    assert abs((layout.today(ObsidianSettings(vault=Path("/v"))) - west).days) <= 2


# --- the daily-note tool, status and doctor --------------------------------------------


@pytest.fixture
def journal(vault_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Vault]:
    monkeypatch.setattr(layout, "today", lambda settings: DAY)
    monkeypatch.setattr(vault_module, "today", lambda settings: DAY)
    monkeypatch.setattr(product_module, "today", lambda settings: DAY)
    _obsidian_daily(vault_dir, {"folder": "Journal", "format": "YYYY/MM/YYYY-MM-DD"})
    note = vault_dir / "Journal/2026/11/2026-11-18.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\nmood: fine\n---\n- [ ] Follow up with Sam\n", encoding="utf-8")

    def build(**overrides: Any) -> Vault:
        return Vault(ObsidianSettings(vault=vault_dir, **overrides), True)

    return build


async def test_the_daily_note_is_read_from_obsidians_place(journal: Callable[..., Vault]) -> None:
    yesterday = await journal().get_daily_note("yesterday")
    assert (yesterday.day, yesterday.path, yesterday.exists) == (
        "2026-11-18",
        "Journal/2026/11/2026-11-18.md",
        True,
    )
    assert yesterday.layout_from == FROM_OBSIDIAN
    assert yesterday.note is not None
    assert yesterday.note.body == "- [ ] Follow up with Sam\n"
    assert yesterday.note.frontmatter == {"mood": "fine"}
    todays = await journal().get_daily_note()
    assert (todays.path, todays.exists, todays.note) == (
        "Journal/2026/11/2026-11-19.md",
        False,
        None,
    )


async def test_status_reports_the_layout(journal: Callable[..., Vault]) -> None:
    status = await journal(
        meeting_date_format="YYYY/MM/YYYY-MM-DD", time_zone=ZoneInfo("Europe/Paris")
    ).status()
    assert status.time_zone == "Europe/Paris (the [obsidian] config)"
    assert status.daily_notes == "Journal/YYYY/MM/YYYY-MM-DD"
    assert status.daily_notes_from == FROM_OBSIDIAN
    assert status.meeting_notes == "Meetings/YYYY/MM/YYYY-MM-DD <title>"
    assert status.today == "2026-11-19"


async def test_the_daily_note_tool_is_served(
    vault_dir: Path, journal: Callable[..., Vault]
) -> None:
    settings = parse_settings({"obsidian": {"vault": str(vault_dir)}})
    server = build_server(settings, {"obsidian": Opened(journal())}, integrations=[])
    result = await server.call_tool("obsidian_get_daily_note", {"day": "2026-11-18"})
    assert "Follow up with Sam" in str(getattr(result, "structured_content", result))


async def test_doctor_shows_where_dated_notes_go(journal: Callable[..., Vault]) -> None:
    lines = [line async for line in doctor(journal(meeting_date_format="YYYY/MM/YYYY-MM-DD"))]
    assert (
        "daily notes go to Journal/YYYY/MM/YYYY-MM-DD (Obsidian's Daily notes settings); "
        "today's, Journal/2026/11/2026-11-19.md, not written yet"
    ) in lines
    assert "meeting notes go to Meetings/2026/11/2026-11-19 <title>.md" in lines
    same = journal(daily_folder="Journal", daily_format="YYYY/MM/YYYY-MM-DD")
    lines = [line async for line in doctor(same)]
    assert any("(the [obsidian] config)" in line for line in lines)


async def test_doctor_fails_when_the_config_and_obsidian_disagree(
    journal: Callable[..., Vault],
) -> None:
    vault = journal(daily_folder="Daily", daily_format="YYYY-MM-DD")
    seen: list[str] = []
    with pytest.raises(ConfigError, match=r"\(Daily/YYYY-MM-DD\) differ from.*\(Journal/YYYY/MM"):
        async for line in doctor(vault):
            seen.append(line)
    assert any(line.startswith("meeting notes go to") for line in seen)


async def test_doctor_names_obsidians_layout_at_the_vault_top(
    journal: Callable[..., Vault], vault_dir: Path
) -> None:
    _obsidian_daily(vault_dir, {"format": "YYYY-MM-DD"})
    with pytest.raises(ConfigError, match=r"Obsidian's Daily notes settings \(YYYY-MM-DD\)"):
        [line async for line in doctor(journal(daily_folder="Journal"))]


def test_the_time_zone_is_this_computers_unless_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(layout, "local_zone", lambda: ZoneInfo("Asia/Kolkata"))
    settings = ObsidianSettings(vault=Path("/v"))
    assert layout.zone(settings) == ZoneInfo("Asia/Kolkata")
    assert layout.describe_zone(settings) == "Asia/Kolkata (this computer's)"
    expected = datetime.datetime.now(ZoneInfo("Asia/Kolkata")).date()
    assert layout.today(settings) == expected
    configured = ObsidianSettings(vault=Path("/v"), time_zone=ZoneInfo("Europe/Paris"))
    assert layout.zone(configured) == ZoneInfo("Europe/Paris")


# --- synced task file names ------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "length", "slug"),
    [
        (
            "Review il5-ansible and related repositories and develop a plan",
            25,
            "review-il5-ansible-and",
        ),
        ("Update Collector's ansible deploy code", 25, "update-collector-s"),
        ("Short", 25, "short"),
        ("exactly-twenty-five-chars", 25, "exactly-twenty-five-chars"),
        ("ends at a word boundary here", 14, "ends-at-a-word"),
        ("Supercalifragilisticexpialidocious", 10, "supercalif"),
        ("  !!  ", 25, ""),
        ("Anything", 0, ""),
        ("Ünïcode Straße", 25, "ünïcode-straße"),
    ],
)
def test_titles_are_slugified_and_shortened(title: str, length: int, slug: str) -> None:
    from productivity_agent_tools.obsidian.paths import slugify

    assert slugify(title, length) == slug
    assert len(slugify(title, length)) <= max(length, 0)


@pytest.mark.parametrize(
    ("key", "title", "length", "name"),
    [
        (
            "ELK-928",
            "Review il5-ansible and related repositories",
            25,
            "ELK-928 review-il5-ansible-and",
        ),
        ("PROJ/web#42", "Review: Change 42", 25, "PROJ-web-42 review-change-42"),
        ("ELK-928", "Anything at all", 0, "ELK-928"),
        ("ELK-1", "!!!", 25, "ELK-1"),
        ("///", "Title", 25, "task title"),
    ],
)
def test_synced_tasks_are_named_by_key(key: str, title: str, length: int, name: str) -> None:
    from productivity_agent_tools.obsidian.paths import keyed_name

    assert keyed_name(key, title, length) == name


def test_the_title_length_is_configurable() -> None:
    assert parse_obsidian_settings({"vault": "/v"}).task_title_length == 25
    assert parse_obsidian_settings({"vault": "/v", "task_title_length": 0}).task_title_length == 0
    for bad in (-1, 101, True, "25"):
        with pytest.raises(ConfigError, match="task_title_length"):
            parse_obsidian_settings({"vault": "/v", "task_title_length": bad})
