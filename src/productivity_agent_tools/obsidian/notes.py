"""Changing a note's text safely: its properties, a marked section, and the file itself.

Properties are changed with ruamel's round-trip mode, so the user's ordering,
comments and quoting survive; only the keys asked for change. A section is the
text between two HTML comments that Obsidian does not show:

    <!-- productivity-agent-tools:plan:start -->
    ...
    <!-- productivity-agent-tools:plan:end -->

Writing it again replaces what is between them, so running the same step twice
never adds a second copy. Files are written to a temporary file and renamed into
place, and a note changed since it was read (in Obsidian, say) is not overwritten.
"""

import contextlib
import datetime
import os
import re
import uuid
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError

from productivity_agent_tools.errors import AgentToolsError
from productivity_agent_tools.obsidian.frontmatter import split

MARKER: str = "productivity-agent-tools"
_SECTION_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")

type Property = str | int | float | bool | list[str] | datetime.date | None
"""A value a property may be set to; None removes the property."""


class ChangedOnDiskError(AgentToolsError):
    """A note changed between being read and being written, so it was left alone."""


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A note's text as read, with what is needed to tell whether it changed since.

    Attributes:
        text: The note's full text.
        stamp: Its modification time (ns) and size when read; None for a note that does
            not exist yet.
    """

    text: str
    stamp: tuple[int, int] | None


def _yaml() -> YAML:
    """A round-trip YAML reader and writer that keeps long lines whole.

    Returns:
        The YAML instance.
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def _value(value: Property) -> object:
    """Prepare a value for YAML: a ``YYYY-MM-DD`` text becomes a date, written unquoted.

    Args:
        value: The value.

    Returns:
        What to store.
    """
    if isinstance(value, str) and _ISO_DAY.fullmatch(value):
        return datetime.date.fromisoformat(value)
    return value


def set_properties(text: str, changes: dict[str, Property]) -> str:
    """Set or remove properties in a note's frontmatter, keeping everything else.

    Args:
        text: The note's full text.
        changes: Properties to set, in the order new ones should be added; a value of
            None removes that property.

    Returns:
        The note's new text. A note with no frontmatter gains one.

    Raises:
        AgentToolsError: If the existing frontmatter is not valid YAML or not a mapping.
    """
    block, body = split(text)
    yaml = _yaml()
    try:
        loaded = yaml.load(block) if block and block.strip() else None
    except YAMLError as exc:
        raise AgentToolsError(
            "The note's properties are not valid YAML, so they were not changed. Fix them "
            "in Obsidian first."
        ) from exc
    properties = CommentedMap() if loaded is None else loaded
    if not isinstance(properties, CommentedMap):
        raise AgentToolsError("The note's properties are not a list of properties.")
    for key, value in changes.items():
        if value is None:
            properties.pop(key, None)
        else:
            properties[key] = _value(value)
    stream = StringIO()
    if properties:
        yaml.dump(properties, stream)
    rendered = stream.getvalue()
    return f"---\n{rendered}---\n{body}"


def check_section_name(name: str) -> str:
    """Check a section's name: lower-case letters, digits and hyphens.

    Args:
        name: The name, such as ``plan``.

    Returns:
        The name, trimmed.

    Raises:
        AgentToolsError: If it is not a usable name.
    """
    cleaned = name.strip()
    if not _SECTION_NAME.fullmatch(cleaned):
        raise AgentToolsError(
            "A section name is up to 40 lower-case letters, digits and hyphens, such as plan."
        )
    return cleaned


def _markers(name: str) -> tuple[str, str]:
    """The start and end comments of a section.

    Args:
        name: The section's name.

    Returns:
        The two comment lines.
    """
    return f"<!-- {MARKER}:{name}:start -->", f"<!-- {MARKER}:{name}:end -->"


def replace_section(text: str, name: str, markdown: str) -> tuple[str, bool]:
    """Put Markdown between a section's markers, adding the section at the end if absent.

    Args:
        text: The note's full text.
        name: The section's name.
        markdown: What the section should hold.

    Returns:
        The new text, and True if the section existed and was replaced.

    Raises:
        AgentToolsError: If the markers are out of order or appear more than once.
    """
    start, end = _markers(check_section_name(name))
    content = markdown.strip("\n")
    block = f"{start}\n{content}\n{end}" if content else f"{start}\n{end}"
    if start not in text and end not in text:
        stem = text.rstrip("\n")
        return (f"{stem}\n\n{block}\n" if stem else f"{block}\n"), False
    if text.count(start) != 1 or text.count(end) != 1 or text.index(start) > text.index(end):
        raise AgentToolsError(
            f"The note's {name} section markers are damaged (missing, repeated or out of "
            "order), so it was not changed. Fix or remove them in Obsidian."
        )
    before = text[: text.index(start)]
    after = text[text.index(end) + len(end) :]
    return f"{before}{block}{after}", True


def free_path(path: Path) -> Path:
    """Find a file name not yet taken: the one given, else with `` 2``, `` 3`` ... added.

    Args:
        path: The wanted file, ending in ``.md``.

    Returns:
        A path that does not exist.
    """
    candidate = path
    number = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} {number}{path.suffix}")
        number += 1
    return candidate


def read(path: Path) -> Snapshot:
    """Read a note and remember its state, to check before writing it back.

    Args:
        path: The note's file.

    Returns:
        The text and stamp; empty text and no stamp when the file does not exist.

    Raises:
        AgentToolsError: If it exists but cannot be read.
    """
    try:
        info = path.stat()
        return Snapshot(path.read_text(encoding="utf-8"), (info.st_mtime_ns, info.st_size))
    except FileNotFoundError, NotADirectoryError:
        return Snapshot("", None)
    except (OSError, UnicodeDecodeError) as exc:
        raise AgentToolsError(f"{path.name} could not be read: {exc}.") from exc


def write(path: Path, text: str, snapshot: Snapshot) -> None:
    """Write a note through a temporary file, unless it changed since it was read.

    Args:
        path: The note's file.
        text: Its new text.
        snapshot: What was read; a missing stamp means the note must still not exist.

    Raises:
        ChangedOnDiskError: If the note changed, appeared or vanished since it was read.
        AgentToolsError: If it cannot be written.
    """
    try:
        info = path.stat()
        now: tuple[int, int] | None = (info.st_mtime_ns, info.st_size)
    except FileNotFoundError, NotADirectoryError:
        now = None
    if now != snapshot.stamp:
        raise ChangedOnDiskError(
            f"{path.name} changed since it was read (edited in Obsidian, perhaps), so it "
            "was left alone. Read it again and repeat the change."
        )
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise AgentToolsError(f"{path.name} could not be written: {exc.strerror}.") from exc
