"""Jira Data Center REST client (API v2) built on an ``ApiClient`` adapter."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from pydantic import JsonValue

from productivity_agent_tools.api import ApiClient, WriteOutcome
from productivity_agent_tools.capabilities import IssueDetails
from productivity_agent_tools.errors import AgentToolsError
from productivity_agent_tools.jira.fields import (
    FieldMeta,
    check_label,
    choose,
    due_date,
    missing_required,
    page_values,
    parse_createmeta,
    parse_editmeta,
    require,
)
from productivity_agent_tools.jira.models import (
    Assignee,
    Comment,
    CreateFields,
    EditFields,
    IssueDetail,
    IssueLink,
    IssueSummary,
    IssueTypeInfo,
    LinkType,
    SearchResult,
    Transition,
    Worklog,
    WorklogList,
)
from productivity_agent_tools.jira.settings import JiraSettings
from productivity_agent_tools.payload import JsonObject
from productivity_agent_tools.payload import as_object as _object
from productivity_agent_tools.payload import number as _number
from productivity_agent_tools.payload import objects as _objects
from productivity_agent_tools.payload import text as _text
from productivity_agent_tools.payload import value as _value

_ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*$")
_SUMMARY_FIELDS: str = "summary,status,issuetype,priority,assignee,updated"
_DETAIL_FIELDS: str = _SUMMARY_FIELDS + (
    ",description,reporter,created,labels,comment,project,duedate,resolution,timetracking,"
    "timeoriginalestimate,timespent,components,fixVersions,versions,issuelinks"
)
MAX_RESULTS_LIMIT: int = 100
# Jira's duration syntax: one or more of a number followed by w, d, h or m.
_DURATION = re.compile(r"^(\d+(\.\d+)?[wdhm])(\s+\d+(\.\d+)?[wdhm])*$")
_ME: frozenset[str] = frozenset({"me", "myself", "self", "current user", "currentuser()"})
_NOBODY: frozenset[str] = frozenset({"none", "nobody", "unassigned", "unassign", ""})
MAX_CANDIDATES: int = 10


_PROJECT_KEY = re.compile(r"^[A-Z][A-Z0-9_]+$")


@dataclass(frozen=True, slots=True)
class FieldChanges:
    """Field values for editing or creating an issue; None means "leave as is".

    Attributes:
        summary: New summary.
        description: New description, in wiki markup.
        issue_type: Issue type name.
        priority: Priority name.
        reporter: Reporter: "me", or a username, name or email address.
        due_date: ISO date, or "none" to clear it.
        original_estimate: Duration in Jira's syntax.
        remaining_estimate: Duration in Jira's syntax.
        add_labels: Labels to add (on create: the labels).
        remove_labels: Labels to remove.
        add_components: Component names to add (on create: the components).
        remove_components: Component names to remove.
        add_fix_versions: Fix version names to add (on create: the fix versions).
        remove_fix_versions: Fix version names to remove.
        add_affects_versions: Affects version names to add (on create: the versions).
        remove_affects_versions: Affects version names to remove.
    """

    summary: str | None = None
    description: str | None = None
    issue_type: str | None = None
    priority: str | None = None
    reporter: str | None = None
    due_date: str | None = None
    original_estimate: str | None = None
    remaining_estimate: str | None = None
    add_labels: list[str] | None = None
    remove_labels: list[str] | None = None
    add_components: list[str] | None = None
    remove_components: list[str] | None = None
    add_fix_versions: list[str] | None = None
    remove_fix_versions: list[str] | None = None
    add_affects_versions: list[str] | None = None
    remove_affects_versions: list[str] | None = None


def normalize_project_key(project: str) -> str:
    """Validate and normalize a project key.

    Args:
        project: Project key as given, such as ``abc``.

    Returns:
        The upper-cased key.

    Raises:
        AgentToolsError: If it is not shaped like a project key.
    """
    normalized = project.strip().upper()
    if not _PROJECT_KEY.fullmatch(normalized):
        raise AgentToolsError(
            f"'{project}' is not a valid project key (expected something like ABC)."
        )
    return normalized


def normalize_duration(time_spent: str) -> str:
    """Validate a duration in Jira's syntax and tidy its spacing.

    Args:
        time_spent: Such as ``1h 30m``, ``2d`` or ``45m``.

    Returns:
        The duration with single spaces and lower-case units.

    Raises:
        AgentToolsError: If it is not in Jira's syntax.
    """
    cleaned = " ".join(time_spent.lower().split())
    cleaned = re.sub(r"(\d)\s+([wdhm])\b", r"\1\2", cleaned)
    if not _DURATION.fullmatch(cleaned):
        raise AgentToolsError(
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
        AgentToolsError: If it cannot be read.
    """
    text = started.strip()
    try:
        if "T" in text or " " in text:
            moment = datetime.fromisoformat(text)
        else:
            day = date.fromisoformat(text)
            moment = datetime(day.year, day.month, day.day, 9)
    except ValueError:
        raise AgentToolsError(
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
        AgentToolsError: If the value is not shaped like an issue key.
    """
    normalized = key.strip().upper()
    if not _ISSUE_KEY.fullmatch(normalized):
        raise AgentToolsError(
            f"'{key}' is not a valid issue key (expected something like ABC-123)."
        )
    return normalized


def _names(source: JsonValue) -> list[str]:
    """List the names of a decoded array of named objects, such as components.

    Args:
        source: Decoded JSON array.

    Returns:
        Each object's name.
    """
    return [name for item in _objects(source) if (name := _text(item, "name"))]


def _link(item: JsonObject) -> IssueLink:
    """Describe one issue link from the side of the issue that holds it.

    Jira lists a link under both issues: with ``outwardIssue`` set the other
    issue is on the outward end ("this blocks it"), with ``inwardIssue`` on
    the inward end ("this is blocked by it").

    Args:
        item: One entry of the ``issuelinks`` field.

    Returns:
        The link.
    """
    outward = item.get("outwardIssue")
    other = outward if isinstance(outward, dict) else item.get("inwardIssue")
    relation = _text(item, "type", "outward" if isinstance(outward, dict) else "inward")
    return IssueLink(
        relation=relation or _text(item, "type", "name") or "relates to",
        key=_text(other, "key") or "",
        summary=_text(other, "fields", "summary"),
        status=_text(other, "fields", "status", "name"),
        link_id=_text(item, "id"),
    )


def _transition(item: JsonObject) -> Transition:
    """Build one transition, including the resolutions its screen offers.

    Args:
        item: One decoded transition, fetched with its fields expanded.

    Returns:
        The transition.
    """
    resolution = _value(item, "fields", "resolution")
    meta = parse_editmeta({"fields": {"resolution": resolution}}).get("resolution")
    return Transition(
        id=_text(item, "id") or "",
        name=_text(item, "name") or "",
        to_status=_text(item, "to", "name"),
        resolutions=[r for v in meta.allowed if (r := _text(v, "name"))] if meta else [],
        resolution_required=meta is not None and meta.required,
    )


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
            AgentToolsError: If the request fails.
        """
        return _object(await self._core.get(f"{self._api}/myself"), "profile")

    async def server_version(self) -> str | None:
        """Fetch the Jira server version.

        Returns:
            The version string, or None if the server does not report one.

        Raises:
            AgentToolsError: If the request fails.
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
            AgentToolsError: If the query is invalid or the request fails.
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
            AgentToolsError: If the key is invalid or the request fails.
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
            project=_text(fields, "project", "key"),
            description=_text(fields, "description"),
            reporter=_text(fields, "reporter", "displayName"),
            created=_text(fields, "created"),
            due_date=_text(fields, "duedate"),
            resolution=_text(fields, "resolution", "name"),
            labels=[str(label) for label in labels] if isinstance(labels, list) else [],
            components=_names(fields.get("components")),
            fix_versions=_names(fields.get("fixVersions")),
            affects_versions=_names(fields.get("versions")),
            original_estimate=_text(fields, "timetracking", "originalEstimate"),
            remaining_estimate=_text(fields, "timetracking", "remainingEstimate"),
            time_spent=_text(fields, "timetracking", "timeSpent"),
            original_estimate_seconds=_number(fields, "timeoriginalestimate"),
            time_spent_seconds=_number(fields, "timespent"),
            links=[_link(item) for item in _objects(fields.get("issuelinks"))],
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
            AgentToolsError: If the key is invalid or the request fails.
        """
        issue_key = normalize_issue_key(key)
        body = _object(
            await self._core.get(
                f"{self._api}/issue/{issue_key}/transitions",
                params={"expand": "transitions.fields"},
            ),
            "transitions",
        )
        return [_transition(item) for item in _objects(body.get("transitions"))]

    async def add_comment(self, key: str, body: str) -> WriteOutcome:
        """Add a comment to an issue (blocked when dry-run is on).

        Args:
            key: Issue key.
            body: Comment text in Jira wiki markup.

        Returns:
            The write outcome.

        Raises:
            AgentToolsError: If the key or body is invalid, or the request fails.
        """
        issue_key = normalize_issue_key(key)
        if not body.strip():
            raise AgentToolsError("Comment body must not be empty.")
        return await self._core.write(
            "POST", f"{self._api}/issue/{issue_key}/comment", {"body": body}
        )

    async def transition_issue(
        self, key: str, transition: str, resolution: str | None = None
    ) -> tuple[Transition, WriteOutcome]:
        """Move an issue through a workflow transition (blocked when dry-run is on).

        The transition is resolved against those currently available, even in
        dry-run, so an invalid name is reported either way.

        Args:
            key: Issue key.
            transition: Transition name (case-insensitive) or identifier.
            resolution: Resolution to set, such as Fixed, when the transition's screen has one.

        Returns:
            The resolved transition and the write outcome.

        Raises:
            AgentToolsError: If the transition is not available or the request fails.
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
            raise AgentToolsError(
                f"Transition '{transition}' is not available on {issue_key}. Available: {names}."
            )
        payload: dict[str, JsonValue] = {"transition": {"id": match.id}}
        if resolution and resolution.strip():
            if not match.resolutions:
                raise AgentToolsError(
                    f"The '{match.name}' transition on {issue_key} does not set a resolution. "
                    "Leave resolution out."
                )
            chosen = next(
                (r for r in match.resolutions if r.casefold() == resolution.strip().casefold()),
                None,
            )
            if chosen is None:
                raise AgentToolsError(
                    f"'{resolution}' is not a resolution the '{match.name}' transition allows. "
                    f"Allowed: {', '.join(match.resolutions)}."
                )
            payload["fields"] = {"resolution": {"name": chosen}}
        elif match.resolution_required:
            raise AgentToolsError(
                f"The '{match.name}' transition on {issue_key} needs a resolution. Ask the user "
                f"which one: {', '.join(match.resolutions)}."
            )
        outcome = await self._core.write(
            "POST", f"{self._api}/issue/{issue_key}/transitions", payload
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
            AgentToolsError: If the key is invalid or the request fails.
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
            AgentToolsError: If a value is invalid or the request fails.
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
            AgentToolsError: If nobody or more than one person matches, or a request fails.
        """
        issue_key = normalize_issue_key(key)
        return await self.find_user(
            person,
            f"{self._api}/user/assignable/search",
            {"issueKey": issue_key},
            f"can be assigned {issue_key}",
        )

    async def find_user(
        self,
        person: str,
        search: str | None = None,
        scope: dict[str, str | int] | None = None,
        who: str = "is a Jira user",
        allow_nobody: bool = True,
    ) -> Assignee:
        """Resolve "me", "none", or a name to one Jira user.

        Args:
            person: "me", "none", or a username, display name or email address.
            search: User search resource; the general user search when None.
            scope: Extra query parameters that narrow the search, such as an issue key.
            who: What the people searched must be, for messages ("can be assigned ABC-1").
            allow_nobody: Accept "none" as a request for no user.

        Returns:
            The user, or an empty assignee for "none".

        Raises:
            AgentToolsError: If nobody or more than one person matches, or a request fails.
        """
        wanted = " ".join(person.split()).casefold()
        if allow_nobody and wanted in _NOBODY:
            return Assignee()
        if wanted in _ME:
            profile = await self.myself()
            return Assignee(
                username=_text(profile, "name"), display_name=_text(profile, "displayName")
            )
        if not wanted:
            raise AgentToolsError("A person must be named.")
        found = await self._core.get(
            search or f"{self._api}/user/search",
            params={**(scope or {}), "username": person.strip(), "maxResults": 50},
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
            raise AgentToolsError(
                f"Nobody matching '{person}' {who}. Check the spelling, or ask the user for "
                "the person's Jira username."
            )
        listed = ", ".join(f"{p.display_name} ({p.username})" for p in matches[:MAX_CANDIDATES])
        more = f" and {len(matches) - MAX_CANDIDATES} more" if len(matches) > MAX_CANDIDATES else ""
        raise AgentToolsError(
            f"'{person}' matches more than one person who {who}: {listed}{more}. Ask the user "
            "which one, then pass that username."
        )

    async def assign_issue(self, key: str, who: Assignee) -> WriteOutcome:
        """Assign an issue to a person found by ``find_assignee`` (blocked when dry-run is on).

        Args:
            key: Issue key.
            who: The person, or an empty assignee to unassign.

        Returns:
            The write outcome.

        Raises:
            AgentToolsError: If the key is invalid or the request fails.
        """
        issue_key = normalize_issue_key(key)
        return await self._core.write(
            "PUT", f"{self._api}/issue/{issue_key}/assignee", {"name": who.username}
        )

    async def _editmeta(self, issue_key: str) -> dict[str, FieldMeta]:
        """Fetch the fields an issue's edit screen offers.

        Args:
            issue_key: Normalized issue key.

        Returns:
            The fields by id.

        Raises:
            AgentToolsError: If the request fails.
        """
        return parse_editmeta(await self._core.get(f"{self._api}/issue/{issue_key}/editmeta"))

    async def edit_fields(self, key: str) -> EditFields:
        """List the fields an issue can be edited with, and their allowed values.

        Args:
            key: Issue key.

        Returns:
            The fields on the issue's edit screen.

        Raises:
            AgentToolsError: If the key is invalid or the request fails.
        """
        issue_key = normalize_issue_key(key)
        fields = await self._editmeta(issue_key)
        return EditFields(issue_key=issue_key, fields=[meta.info() for meta in fields.values()])

    async def _set_values(
        self,
        fields: dict[str, FieldMeta],
        where: str,
        changes: FieldChanges,
    ) -> dict[str, JsonValue]:
        """Turn single-value changes into a ``fields`` object, checking each against the screen.

        Args:
            fields: The screen's fields.
            where: The screen, for messages.
            changes: The requested changes.

        Returns:
            The ``fields`` object to send.

        Raises:
            AgentToolsError: If a field is not on the screen or a value is not allowed.
        """
        out: dict[str, JsonValue] = {}
        if changes.summary is not None:
            require(fields, "summary", where)
            if not changes.summary.strip():
                raise AgentToolsError("The summary must not be empty.")
            out["summary"] = changes.summary.strip()
        if changes.description is not None:
            require(fields, "description", where)
            out["description"] = changes.description
        if changes.issue_type is not None:
            out["issuetype"] = choose(require(fields, "issuetype", where), changes.issue_type)
        if changes.priority is not None:
            out["priority"] = choose(require(fields, "priority", where), changes.priority)
        if changes.reporter is not None:
            require(fields, "reporter", where)
            who = await self.find_user(changes.reporter, allow_nobody=False)
            out["reporter"] = {"name": who.username}
        if changes.due_date is not None:
            require(fields, "duedate", where)
            out["duedate"] = due_date(changes.due_date)
        estimates: dict[str, JsonValue] = {}
        if changes.original_estimate is not None:
            estimates["originalEstimate"] = normalize_duration(changes.original_estimate)
        if changes.remaining_estimate is not None:
            estimates["remainingEstimate"] = normalize_duration(changes.remaining_estimate)
        if estimates:
            require(fields, "timetracking", where)
            out["timetracking"] = estimates
        return out

    async def edit_issue(self, key: str, changes: FieldChanges) -> tuple[list[str], WriteOutcome]:
        """Change an issue's fields (blocked when dry-run is on).

        Every field is checked against the issue's edit screen first, even in
        dry-run, and values are matched by name against what the field allows.
        Labels, components and versions are added or removed one by one, so
        the rest of each list is kept.

        Args:
            key: Issue key.
            changes: The requested changes.

        Returns:
            The names of the changed fields, and the write outcome.

        Raises:
            AgentToolsError: If nothing is asked for, a field or value is not allowed, or
                the request fails.
        """
        issue_key = normalize_issue_key(key)
        fields = await self._editmeta(issue_key)
        where = f"the edit screen of {issue_key}"
        values = await self._set_values(fields, where, changes)
        update: dict[str, JsonValue] = {}
        for field_id, added, removed in (
            ("labels", changes.add_labels, changes.remove_labels),
            ("components", changes.add_components, changes.remove_components),
            ("fixVersions", changes.add_fix_versions, changes.remove_fix_versions),
            ("versions", changes.add_affects_versions, changes.remove_affects_versions),
        ):
            if not added and not removed:
                continue
            meta = require(fields, field_id, where)
            ops: list[JsonValue] = []
            for verb, items in (("add", added or []), ("remove", removed or [])):
                for item in items:
                    target = check_label(item) if field_id == "labels" else choose(meta, item)
                    ops.append({verb: target})
            update[field_id] = ops
        if not values and not update:
            raise AgentToolsError("Nothing to change: give at least one field.")
        payload: dict[str, JsonValue] = {}
        if values:
            payload["fields"] = values
        if update:
            payload["update"] = update
        changed = [fields[f].name for f in [*values, *update]]
        outcome = await self._core.write("PUT", f"{self._api}/issue/{issue_key}", payload)
        return changed, outcome

    async def issue_types(self, project: str) -> list[IssueTypeInfo]:
        """List the issue types the user can create in a project.

        Args:
            project: Project key.

        Returns:
            The issue types.

        Raises:
            AgentToolsError: If the key is invalid or the request fails.
        """
        key = normalize_project_key(project)
        body = await self._core.get(
            f"{self._api}/issue/createmeta/{key}/issuetypes", params={"maxResults": 100}
        )
        return [
            IssueTypeInfo(
                id=_text(item, "id") or "",
                name=_text(item, "name") or "",
                subtask=item.get("subtask") is True,
            )
            for item in page_values(body)
        ]

    async def _create_screen(
        self, project: str, issue_type: str
    ) -> tuple[str, list[IssueTypeInfo], IssueTypeInfo, dict[str, FieldMeta]]:
        """Find an issue type by name and fetch its create screen.

        Args:
            project: Project key.
            issue_type: Issue type name (any letter case) or id.

        Returns:
            The project key, all issue types, the chosen one, and its fields.

        Raises:
            AgentToolsError: If the type is not offered or a request fails.
        """
        key = normalize_project_key(project)
        types = await self.issue_types(key)
        wanted = issue_type.strip().casefold()
        chosen = next((t for t in types if wanted in {t.name.casefold(), t.id}), None)
        if chosen is None:
            names = ", ".join(t.name for t in types) or "none"
            raise AgentToolsError(
                f"'{issue_type}' is not an issue type you can create in {key}. Available: {names}."
            )
        body = await self._core.get(
            f"{self._api}/issue/createmeta/{key}/issuetypes/{chosen.id}",
            params={"maxResults": 200},
        )
        return key, types, chosen, parse_createmeta(body)

    async def create_fields(self, project: str, issue_type: str | None = None) -> CreateFields:
        """Describe what creating an issue in a project needs.

        Args:
            project: Project key.
            issue_type: An issue type to list the fields of, or None for the types only.

        Returns:
            The issue types, and the chosen type's fields.

        Raises:
            AgentToolsError: If a value is invalid or a request fails.
        """
        if issue_type is None or not issue_type.strip():
            key = normalize_project_key(project)
            return CreateFields(project=key, issue_types=await self.issue_types(key))
        key, types, chosen, fields = await self._create_screen(project, issue_type)
        return CreateFields(
            project=key,
            issue_types=types,
            issue_type=chosen.name,
            fields=[meta.info() for meta in fields.values()],
        )

    async def create_issue(
        self,
        project: str,
        issue_type: str,
        changes: FieldChanges,
        assignee: str | None = None,
        parent: str | None = None,
    ) -> tuple[IssueTypeInfo, WriteOutcome]:
        """Create an issue (blocked when dry-run is on).

        The project's create screen is checked first, even in dry-run: every
        field must be on it, and every required field without a default must
        be given.

        Args:
            project: Project key.
            issue_type: Issue type name or id.
            changes: The fields to set; ``summary`` is required. Labels,
                components and versions come from the ``add_`` lists.
            assignee: "me", or a person who can be assigned issues in the project.
            parent: Parent issue key, for a sub-task.

        Returns:
            The issue type used, and the write outcome.

        Raises:
            AgentToolsError: If a field or value is not allowed, a required field is
                missing, or the request fails.
        """
        key, _, chosen, fields = await self._create_screen(project, issue_type)
        if not changes.summary or not changes.summary.strip():
            raise AgentToolsError("A new issue needs a summary.")
        where = f"the create screen of {chosen.name} in {key}"
        values = await self._set_values(fields, where, changes)
        values["project"] = {"key": key}
        values["issuetype"] = {"id": chosen.id}
        for field_id, items in (
            ("labels", changes.add_labels),
            ("components", changes.add_components),
            ("fixVersions", changes.add_fix_versions),
            ("versions", changes.add_affects_versions),
        ):
            if items:
                meta = require(fields, field_id, where)
                values[field_id] = [
                    check_label(i) if field_id == "labels" else choose(meta, i) for i in items
                ]
        if assignee is not None and assignee.strip():
            require(fields, "assignee", where)
            who = await self.find_user(
                assignee,
                f"{self._api}/user/assignable/search",
                {"project": key},
                f"can be assigned issues in {key}",
            )
            values["assignee"] = {"name": who.username}
        if chosen.subtask != bool(parent and parent.strip()):
            raise AgentToolsError(
                f"{chosen.name} is a sub-task type, so give the parent issue key."
                if chosen.subtask
                else f"{chosen.name} is not a sub-task type, so leave parent out."
            )
        if parent and parent.strip():
            values["parent"] = {"key": normalize_issue_key(parent)}
        missing = missing_required(fields, set(values))
        if missing:
            listed = ", ".join(f"{m.name} ({m.id})" for m in missing)
            raise AgentToolsError(
                f"Creating a {chosen.name} in {key} also needs: {listed}. This tool cannot "
                "fill those fields; ask the user to create the issue in the browser, or "
                "choose another issue type."
            )
        outcome = await self._core.write("POST", f"{self._api}/issue", {"fields": values})
        return chosen, outcome

    async def link_types(self) -> list[LinkType]:
        """List the kinds of links issues can have.

        Returns:
            The link types.

        Raises:
            AgentToolsError: If the request fails.
        """
        body = await self._core.get(f"{self._api}/issueLinkType")
        return [
            LinkType(
                name=_text(item, "name") or "",
                outward=_text(item, "outward") or "",
                inward=_text(item, "inward") or "",
            )
            for item in _objects(_value(body, "issueLinkTypes"))
        ]

    async def link_issues(
        self, key: str, relation: str, other_key: str, comment: str | None = None
    ) -> tuple[str, WriteOutcome]:
        """Link two issues, such as "ABC-1 blocks ABC-2" (blocked when dry-run is on).

        The relation can be a link type's name or either of its wordings; an
        inward wording ("is blocked by") swaps the two ends.

        Args:
            key: The issue the sentence starts with.
            relation: Such as "blocks", "is blocked by", "relates to" or "Duplicate".
            other_key: The issue the sentence ends with.
            comment: An optional comment added to the first issue, in wiki markup.

        Returns:
            The link as a sentence, and the write outcome.

        Raises:
            AgentToolsError: If a key or the relation is invalid, or the request fails.
        """
        first = normalize_issue_key(key)
        second = normalize_issue_key(other_key)
        if first == second:
            raise AgentToolsError("An issue cannot be linked to itself.")
        wanted = " ".join(relation.split()).casefold()
        types = await self.link_types()
        for link in types:
            if wanted in {link.name.casefold(), link.outward.casefold()}:
                source, target, words = first, second, link.outward
                break
            if wanted == link.inward.casefold():
                source, target, words = second, first, link.outward
                break
        else:
            listed = "; ".join(f"{t.outward} / {t.inward}" for t in types) or "none"
            raise AgentToolsError(
                f"'{relation}' is not a kind of issue link here. Use one of: {listed}."
            )
        # Jira's inwardIssue is the link's source, the issue that shows the outward wording.
        payload: dict[str, JsonValue] = {
            "type": {"name": link.name},
            "inwardIssue": {"key": source},
            "outwardIssue": {"key": target},
        }
        if comment and comment.strip():
            payload["comment"] = {"body": comment}
        outcome = await self._core.write("POST", f"{self._api}/issueLink", payload)
        return f"{source} {words} {target}", outcome

    async def describe_issues(self, keys: Sequence[str]) -> dict[str, IssueDetails]:
        """Look up issues by key in one search, for other products to show beside a mention.

        Args:
            keys: Issue keys; ones not shaped like ``ABC-123`` are ignored, so the
                rest can go into JQL unquoted.

        Returns:
            The issues found, by key; keys Jira does not return are left out.

        Raises:
            AgentToolsError: If the search fails.
        """
        valid = sorted({key for key in keys if _ISSUE_KEY.fullmatch(key)})
        if not valid:
            return {}
        result = await self.search(f"key in ({', '.join(valid)})", len(valid))
        return {
            issue.key: IssueDetails(
                key=issue.key,
                url=issue.url,
                summary=issue.summary,
                status=issue.status,
                assignee=issue.assignee,
            )
            for issue in result.issues
        }

    async def development_pull_requests(self, key: str) -> list[JsonObject]:
        """Fetch the pull requests Jira's Development panel shows for an issue.

        This reads ``rest/dev-status``, the resource behind that panel. Atlassian
        does not document it, so callers should treat its entries defensively.
        It covers pull requests linked by branch name or title as well as by
        commits, for every linked Bitbucket.

        Args:
            key: Issue key.

        Returns:
            The panel's pull request entries, as Jira sends them.

        Raises:
            AgentToolsError: If the key is invalid or a request fails.
        """
        issue_key = normalize_issue_key(key)
        issue = _object(
            await self._core.get(f"{self._api}/issue/{issue_key}", params={"fields": "summary"}),
            "issue",
        )
        issue_id = _text(issue, "id")
        if issue_id is None:
            raise AgentToolsError(f"Jira did not give the internal id of {issue_key}.")
        body = await self._core.get(
            f"{self._settings.path}/rest/dev-status/1.0/issue/detail",
            params={"issueId": issue_id, "applicationType": "stash", "dataType": "pullrequest"},
        )
        return [
            pull
            for detail in _objects(_value(body, "detail"))
            for pull in _objects(detail.get("pullRequests"))
        ]
