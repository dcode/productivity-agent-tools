"""Jira Data Center REST client (API v2) built on the shared HTTP core."""

import re

from pydantic import JsonValue

from productivity_agent_tools.config import JiraSettings
from productivity_agent_tools.errors import ApiError, AtlassianError
from productivity_agent_tools.http import AtlassianClient, WriteOutcome
from productivity_agent_tools.jira.models import (
    Comment,
    IssueDetail,
    IssueSummary,
    SearchResult,
    Transition,
)

type JsonObject = dict[str, JsonValue]

_ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*$")
_SUMMARY_FIELDS: str = "summary,status,issuetype,priority,assignee,updated"
_DETAIL_FIELDS: str = _SUMMARY_FIELDS + ",description,reporter,created,labels,comment"
MAX_RESULTS_LIMIT: int = 100


def _text(source: JsonValue, *keys: str) -> str | None:
    """Read a nested string out of decoded JSON without raising.

    Args:
        source: Decoded JSON value to walk.
        *keys: Successive object keys to follow.

    Returns:
        The string found at the end of the path, or None.
    """
    current = source
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, str) else None


def _objects(source: JsonValue) -> list[JsonObject]:
    """Return the JSON objects inside a decoded JSON array.

    Args:
        source: Decoded JSON value expected to be an array.

    Returns:
        The array's object members; empty if the value is not an array.
    """
    if not isinstance(source, list):
        return []
    return [item for item in source if isinstance(item, dict)]


def _object(source: JsonValue, what: str) -> JsonObject:
    """Require a decoded JSON value to be an object.

    Args:
        source: Decoded JSON value.
        what: Name of the response, used in the error message.

    Returns:
        The value as an object.

    Raises:
        ApiError: If the value is not an object.
    """
    if not isinstance(source, dict):
        raise ApiError(200, f"Jira returned an unexpected {what} response.")
    return source


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

    def __init__(self, core: AtlassianClient, settings: JiraSettings) -> None:
        """Create the client.

        Args:
            core: Shared HTTP core for the host.
            settings: Jira module settings.
        """
        self._core = core
        self._settings = settings
        self._api = f"{settings.path}/rest/api/2"

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
