"""Where dated notes go: date formats written as Obsidian writes them, and safe file names.

Obsidian's Daily notes plugin takes a folder and a date format in moment.js
syntax (``YYYY/MM/YYYY-MM-DD``), where a ``/`` in the format makes sub-folders.
The same syntax is used here for daily notes and meeting notes, so a format can
be copied between Obsidian's settings and the config. The folder is kept apart
from the format because a folder name's letters (the ``M`` of ``Meetings``)
would otherwise be read as date tokens.
"""

import datetime
import re

from productivity_agent_tools.errors import ConfigError

DEFAULT_DATE_FORMAT: str = "YYYY-MM-DD"

_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# The longest token first, so YYYY is not read as two YY.
_TOKEN = re.compile(
    r"\[[^\]]*\]|YYYY|YY|MMMM|MMM|MM|M|DDDD|DDD|DD|Do|D|dddd|ddd|d|GGGG|WW|W|[A-Za-z]|."
)
_UNSUPPORTED = frozenset("AaHhkmsSXxZzQEeBbCcFfIiJjLlNnOoPpRrTtUuVvwgyYG")
# Characters Obsidian does not allow in a file name, or that break a [[link]].
_NOT_IN_NAME = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]+')
_NOT_IN_SLUG = re.compile(r"[\W_]+")
DEFAULT_TITLE_LENGTH: int = 25
"""Characters of a title kept in a synced task note's file name."""


def _ordinal(day: int) -> str:
    """Write a day of the month as moment's ``Do`` does: 1st, 2nd, 3rd, 4th, 11th.

    Args:
        day: The day of the month.

    Returns:
        The day with its English ordinal suffix.
    """
    if 11 <= day % 100 <= 13:
        return f"{day}th"
    return f"{day}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th') }"


def _render(token: str, day: datetime.date) -> str:
    """Write one token of a date format for a day.

    Args:
        token: One token, a ``[literal]``, or one other character.
        day: The day.

    Returns:
        The token's text for that day.
    """
    iso = day.isocalendar()
    values = {
        "YYYY": f"{day.year:04d}",
        "YY": f"{day.year % 100:02d}",
        "MMMM": _MONTHS[day.month - 1],
        "MMM": _MONTHS[day.month - 1][:3],
        "MM": f"{day.month:02d}",
        "M": str(day.month),
        "DDDD": f"{day.timetuple().tm_yday:03d}",
        "DDD": str(day.timetuple().tm_yday),
        "DD": f"{day.day:02d}",
        "Do": _ordinal(day.day),
        "D": str(day.day),
        "dddd": _WEEKDAYS[day.weekday()],
        "ddd": _WEEKDAYS[day.weekday()][:3],
        "d": str(day.isoweekday() % 7),
        "GGGG": f"{iso.year:04d}",
        "WW": f"{iso.week:02d}",
        "W": str(iso.week),
    }
    if token.startswith("[") and token.endswith("]") and len(token) > 1:
        return token[1:-1]
    return values.get(token, token)


def check_format(date_format: str, subject: str) -> str:
    """Check that a date format uses only tokens that make sense for a day's note.

    Args:
        date_format: The format, in moment.js syntax.
        subject: What the format is, starting the error message, such as
            ``Config key 'obsidian.daily_format'``.

    Returns:
        The format, with leading and trailing slashes and spaces removed.

    Raises:
        ConfigError: If it is empty, uses a time or other unsupported token, or would
            leave its folder.
    """
    cleaned = date_format.strip().strip("/")
    tokens = _TOKEN.findall(cleaned)
    unsupported = sorted({t for t in tokens if t in _UNSUPPORTED})
    if not cleaned or unsupported:
        detail = (
            f" (it uses {', '.join(unsupported)}, which no daily format needs)"
            if unsupported
            else ""
        )
        raise ConfigError(
            f"{subject} must be a date format such as YYYY/MM/YYYY-MM-DD{detail}. "
            "Use YYYY, YY, MMMM, MMM, MM, M, DDDD, DDD, DD, Do, D, dddd, ddd, d, GGGG, WW, "
            "W, and [text] for literal text."
        )
    rendered = format_date(cleaned, datetime.date(2000, 1, 1))
    if rendered.startswith("/") or ".." in rendered.split("/"):
        raise ConfigError(f"{subject} must stay inside its folder.")
    return cleaned


def format_date(date_format: str, day: datetime.date) -> str:
    """Write a day in a moment.js date format.

    Args:
        date_format: The format, such as ``YYYY/MM/YYYY-MM-DD``.
        day: The day.

    Returns:
        The formatted text; each ``/`` in it is a folder.
    """
    return "".join(_render(token, day) for token in _TOKEN.findall(date_format))


def safe_name(title: str) -> str:
    """Make a title usable as an Obsidian file name.

    Characters Obsidian refuses in a file name, or that would break a ``[[link]]``
    to it (slashes, colons, brackets, ``#``, ``|`` and the like), become a space; runs
    of spaces become one; leading dots are dropped so the note is not hidden.

    Args:
        title: The title.

    Returns:
        The file name, without ``.md``; ``Untitled`` when nothing is left.
    """
    name = " ".join(_NOT_IN_NAME.sub(" ", title).split()).lstrip(".").strip()
    return name[:150].rstrip() or "Untitled"


def slugify(text: str, length: int) -> str:
    """Shorten a title for a file name: lower case, words joined by hyphens.

    Args:
        text: The title.
        length: Most characters to keep; a cut inside a word goes back to the word
            before, unless that would leave nothing.

    Returns:
        The slug, such as ``review-il5-ansible-and``; empty when ``length`` is 0 or
        the title has no letters or digits.
    """
    slug = _NOT_IN_SLUG.sub("-", text.lower()).strip("-")
    if length <= 0 or not slug:
        return ""
    if len(slug) <= length:
        return slug
    cut = slug[:length]
    if slug[length] != "-" and "-" in cut:
        cut = cut[: cut.rindex("-")]
    return cut.strip("-")


def keyed_name(key: str, title: str, length: int) -> str:
    """Name a task note that comes from another system: its key, then a short title.

    Args:
        key: The task's key, such as ``ABC-123`` or ``PROJ/repo#42``.
        title: The task's title.
        length: Most characters of the title to keep (0 for the key alone).

    Returns:
        The file name without ``.md``, such as ``ELK-928 review-il5-ansible-and`` or
        ``PROJ-web-42 review-change-42``.
    """
    safe_key = _NOT_IN_NAME.sub("-", key).replace(" ", "-").strip("-. ") or "task"
    slug = slugify(title, length)
    return f"{safe_key} {slug}" if slug else safe_key


def note_path(folder: str, date_format: str, day: datetime.date, title: str | None = None) -> str:
    """Build a dated note's path relative to the vault.

    Args:
        folder: The folder, relative to the vault; empty for its top.
        date_format: The date format, in moment.js syntax.
        day: The note's day.
        title: For a meeting, its title, written after the date and a space.

    Returns:
        The path, ending in ``.md``.
    """
    stem = format_date(date_format, day)
    if title is not None:
        stem = f"{stem} {safe_name(title)}"
    return f"{folder}/{stem}.md" if folder else f"{stem}.md"
