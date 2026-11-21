"""Where the vault's dated notes go: daily notes and meeting notes.

A daily note's place comes first from the config, then from Obsidian's own Daily
notes settings (``.obsidian/daily-notes.json``), then Obsidian's default (the
vault's top, ``YYYY-MM-DD``). Following Obsidian matters: a daily note written
anywhere else is a second note for the same day, not the one Obsidian opens.
"""

import datetime
import json
from dataclasses import dataclass
from pathlib import Path

from productivity_agent_tools.errors import AgentToolsError, ConfigError
from productivity_agent_tools.obsidian.paths import DEFAULT_DATE_FORMAT, check_format, note_path
from productivity_agent_tools.obsidian.settings import ObsidianSettings
from productivity_agent_tools.timezones import local_zone, zone_label

DAILY_NOTES_FILE: str = ".obsidian/daily-notes.json"

FROM_CONFIG: str = "the [obsidian] config"
FROM_OBSIDIAN: str = "Obsidian's Daily notes settings"
FROM_DEFAULT: str = "Obsidian's defaults"


@dataclass(frozen=True, slots=True)
class DailyLayout:
    """Where daily notes go, and where that was decided.

    Attributes:
        folder: Folder relative to the vault; empty for its top.
        date_format: Date format inside the folder, in moment.js syntax.
        source: Where the folder and format came from (config, Obsidian, defaults; a
            mix is joined with "and").
        obsidian: Obsidian's own folder and format, when its settings file has them;
            None when it has no Daily notes settings.
    """

    folder: str
    date_format: str
    source: str
    obsidian: tuple[str, str] | None

    @property
    def pattern(self) -> str:
        """The layout as one path, such as ``Journal/YYYY/MM/YYYY-MM-DD``."""
        return f"{self.folder}/{self.date_format}" if self.folder else self.date_format

    @property
    def differs_from_obsidian(self) -> bool:
        """True when the config puts daily notes somewhere Obsidian's settings do not."""
        return self.obsidian is not None and self.obsidian != (self.folder, self.date_format)

    def path(self, day: datetime.date) -> str:
        """The daily note's path for a day.

        Args:
            day: The day.

        Returns:
            Its path relative to the vault.
        """
        return note_path(self.folder, self.date_format, day)


def _obsidian_daily(root: Path) -> tuple[str, str] | None:
    """Read the folder and format from Obsidian's Daily notes settings.

    Args:
        root: The vault's folder.

    Returns:
        The folder and format (Obsidian's defaults for any it leaves out), or None when
        the settings file is absent or unreadable.
    """
    try:
        settings = json.loads((root / DAILY_NOTES_FILE).read_text(encoding="utf-8"))
    except OSError, UnicodeDecodeError, json.JSONDecodeError:
        return None
    if not isinstance(settings, dict):
        return None
    folder = settings.get("folder")
    date_format = settings.get("format")
    folder = folder.replace("\\", "/").strip().strip("/") if isinstance(folder, str) else ""
    date_format = date_format.strip() if isinstance(date_format, str) else ""
    return folder, date_format or DEFAULT_DATE_FORMAT


def daily_layout(settings: ObsidianSettings) -> DailyLayout:
    """Decide where daily notes go: the config, else Obsidian's settings, else its defaults.

    Args:
        settings: The ``[obsidian]`` settings.

    Returns:
        The layout.

    Raises:
        AgentToolsError: If Obsidian's own format uses a token this cannot write and
            the config does not replace it.
    """
    obsidian = _obsidian_daily(settings.vault)
    fallback = obsidian or ("", DEFAULT_DATE_FORMAT)
    fallback_source = FROM_OBSIDIAN if obsidian else FROM_DEFAULT
    folder = fallback[0] if settings.daily_folder is None else settings.daily_folder
    date_format = settings.daily_format or fallback[1]
    sources = [
        FROM_CONFIG if settings.daily_folder is not None else fallback_source,
        FROM_CONFIG if settings.daily_format else fallback_source,
    ]
    if not settings.daily_format:
        try:
            date_format = check_format(
                date_format, f"The date format in Obsidian's Daily notes settings ({date_format})"
            )
        except ConfigError as exc:
            raise AgentToolsError(
                f"{exc} Set daily_folder and daily_format in the [obsidian] table instead."
            ) from exc
    source = sources[0] if sources[0] == sources[1] else " and ".join(sources)
    return DailyLayout(folder, date_format, source, obsidian)


def meeting_pattern(settings: ObsidianSettings) -> str:
    """Show where meeting notes go, as one path with a placeholder for the title.

    Args:
        settings: The ``[obsidian]`` settings.

    Returns:
        The pattern, such as ``Meetings/YYYY/MM/YYYY-MM-DD <title>``.
    """
    stem = f"{settings.meeting_date_format} <title>"
    return f"{settings.meetings_folder}/{stem}" if settings.meetings_folder else stem


def meeting_path(settings: ObsidianSettings, day: datetime.date, title: str) -> str:
    """A meeting note's path.

    Args:
        settings: The ``[obsidian]`` settings.
        day: The meeting's day.
        title: The meeting's title.

    Returns:
        Its path relative to the vault.
    """
    return note_path(settings.meetings_folder, settings.meeting_date_format, day, title)


def meeting_example(settings: ObsidianSettings, day: datetime.date) -> str:
    """Show a meeting note's path for a day, with a placeholder for its title.

    Args:
        settings: The ``[obsidian]`` settings.
        day: The day.

    Returns:
        The path, such as ``Meetings/2026/11/2026-11-19 <title>.md``.
    """
    return (
        note_path(settings.meetings_folder, settings.meeting_date_format, day).removesuffix(".md")
        + " <title>.md"
    )


def zone(settings: ObsidianSettings) -> datetime.tzinfo:
    """The time zone that decides what day it is: the config's, else this computer's.

    Args:
        settings: The ``[obsidian]`` settings.

    Returns:
        The zone, looked up again on every call.
    """
    return settings.time_zone or local_zone()


def describe_zone(settings: ObsidianSettings) -> str:
    """Name the time zone in use and where it came from.

    Args:
        settings: The ``[obsidian]`` settings.

    Returns:
        Such as ``America/New_York (this computer's)``.
    """
    source = "the [obsidian] config" if settings.time_zone else "this computer's"
    return f"{zone_label(zone(settings))} ({source})"


def today(settings: ObsidianSettings) -> datetime.date:
    """The current day in the configured time zone, or this computer's.

    Args:
        settings: The ``[obsidian]`` settings.

    Returns:
        Today's date, from an aware current time in that zone.
    """
    return datetime.datetime.now(zone(settings)).date()


def parse_day(value: str | None, settings: ObsidianSettings) -> datetime.date:
    """Read a day argument: ``YYYY-MM-DD``, ``today``, ``yesterday`` or ``tomorrow``.

    Args:
        value: The argument; None means today.
        settings: The ``[obsidian]`` settings, for the time zone.

    Returns:
        The day.

    Raises:
        AgentToolsError: If it is none of those.
    """
    text = (value or "today").strip().lower()
    offsets = {"today": 0, "yesterday": -1, "tomorrow": 1}
    if text in offsets:
        return today(settings) + datetime.timedelta(days=offsets[text])
    try:
        return datetime.date.fromisoformat(text)
    except ValueError as exc:
        raise AgentToolsError(
            f"day must be YYYY-MM-DD, today, yesterday or tomorrow, got {value!r}."
        ) from exc
