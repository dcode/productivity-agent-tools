"""What one product can offer another without either importing the other.

A product's client implements one of these protocols; another product finds
it at runtime with ``ToolContext.peer``. Bitbucket, for instance, asks for an
``IssueTracker`` to describe the issues a pull request mentions, and does not
care whether Jira or something else answers.
"""

import datetime
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


@dataclass(frozen=True, slots=True)
class SourcedTask:
    """Something the user is responsible for in another system, as a task note shows it.

    Attributes:
        source: The property that holds its key in the vault, such as ``jira``.
        key: Its key there, such as ``ABC-123`` or ``PROJ/repo#42``.
        title: Its title.
        status: TaskNotes status: ``open``, ``in-progress`` or ``done``.
        done: True when it needs nothing more from the user.
        priority: TaskNotes priority (``low``, ``normal``, ``high``), or None.
        due: Due day, ``YYYY-MM-DD``, or None.
        url: Browser URL, or None.
        source_status: The status as that system names it, such as ``In Review``.
        estimate_minutes: Time still needed, as that system estimates it, or None.
    """

    source: str
    key: str
    title: str
    status: str
    done: bool
    priority: str | None = None
    due: str | None = None
    url: str | None = None
    source_status: str | None = None
    estimate_minutes: int | None = None


@runtime_checkable
class TaskSource(Protocol):
    """A client that knows what the user is responsible for, each with a lasting key."""

    @property
    def task_source(self) -> str:
        """The property its keys are kept under in the vault, such as ``jira``."""
        ...

    async def my_open_tasks(self) -> Sequence[SourcedTask]:
        """The tasks waiting on the user now.

        Returns:
            The open tasks.
        """
        ...

    async def tasks_by_key(self, keys: Sequence[str]) -> dict[str, SourcedTask]:
        """Look up tasks by key, to find out whether ones no longer open are finished.

        Args:
            keys: Keys as ``my_open_tasks`` gave them.

        Returns:
            The tasks found, by key; keys the source no longer knows are left out.
        """
        ...


@dataclass(frozen=True, slots=True)
class SourcedMeeting:
    """One meeting on the user's calendar, as a meeting note shows it.

    Attributes:
        ical_uid: The key that identifies this meeting (one occurrence of a series)
            across calendars and days, even when it moves.
        title: Its subject.
        start: When it starts, time-zone aware.
        end: When it ends, time-zone aware.
        series_id: The recurring series it belongs to, or None.
        all_day: True for an all-day event.
        cancelled: True if it was cancelled.
        response: The user's response: ``Accepted``, ``Declined``, ``Organizer`` ...
        show_as: How it shows the user's time: ``Busy``, ``Free``, ``Tentative`` ...
        organizer: The organizer's name.
        location: Where it is.
        join_url: Link to join online.
        url: Browser link to the event.
    """

    ical_uid: str
    title: str
    start: datetime.datetime
    end: datetime.datetime
    series_id: str | None = None
    all_day: bool = False
    cancelled: bool = False
    response: str | None = None
    show_as: str | None = None
    organizer: str | None = None
    location: str | None = None
    join_url: str | None = None
    url: str | None = None


@runtime_checkable
class MeetingSource(Protocol):
    """A client that can list the user's meetings with their lasting keys."""

    async def my_meetings(
        self, start: datetime.datetime, end: datetime.datetime
    ) -> Sequence[SourcedMeeting]:
        """The meetings that start in a time range, recurrences expanded.

        Args:
            start: Start of the range, time-zone aware.
            end: End of the range, time-zone aware.

        Returns:
            The meetings, earliest first.
        """
        ...
