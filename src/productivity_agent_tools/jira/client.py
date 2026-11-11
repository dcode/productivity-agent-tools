"""Jira Data Center REST client (API v2) built on an ``ApiClient`` adapter."""

import re
from datetime import date, datetime

from pydantic import JsonValue

from productivity_agent_tools.api import ApiClient, WriteOutcome
from productivity_agent_tools.config import JiraSettings
from productivity_agent_tools.errors import AtlassianError
from productivity_agent_tools.jira.models import (
    Assignee,
    Comment,
    IssueDetail,
    IssueSummary,
    SearchResult,
    Transition,
    Worklog,
    WorklogList,
)
from productivity_agent_tools.payload import JsonObject
from productivity_agent_tools.payload import as_object as _object
from productivity_agent_tools.payload import number as _number
from productivity_agent_tools.payload import objects as _objects
from productivity_agent_tools.payload import text as _text

_ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*$")
_SUMMARY_FIELDS: str = "summary,status,issuetype,priority,assignee,updated"
_DETAIL_FIELDS: str = _SUMMARY_FIELDS + ",description,reporter,created,labels,comment"
MAX_RESULTS_LIMIT: int = 100
# Jira's duration syntax: one or more of a number followed by w, d, h or m.
_DURATION = re.compile(r"^(\d+(\.\d+)?[wdhm])(\s+\d+(\.\d+)?[wdhm])*$")
_ME: frozenset[str] = frozenset({"me", "myself", "self", "current user", "currentuser()"})
_NOBODY: frozenset[str] = frozenset({"none", "nobody", "unassigned", "unassign", ""})
MAX_CANDIDATES: int = 10


def normalize_duration(time_spent: str) -> str:
    """Validate a duration in Jira's syntax and tidy its spacing.

    Args:
        time_spent: Such as ``1h 30m``, ``2d`` or ``45m``.

    Returns:
        The duration with single spaces and lower-case units.

    Raises:
        AtlassianError: If it is not in Jira's syntax.
    """
    cleaned = " ".join(time_spent.lower().split())
    cleaned = re.sub(r"(\d)\s+([wdhm])\b", r"\1\2", cleaned)
    if not _DURATION.fullmatch(cleaned):
        raise AtlassianError(
            f"'{time_spent}' is not a Jira duration. Use numbers with w, d, h or m, such as "
            "'1h 30m', '2d' or '45m'."
        )
    return cleaned


def jira_time(started: str) -> str:
    """Convert an ISO 8601 date or time to the format Jira's worklog API takes.

    A time without an offset is read as local time on this machine, which is
    the user's own laptop. A bare date means 09:00 local time.

    Args:
        started: Such as ``2026-11-11T14:30``, ``2026-11-11T14:30:00+01:00`` or ``2026-11-11``.

    Returns:
        Such as ``2026-11-11T14:30:00.000+0100``.

    Raises:
        AtlassianError: If it cannot be read.
    """
    text = started.strip()
    try:
        if "T" in text or " " in text:
            moment = datetime.fromisoformat(text)
        else:
            day = date.fromisoformat(text)
            moment = datetime(day.year, day.month, day.day, 9)
    except ValueError:
        raise AtlassianError(
            f"'{started}' is not an ISO 8601 date or time, such as 2026-11-11T14:30."
        ) from None
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000%z")


def normalize_issue_key(key: str) -> str:
    """Validate and normalize an issue key.

    Args:
        key: Issue key as given by the caller, such as ``proj-12``.

    Returns:
        The upper-cased key.

    Raises:
        AtlassianError: If the value is not shaped like an issue key.
    """
    normalized = key.strip().upper()
    if not _ISSUE_KEY.fullmatch(normalized):
        raise AtlassianError(f"'{key}' is not a valid issue key (expected something like ABC-123).")
    return normalized


class JiraClient:
    """Typed operations against one Jira Data Center instance."""

    def __init__(self, core: ApiClient, settings: JiraSettings) -> None:
        """Create the client.

        Args:
            core: API adapter connected to Jira's host.
            settings: Jira module settings.
        """
        self._core = core
        self._settings = settings
        self._api = f"{settings.path}/rest/api/2"

    @property
    def settings(self) -> JiraSettings:
        """The Jira module settings this client was built with."""
        return self._settings

    @property
    def base_url(self) -> str:
        """Browser base URL of this Jira instance."""
        return f"{self._core.base_url}{self._settings.path}"

    def _summary(self, issue: JsonObject) -> IssueSummary:
        """Build the compact view of an issue.

        Args:
            issue: One decoded issue object from the API.

        Returns:
            The issue summary.
        """
        key = _text(issue, "key") or ""
        fields = issue.get("fields")
        return IssueSummary(
            key=key,
            summary=_text(fields, "summary") or "",
            status=_text(fields, "status", "name") or "Unknown",
            issue_type=_text(fields, "issuetype", "name"),
            priority=_text(fields, "priority", "name"),
            assignee=_text(fields, "assignee", "displayName"),
            updated=_text(fields, "updated"),
            url=f"{self.base_url}/browse/{key}",
        )

    async def myself(self) -> JsonObject:
        """Fetch the authenticated user's profile.

        Returns:
            The decoded profile object.

        Raises:
            AtlassianError: If the request fails.
        """
        return _object(await self._core.get(f"{self._api}/myself"), "profile")

    async def server_version(self) -> str | None:
        """Fetch the Jira server version.

        Returns:
            The version string, or None if the server does not report one.

        Raises:
            AtlassianError: If the request fails.
        """
        return _text(await self._core.get(f"{self._api}/serverInfo"), "version")

    async def search(self, jql: str, max_results: int | None = None) -> SearchResult:
        """Run a JQL search and return compact rows.

        Args:
            jql: The JQL query.
            max_results: Row cap; defaults to the configured value, limited to 100.

        Returns:
            The matching issues and the server-side total.

        Raises:
            AtlassianError: If the query is invalid or the request fails.
        """
        limit = max(1, min(max_results or self._settings.default_max_results, MAX_RESULTS_LIMIT))
        body = _object(
            await self._core.get(
                f"{self._api}/search",
                params={"jql": jql, "maxResults": limit, "fields": _SUMMARY_FIELDS},
            ),
            "search",
        )
        issues = [self._summary(item) for item in _objects(body.get("issues"))]
        total = body.get("total")
        return SearchResult(
            jql=jql,
            total=total if isinstance(total, int) else len(issues),
            returned=len(issues),
            issues=issues,
        )

    async def get_issue(self, key: str, max_comments: int = 10) -> IssueDetail:
        """Fetch one issue with its description and most recent comments.

        Args:
            key: Issue key.
            max_comments: How many of the newest comments to include.

        Returns:
            The issue detail.

        Raises:
            AtlassianError: If the key is invalid or the request fails.
        """
        issue_key = normalize_issue_key(key)
        body = _object(
            await self._core.get(
                f"{self._api}/issue/{issue_key}", params={"fields": _DETAIL_FIELDS}
            ),
            "issue",
        )
        fields = body.get("fields")
        fields = fields if isinstance(fields, dict) else {}
        comment_block = fields.get("comment")
        all_comments = _objects(
            comment_block.get("comments") if isinstance(comment_block, dict) else None
        )
        newest = all_comments[-max_comments:] if max_comments > 0 else []
        labels = fields.get("labels")
        return IssueDetail(
            **self._summary(body).model_dump(),
            description=_text(fields, "description"),
            reporter=_text(fields, "reporter", "displayName"),
            created=_text(fields, "created"),
            labels=[str(label) for label in labels] if isinstance(labels, list) else [],
            comments=[
                Comment(
                    id=_text(c, "id") or "",
                    author=_text(c, "author", "displayName"),
                    created=_text(c, "created"),
                    body=_text(c, "body") or "",
                )
                for c in newest
            ],
            comment_total=len(all_comments),
        )

    async def list_transitions(self, key: str) -> list[Transition]:
        """List the workflow transitions currently available on an issue.

        Args:
            key: Issue key.

        Returns:
            The available transitions.

        Raises:
            AtlassianError: If the key is invalid or the request fails.
        """
        issue_key = normalize_issue_key(key)
        body = _object(
            await self._core.get(f"{self._api}/issue/{issue_key}/transitions"), "transitions"
        )
        return [
            Transition(
                id=_text(item, "id") or "",
                name=_text(item, "name") or "",
                to_status=_text(item, "to", "name"),
            )
            for item in _objects(body.get("transitions"))
        ]

    async def add_comment(self, key: str, body: str) -> WriteOutcome:
        """Add a comment to an issue (blocked when dry-run is on).

        Args:
            key: Issue key.
            body: Comment text in Jira wiki markup.

        Returns:
            The write outcome.

        Raises:
            AtlassianError: If the key or body is invalid, or the request fails.
        """
        issue_key = normalize_issue_key(key)
        if not body.strip():
            raise AtlassianError("Comment body must not be empty.")
        return await self._core.write(
            "POST", f"{self._api}/issue/{issue_key}/comment", {"body": body}
        )

    async def transition_issue(self, key: str, transition: str) -> tuple[Transition, WriteOutcome]:
        """Move an issue through a workflow transition (blocked when dry-run is on).

        The transition is resolved against those currently available, even in
        dry-run, so an invalid name is reported either way.

        Args:
            key: Issue key.
            transition: Transition name (case-insensitive) or identifier.

        Returns:
            The resolved transition and the write outcome.

        Raises:
            AtlassianError: If the transition is not available or the request fails.
        """
        issue_key = normalize_issue_key(key)
        available = await self.list_transitions(issue_key)
        wanted = transition.strip().casefold()
        match = next(
            (t for t in available if t.id == transition.strip() or t.name.casefold() == wanted),
            None,
        )
        if match is None:
            names = ", ".join(f"'{t.name}'" for t in available) or "none"
            raise AtlassianError(
                f"Transition '{transition}' is not available on {issue_key}. Available: {names}."
            )
        outcome = await self._core.write(
            "POST",
            f"{self._api}/issue/{issue_key}/transitions",
            {"transition": {"id": match.id}},
        )
        return match, outcome

    async def list_worklogs(self, key: str, max_results: int = 10) -> WorklogList:
        """List the time logged on an issue.

        Args:
            key: Issue key.
            max_results: How many of the newest worklogs to include.

        Returns:
            The newest worklogs and totals over all of them.

        Raises:
            AtlassianError: If the key is invalid or the request fails.
        """
        issue_key = normalize_issue_key(key)
        body = _object(await self._core.get(f"{self._api}/issue/{issue_key}/worklog"), "worklogs")
        logs = [
            Worklog(
                id=_text(item, "id") or "",
                author=_text(item, "author", "displayName") or _text(item, "author", "name"),
                started=_text(item, "started"),
                time_spent=_text(item, "timeSpent"),
                seconds=_number(item, "timeSpentSeconds") or 0,
                comment=_text(item, "comment"),
            )
            for item in _objects(body.get("worklogs"))
        ]
        newest = logs[-max_results:] if max_results > 0 else []
        return WorklogList(
            issue_key=issue_key,
            total=_number(body, "total") or len(logs),
            total_seconds=sum(log.seconds for log in logs),
            returned=len(newest),
            worklogs=newest,
        )

    async def log_work(
        self, key: str, time_spent: str, comment: str | None = None, started: str | None = None
    ) -> WriteOutcome:
        """Log time on an issue (blocked when dry-run is on).

        The remaining estimate is reduced automatically, as Jira does by default.

        Args:
            key: Issue key.
            time_spent: Duration in Jira's syntax, such as ``1h 30m``.
            comment: Work description in Jira wiki markup, or None.
            started: When the work started (ISO 8601), or None to let Jira use now.

        Returns:
            The write outcome.

        Raises:
            AtlassianError: If a value is invalid or the request fails.
        """
        issue_key = normalize_issue_key(key)
        payload: dict[str, JsonValue] = {"timeSpent": normalize_duration(time_spent)}
        if comment and comment.strip():
            payload["comment"] = comment
        if started and started.strip():
            payload["started"] = jira_time(started)
        return await self._core.write("POST", f"{self._api}/issue/{issue_key}/worklog", payload)

    async def find_assignee(self, key: str, person: str) -> Assignee:
        """Work out who "assign to <person>" means for an issue.

        Args:
            key: Issue key; only people who may be assigned this issue are considered.
            person: "me", "none" to unassign, or a username, name or email address.

        Returns:
            The person, or an empty assignee to unassign.

        Raises:
            AtlassianError: If nobody or more than one person matches, or a request fails.
        """
        issue_key = normalize_issue_key(key)
        wanted = " ".join(person.split()).casefold()
        if wanted in _NOBODY:
            return Assignee()
        if wanted in _ME:
            profile = await self.myself()
            return Assignee(
                username=_text(profile, "name"), display_name=_text(profile, "displayName")
            )
        found = await self._core.get(
            f"{self._api}/user/assignable/search",
            params={"issueKey": issue_key, "username": person.strip(), "maxResults": 50},
        )
        people = [
            Assignee(username=_text(item, "name"), display_name=_text(item, "displayName"))
            for item in _objects(found)
            if _text(item, "name")
        ]
        exact = [
            p
            for p in people
            if wanted in {(p.username or "").casefold(), (p.display_name or "").casefold()}
        ]
        matches = exact or people
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise AtlassianError(
                f"Nobody matching '{person}' can be assigned {issue_key}. Check the spelling, "
                "or ask the user for the person's Jira username."
            )
        listed = ", ".join(f"{p.display_name} ({p.username})" for p in matches[:MAX_CANDIDATES])
        more = f" and {len(matches) - MAX_CANDIDATES} more" if len(matches) > MAX_CANDIDATES else ""
        raise AtlassianError(
            f"'{person}' matches more than one person who can be assigned {issue_key}: "
            f"{listed}{more}. Ask the user which one, then pass that username."
        )

    async def assign_issue(self, key: str, who: Assignee) -> WriteOutcome:
        """Assign an issue to a person found by ``find_assignee`` (blocked when dry-run is on).

        Args:
            key: Issue key.
            who: The person, or an empty assignee to unassign.

        Returns:
            The write outcome.

        Raises:
            AtlassianError: If the key is invalid or the request fails.
        """
        issue_key = normalize_issue_key(key)
        return await self._core.write(
            "PUT", f"{self._api}/issue/{issue_key}/assignee", {"name": who.username}
        )
