"""Starting a daily note from the template Obsidian's Daily notes settings name.

A daily note this package creates should look like one Obsidian would have
created, so it starts from the template set in Daily notes, with the core
Templates plugin's variables filled in: ``{{title}}``, ``{{date}}``,
``{{date:FORMAT}}`` and ``{{time}}``. Templater's ``<% %>`` commands are left as
written; Templater fills them itself if it is set to run on new files.
"""

import datetime
import json
import re
from pathlib import Path

from productivity_agent_tools.errors import ConfigError
from productivity_agent_tools.obsidian.paths import DEFAULT_DATE_FORMAT, check_format, format_date

TEMPLATES_FILE: str = ".obsidian/templates.json"
_VARIABLE = re.compile(r"\{\{\s*(title|date|time)\s*(?::\s*([^}]*?)\s*)?\}\}", re.IGNORECASE)


def _settings(root: Path, name: str) -> dict[str, object]:
    """Read one of Obsidian's settings files.

    Args:
        root: The vault's folder.
        name: The file, relative to the vault.

    Returns:
        Its settings, or an empty mapping when it is absent or unreadable.
    """
    try:
        loaded = json.loads((root / name).read_text(encoding="utf-8"))
    except OSError, UnicodeDecodeError, json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def daily_template(root: Path) -> str | None:
    """Read the template Obsidian's Daily notes settings name, if any.

    Args:
        root: The vault's folder.

    Returns:
        The template's text, or None when none is set or it cannot be read.
    """
    name = _settings(root, ".obsidian/daily-notes.json").get("template")
    if not isinstance(name, str) or not name.strip():
        return None
    relative = name.strip().strip("/")
    path = root / (relative if relative.endswith(".md") else f"{relative}.md")
    try:
        if not path.resolve().is_relative_to(root.resolve()):
            return None
        return path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return None


def fill(template: str, root: Path, title: str, now: datetime.datetime) -> str:
    """Fill a template's ``{{title}}``, ``{{date}}`` and ``{{time}}`` as Obsidian would.

    ``{{date}}`` uses the Templates plugin's date format (``YYYY-MM-DD`` by default),
    ``{{date:FORMAT}}`` the format given, and ``{{time}}`` ``HH:mm``. A format this
    cannot write leaves the variable as it is.

    Args:
        template: The template's text.
        root: The vault's folder, for the Templates plugin's settings.
        title: The new note's title (its file name).
        now: The note's day and time.

    Returns:
        The filled text.
    """
    configured = _settings(root, TEMPLATES_FILE).get("dateFormat")
    default = configured if isinstance(configured, str) and configured else DEFAULT_DATE_FORMAT

    def replace(match: re.Match[str]) -> str:
        variable = match.group(1).lower()
        if variable == "title":
            return title
        if variable == "time":
            return now.strftime("%H:%M")
        try:
            date_format = check_format(match.group(2) or default, "template date")
        except ConfigError:
            return match.group(0)
        return format_date(date_format, now.date())

    return _VARIABLE.sub(replace, template)
