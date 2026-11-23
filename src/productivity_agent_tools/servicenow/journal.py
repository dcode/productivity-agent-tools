"""Splitting a journal field (comments, work notes) into its entries.

Read with display values, a journal field is one text, newest entry first:

    2026-11-21 10:00:00 - Sam Desk (Additional comments)
    Looking into it.

    2026-11-20 09:00:00 - Pat Example (Additional comments)
    The VPN drops hourly.

Entries are found by their header lines, not split on blank lines, since a
comment can hold blank lines of its own.
"""

import re

from productivity_agent_tools.servicenow.models import Comment

_HEADER = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - (.+?) \(([^()\n]+)\)[ \t]*$", re.MULTILINE
)


def parse_journal(journal: str) -> list[Comment]:
    """Split a journal field into its entries.

    Args:
        journal: The field's display value.

    Returns:
        The entries, in the order given (newest first). Text before the first header,
        or a journal with no headers at all, becomes one entry with only a body.
    """
    headers = list(_HEADER.finditer(journal))
    entries: list[Comment] = []
    lead = journal[: headers[0].start() if headers else len(journal)].strip()
    if lead:
        entries.append(Comment(body=lead))
    for place, header in enumerate(headers):
        end = headers[place + 1].start() if place + 1 < len(headers) else len(journal)
        entries.append(
            Comment(
                at=header.group(1),
                author=header.group(2),
                kind=header.group(3),
                body=journal[header.end() : end].strip("\n").rstrip(),
            )
        )
    return entries
