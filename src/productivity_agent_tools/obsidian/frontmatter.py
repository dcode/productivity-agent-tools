"""Reading the YAML frontmatter at the top of an Obsidian note.

A note's frontmatter is a YAML mapping between a first line of ``---`` and the
next line of ``---`` (or ``...``). Obsidian calls its entries properties.
"""

import datetime
from dataclasses import dataclass, field
from io import StringIO

from pydantic import JsonValue
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

_FENCE = "---"
_ENDS = frozenset({"---", "..."})


@dataclass(frozen=True, slots=True)
class ParsedNote:
    """A note split into its frontmatter and its body.

    Attributes:
        frontmatter: The properties, as JSON values; empty when the note has none or
            they could not be read.
        body: The text after the frontmatter (the whole text when there is none).
        error: Why the frontmatter could not be read, or None.
    """

    frontmatter: dict[str, JsonValue] = field(default_factory=dict)
    body: str = ""
    error: str | None = None


def split(text: str) -> tuple[str | None, str]:
    """Separate the frontmatter block from the body.

    Args:
        text: The note's full text.

    Returns:
        The YAML between the fences (None when the note has no frontmatter block) and
        the body after it.
    """
    text = text.removeprefix("﻿")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip() != _FENCE:
        return None, text
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip() in _ENDS:
            return "".join(lines[1:index]), "".join(lines[index + 1 :])
    return None, text


def to_json(value: object) -> JsonValue:
    """Turn a value YAML produced into a JSON value.

    Dates and times become ISO 8601 text; mapping keys become text; anything else
    unexpected becomes its text form.

    Args:
        value: A value from the YAML loader.

    Returns:
        The JSON value.
    """
    match value:
        case None | bool() | int() | float() | str():
            return value
        case datetime.date():
            return value.isoformat()
        case dict():
            return {str(key): to_json(item) for key, item in value.items()}
        case list() | tuple():
            return [to_json(item) for item in value]
    return str(value)


def parse_properties(block: str) -> ParsedNote:
    """Read a frontmatter block's properties, without the note's body.

    Args:
        block: The YAML between the fences, as ``split`` gives it.

    Returns:
        The properties (with an empty body). YAML that is not valid, or not a mapping,
        gives empty properties and an ``error``, never an exception.
    """
    try:
        loaded = YAML(typ="safe", pure=True).load(StringIO(block))
    except YAMLError as exc:
        problem = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        return ParsedNote(error=f"frontmatter is not valid YAML: {problem}")
    if loaded is None:
        return ParsedNote()
    if not isinstance(loaded, dict):
        return ParsedNote(error="frontmatter is not a list of properties")
    properties = to_json(loaded)
    assert isinstance(properties, dict)
    return ParsedNote(frontmatter=properties)


def parse(text: str) -> ParsedNote:
    """Read a note's frontmatter and body.

    Args:
        text: The note's full text.

    Returns:
        The parsed note. Frontmatter that is not valid YAML, or not a mapping, gives
        empty properties and an ``error``, never an exception.
    """
    block, body = split(text)
    if block is None:
        return ParsedNote(body=body)
    properties = parse_properties(block)
    return ParsedNote(frontmatter=properties.frontmatter, body=body, error=properties.error)
