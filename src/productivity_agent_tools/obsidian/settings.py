"""Settings for the Obsidian product, read from the ``[obsidian]`` config table."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from productivity_agent_tools.config import (
    LocalProductSettings,
    as_str,
    dry_run_override,
    expand,
    max_results,
)
from productivity_agent_tools.errors import ConfigError
from productivity_agent_tools.obsidian.paths import (
    DEFAULT_DATE_FORMAT,
    DEFAULT_TITLE_LENGTH,
    check_format,
)

TABLE: str = "obsidian"


@dataclass(frozen=True, slots=True, kw_only=True)
class ObsidianSettings(LocalProductSettings):
    """Settings for the Obsidian vault, which is on only when ``[obsidian]`` is configured.

    Attributes:
        vault: The vault's folder, the one holding ``.obsidian/``.
        tasks_folder: Folder, relative to the vault, where new task notes go. Task notes
            are found anywhere in the vault by their tag.
        meetings_folder: Folder, relative to the vault, where meeting notes go.
        meeting_template: Note, relative to the vault, that new synced meeting notes start
            from, with ``{{title}}``, ``{{date}}`` and ``{{time}}`` filled in; None for the
            built-in layout.
        meeting_date_format: Date part of a meeting note's path inside ``meetings_folder``,
            in moment.js syntax; the note is named ``<date part> <title>``.
        daily_folder: Folder of the daily notes, or None to use Obsidian's Daily notes
            setting (``.obsidian/daily-notes.json``).
        daily_format: Date format of a daily note's path inside ``daily_folder``, or None
            to use Obsidian's Daily notes setting.
        time_zone: Time zone that decides what day "today" is, or None for this computer's
            (found from the system on every use; see ``timezones``).
        task_tag: Tag that marks a note as a task, as TaskNotes uses it (without ``#``).
        done_statuses: Task statuses that count as finished.
        default_max_results: Rows returned when a call names no limit.
        task_title_length: Characters of the title, as a slug, in the file name of a task
            note from another system (``ELK-928 review-il5-ansible-and.md``); 0 names it by
            its key alone.
        timeblock_minutes: Length of the time block planned for a task with no estimate
            (25, a pomodoro).
        max_timeblock_minutes: Longest time block planned for one task; a task with an
            estimate gets the smaller of its estimate and this.
        break_minutes: Break planned after a block of ``timeblock_minutes`` or less.
        long_break_minutes: Break planned after a longer block.
        sync_days_ahead: Days after today whose meetings ``obsidian_sync`` brings in.
        meeting_tag: Tag given to the meeting notes ``obsidian_sync`` makes.
    """

    vault: Path
    tasks_folder: str = "TaskNotes/Tasks"
    meetings_folder: str = "Meetings"
    meeting_date_format: str = DEFAULT_DATE_FORMAT
    meeting_template: str | None = None
    daily_folder: str | None = None
    daily_format: str | None = None
    time_zone: ZoneInfo | None = None
    task_tag: str = "task"
    done_statuses: tuple[str, ...] = ("done",)
    default_max_results: int = 50
    task_title_length: int = DEFAULT_TITLE_LENGTH
    timeblock_minutes: int = 25
    max_timeblock_minutes: int = 50
    break_minutes: int = 5
    long_break_minutes: int = 10
    sync_days_ahead: int = 1
    meeting_tag: str = "meeting"


def _folder(raw: dict[str, Any], key: str, default: str) -> str:
    """Read a folder inside the vault, as a relative POSIX path.

    Args:
        raw: The ``[obsidian]`` table.
        key: Key to read.
        default: Folder used when the key is absent.

    Returns:
        The folder with no leading or trailing slash; empty for the vault's top.

    Raises:
        ConfigError: If the value is not a string or would leave the vault.
    """
    value = (as_str(raw, key, default) or "").replace("\\", "/").strip().strip("/")
    if Path(value).is_absolute() or ".." in value.split("/"):
        raise ConfigError(f"Config key '{TABLE}.{key}' must be a folder inside the vault.")
    return value


def _date_format(raw: dict[str, Any], key: str) -> str | None:
    """Read an optional date format.

    Args:
        raw: The ``[obsidian]`` table.
        key: Key to read.

    Returns:
        The checked format, or None when the key is absent.

    Raises:
        ConfigError: If the format is not one a day's note can use.
    """
    value = as_str(raw, key, None)
    return None if value is None else check_format(value, f"Config key '{TABLE}.{key}'")


def _time_zone(raw: dict[str, Any]) -> ZoneInfo | None:
    """Read the optional time zone.

    Args:
        raw: The ``[obsidian]`` table.

    Returns:
        The time zone, or None for this computer's.

    Raises:
        ConfigError: If it is not a known IANA time zone name.
    """
    name = (as_str(raw, "time_zone", None) or "").strip()
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(
            f"Config key '{TABLE}.time_zone' must be a time zone name such as "
            f"America/New_York, got {name!r}."
        ) from exc


def _title_length(raw: dict[str, Any]) -> int:
    """Read how much of a synced task's title goes into its file name.

    Args:
        raw: The ``[obsidian]`` table.

    Returns:
        The number of characters, 0 to 100.

    Raises:
        ConfigError: If it is not a whole number from 0 to 100.
    """
    value = raw.get("task_title_length", DEFAULT_TITLE_LENGTH)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise ConfigError(
            f"Config key '{TABLE}.task_title_length' must be a whole number, 0 to 100."
        )
    return value


def _minutes(raw: dict[str, Any], key: str, default: int) -> int:
    """Read a time block length in minutes.

    Args:
        raw: The ``[obsidian]`` table.
        key: Key to read.
        default: Minutes used when the key is absent.

    Returns:
        The minutes, 5 to 480.

    Raises:
        ConfigError: If it is not a whole number from 5 to 480.
    """
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not 5 <= value <= 480:
        raise ConfigError(
            f"Config key '{TABLE}.{key}' must be a whole number of minutes, 5 to 480."
        )
    return value


def _break(raw: dict[str, Any], key: str, default: int) -> int:
    """Read a break length in minutes.

    Args:
        raw: The ``[obsidian]`` table.
        key: Key to read.
        default: Minutes used when the key is absent.

    Returns:
        The minutes, 0 to 60.

    Raises:
        ConfigError: If it is not a whole number from 0 to 60.
    """
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 60:
        raise ConfigError(f"Config key '{TABLE}.{key}' must be a whole number of minutes, 0 to 60.")
    return value


def _days_ahead(raw: dict[str, Any]) -> int:
    """Read how many days after today the sync looks for meetings.

    Args:
        raw: The ``[obsidian]`` table.

    Returns:
        The number of days, 0 to 14.

    Raises:
        ConfigError: If it is not a whole number from 0 to 14.
    """
    value = raw.get("sync_days_ahead", 1)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 14:
        raise ConfigError(f"Config key '{TABLE}.sync_days_ahead' must be a whole number, 0 to 14.")
    return value


def _statuses(raw: dict[str, Any]) -> tuple[str, ...]:
    """Read the statuses that count as finished.

    Args:
        raw: The ``[obsidian]`` table.

    Returns:
        The statuses, in the order written.

    Raises:
        ConfigError: If the value is not a non-empty list of strings.
    """
    value = raw.get("done_statuses", ["done"])
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item.strip() for item in value)
    ):
        raise ConfigError(
            f"Config key '{TABLE}.done_statuses' must be a list of statuses, e.g. [\"done\"]."
        )
    return tuple(item.strip() for item in value)


def parse_obsidian_settings(raw: dict[str, Any]) -> ObsidianSettings:
    """Build Obsidian settings from the ``[obsidian]`` table.

    Args:
        raw: Parsed ``[obsidian]`` table.

    Returns:
        The Obsidian settings.

    Raises:
        ConfigError: If ``vault`` is missing or a value is invalid.
    """
    vault = (as_str(raw, "vault", None) or "").strip()
    if not vault:
        raise ConfigError(
            f"Config key '{TABLE}.vault' is required: the folder of your Obsidian vault, "
            'e.g. vault = "~/Notes".'
        )
    tag = (as_str(raw, "task_tag", "task") or "").strip().lstrip("#")
    if not tag:
        raise ConfigError(f"Config key '{TABLE}.task_tag' must not be empty.")
    block = _minutes(raw, "timeblock_minutes", 25)
    longest = _minutes(raw, "max_timeblock_minutes", 50)
    if block > longest:
        raise ConfigError(
            f"Config key '{TABLE}.timeblock_minutes' ({block}) must not be more than "
            f"'{TABLE}.max_timeblock_minutes' ({longest})."
        )
    short_break = _break(raw, "break_minutes", 5)
    long_break = _break(raw, "long_break_minutes", 10)
    if short_break > long_break:
        raise ConfigError(
            f"Config key '{TABLE}.break_minutes' ({short_break}) must not be more than "
            f"'{TABLE}.long_break_minutes' ({long_break})."
        )
    return ObsidianSettings(
        vault=Path(expand(vault, f"{TABLE}.vault")),
        tasks_folder=_folder(raw, "tasks_folder", "TaskNotes/Tasks"),
        meetings_folder=_folder(raw, "meetings_folder", "Meetings"),
        meeting_date_format=_date_format(raw, "meeting_date_format") or DEFAULT_DATE_FORMAT,
        meeting_template=_folder(raw, "meeting_template", "") or None,
        daily_folder=_folder(raw, "daily_folder", "") if "daily_folder" in raw else None,
        daily_format=_date_format(raw, "daily_format"),
        time_zone=_time_zone(raw),
        task_tag=tag,
        done_statuses=_statuses(raw),
        default_max_results=max_results(raw, TABLE, 50),
        task_title_length=_title_length(raw),
        timeblock_minutes=block,
        max_timeblock_minutes=longest,
        break_minutes=short_break,
        long_break_minutes=long_break,
        sync_days_ahead=_days_ahead(raw),
        meeting_tag=(as_str(raw, "meeting_tag", "meeting") or "").strip().lstrip("#") or "meeting",
        dry_run=dry_run_override(raw, TABLE),
    )
