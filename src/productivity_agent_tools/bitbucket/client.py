"""Bitbucket Data Center REST client (API 1.0) built on an ``ApiClient`` adapter."""

import re
from datetime import UTC, datetime

from pydantic import JsonValue

from productivity_agent_tools.api import ApiClient
from productivity_agent_tools.bitbucket.models import (
    BuildStatus,
    PullRequestComment,
    PullRequestDetail,
    PullRequestList,
    PullRequestSummary,
    Reviewer,
)
from productivity_agent_tools.config import BitbucketSettings
from productivity_agent_tools.errors import ApiError, AtlassianError, NotFoundError
from productivity_agent_tools.payload import JsonObject, as_object, number, objects, text

ROLES: tuple[str, ...] = ("reviewer", "author", "participant")
STATES: tuple[str, ...] = ("open", "merged", "declined", "all")
MAX_RESULTS_LIMIT: int = 100
_ACTIVITY_PAGE: int = 100
_NAME = re.compile(r"^~?[A-Za-z0-9_][A-Za-z0-9_.\-]*$")


def _choice(value: str, allowed: tuple[str, ...], what: str) -> str:
    """Validate a value against a fixed set of choices.

    Args:
        value: What the caller gave, in any letter case.
        allowed: The accepted lower-case values.
        what: Name of the parameter, for the error message.

    Returns:
        The lower-cased value.

    Raises:
        AtlassianError: If the value is not one of the choices.
    """
    cleaned = value.strip().lower()
    if cleaned not in allowed:
        raise AtlassianError(f"'{value}' is not a valid {what}. Use one of: {', '.join(allowed)}.")
    return cleaned


def _name(value: str, what: str) -> str:
    """Validate a project key or repository slug before it goes into a URL.

    Args:
        value: The key or slug as given by the caller.
        what: Name of the parameter, for the error message.

    Returns:
        The value with surrounding whitespace removed.

    Raises:
        AtlassianError: If the value has characters a key or slug cannot.
    """
    cleaned = value.strip()
    if not _NAME.fullmatch(cleaned):
        raise AtlassianError(f"'{value}' is not a valid {what}.")
    return cleaned


def _timestamp(source: JsonValue, key: str) -> str | None:
    """Convert one of Bitbucket's millisecond timestamps to ISO 8601.

    Args:
        source: Decoded JSON object holding the timestamp.
        key: Name of the timestamp field.

    Returns:
        The UTC time in ISO 8601, or None when absent.
    """
    millis = number(source, key)
    if millis is None:
        return None
    return datetime.fromtimestamp(millis / 1000, tz=UTC).isoformat(timespec="seconds")


class BitbucketClient:
    """Read operations on Bitbucket pull requests, returning typed results."""

    def __init__(self, core: ApiClient, settings: BitbucketSettings) -> None:
        """Create the client.

        Args:
            core: API adapter connected to Bitbucket's host.
            settings: Bitbucket module settings.
        """
        self._core = core
        self._settings = settings
        self._api = f"{settings.path}/rest/api/1.0"
        self._builds = f"{settings.path}/rest/build-status/1.0"

    @property
    def base_url(self) -> str:
        """Browser base URL of this Bitbucket instance."""
        return f"{self._core.base_url}{self._settings.path}"

    def _summary_fields(self, pull: JsonObject) -> dict[str, object]:
        """Collect the fields shared by the compact and full views.

        Args:
            pull: One decoded pull request object from the API.

        Returns:
            Keyword arguments for ``PullRequestSummary``.
        """
        target = pull.get("toRef")
        links = objects(as_object(pull.get("links") or {}, "pull request links").get("self"))
        return {
            "project": text(target, "repository", "project", "key") or "",
            "repository": text(target, "repository", "slug") or "",
            "id": number(pull, "id") or 0,
            "title": text(pull, "title") or "",
            "state": text(pull, "state") or "UNKNOWN",
            "author": text(pull, "author", "user", "displayName"),
            "source_branch": text(pull, "fromRef", "displayId"),
            "target_branch": text(target, "displayId"),
            "reviewers": [
                Reviewer(
                    name=text(item, "user", "displayName") or text(item, "user", "name") or "",
                    status=text(item, "status") or "UNAPPROVED",
                )
                for item in objects(pull.get("reviewers"))
            ],
            "comment_count": number(pull, "properties", "commentCount") or 0,
            "open_task_count": number(pull, "properties", "openTaskCount") or 0,
            "updated": _timestamp(pull, "updatedDate"),
            "url": text(links[0], "href") if links else None,
        }

    async def server_version(self) -> str | None:
        """Fetch the Bitbucket server version.

        Returns:
            The version string, or None if the server does not report one.

        Raises:
            AtlassianError: If the request fails.
        """
        return text(await self._core.get(f"{self._api}/application-properties"), "version")

    async def list_pull_requests(
        self, role: str = "reviewer", state: str = "open", max_results: int | None = None
    ) -> PullRequestList:
        """List pull requests across all repositories that involve the user.

        Args:
            role: The user's role: reviewer, author or participant.
            state: open, merged, declined or all.
            max_results: Row cap; defaults to the configured value, limited to 100.

        Returns:
            The pull requests, newest first.

        Raises:
            AtlassianError: If a parameter is invalid or the request fails.
        """
        chosen_role = _choice(role, ROLES, "role")
        chosen_state = _choice(state, STATES, "state")
        limit = max(1, min(max_results or self._settings.default_max_results, MAX_RESULTS_LIMIT))
        params: dict[str, str | int] = {
            "role": chosen_role.upper(),
            "limit": limit,
            "order": "NEWEST",
        }
        if chosen_state != "all":
            params["state"] = chosen_state.upper()
        body = as_object(
            await self._core.get(f"{self._api}/dashboard/pull-requests", params=params),
            "pull request list",
        )
        pulls = [
            PullRequestSummary.model_validate(self._summary_fields(item))
            for item in objects(body.get("values"))
        ]
        return PullRequestList(
            role=chosen_role,
            state=chosen_state,
            returned=len(pulls),
            more_available=body.get("isLastPage") is False,
            pull_requests=pulls,
        )

    async def get_pull_request(
        self, project: str, repository: str, pull_request_id: int, max_comments: int = 10
    ) -> PullRequestDetail:
        """Fetch one pull request with its description, recent comments and builds.

        Args:
            project: Project key of the repository.
            repository: Repository slug.
            pull_request_id: Pull request number within the repository.
            max_comments: How many of the newest comments to include.

        Returns:
            The pull request detail.

        Raises:
            AtlassianError: If a parameter is invalid or the request fails.
        """
        if pull_request_id < 1:
            raise AtlassianError(f"{pull_request_id} is not a valid pull request number.")
        base = (
            f"{self._api}/projects/{_name(project, 'project key')}"
            f"/repos/{_name(repository, 'repository slug')}/pull-requests/{pull_request_id}"
        )
        pull = as_object(await self._core.get(base), "pull request")
        latest_commit = text(pull, "fromRef", "latestCommit")

        comments: list[PullRequestComment] = []
        if max_comments > 0:
            activities = as_object(
                await self._core.get(f"{base}/activities", params={"limit": _ACTIVITY_PAGE}),
                "pull request activity",
            )
            newest_first = [
                item
                for item in objects(activities.get("values"))
                if text(item, "action") == "COMMENTED" and isinstance(item.get("comment"), dict)
            ][:max_comments]
            comments = [
                PullRequestComment(
                    id=number(item, "comment", "id"),
                    author=text(item, "comment", "author", "displayName"),
                    created=_timestamp(item.get("comment"), "createdDate"),
                    text=text(item, "comment", "text") or "",
                    file=text(item, "commentAnchor", "path"),
                )
                for item in reversed(newest_first)
            ]

        builds: list[BuildStatus] = []
        builds_available = latest_commit is not None
        if latest_commit is not None:
            try:
                statuses = await self._core.get(f"{self._builds}/commits/{latest_commit}")
            except NotFoundError, ApiError:
                builds_available = False
            else:
                builds = [
                    BuildStatus(
                        state=text(item, "state") or "UNKNOWN",
                        name=text(item, "name") or text(item, "key"),
                        url=text(item, "url"),
                    )
                    for item in objects(as_object(statuses, "build status").get("values"))
                ]

        return PullRequestDetail.model_validate(
            {
                **self._summary_fields(pull),
                "description": text(pull, "description"),
                "created": _timestamp(pull, "createdDate"),
                "latest_commit": latest_commit,
                "comments": comments,
                "builds": builds,
                "builds_available": builds_available,
            }
        )
