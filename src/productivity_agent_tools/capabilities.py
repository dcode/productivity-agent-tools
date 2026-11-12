"""What one product can offer another without either importing the other.

A product's client implements one of these protocols; another product finds
it at runtime with ``ToolContext.peer``. Bitbucket, for instance, asks for an
``IssueTracker`` to describe the issues a pull request mentions, and does not
care whether Jira or something else answers.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class IssueDetails:
    """The few facts about an issue worth showing next to a mention of it.

    Attributes:
        key: Issue key, such as ``ABC-123``.
        url: Browser URL of the issue.
        summary: One-line summary.
        status: Workflow status name.
        assignee: Assignee's display name.
    """

    key: str
    url: str | None = None
    summary: str | None = None
    status: str | None = None
    assignee: str | None = None


@runtime_checkable
class IssueTracker(Protocol):
    """A client that can describe issues by key."""

    async def describe_issues(self, keys: Sequence[str]) -> dict[str, IssueDetails]:
        """Look up issues by key.

        Args:
            keys: Issue keys; malformed ones are ignored.

        Returns:
            The issues found, by key. Keys the tracker does not know are left out.
        """
        ...
