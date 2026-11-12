"""Bitbucket Data Center REST client (API 1.0) built on an ``ApiClient`` adapter."""

import html
import re
from datetime import UTC, datetime
from http import HTTPStatus
from urllib.parse import quote

from pydantic import JsonValue

from productivity_agent_tools.api import ApiClient, WriteOutcome
from productivity_agent_tools.bitbucket.models import (
    Activity,
    BuildCounts,
    BuildStatus,
    ChangedFile,
    CodeHit,
    CodeLine,
    CodeSearchResult,
    Commit,
    CommitBuilds,
    DefaultProject,
    Deployment,
    DeploymentEnvironment,
    IssueCommit,
    LinkedIssue,
    MergeStatus,
    MergeVeto,
    Project,
    ProjectList,
    PullRequestActivity,
    PullRequestChanges,
    PullRequestComment,
    PullRequestCommits,
    PullRequestDetail,
    PullRequestDiff,
    PullRequestIssues,
    PullRequestList,
    PullRequestSummary,
    PullRequestTask,
    PullRequestTasks,
    Repository,
    RepositoryList,
    RepositoryPullRequests,
    RequiredBuild,
    RequiredBuildList,
    Reviewer,
    TestResults,
)
from productivity_agent_tools.bitbucket.settings import BitbucketSettings
from productivity_agent_tools.errors import AgentToolsError, ApiError, NotFoundError
from productivity_agent_tools.payload import JsonObject, as_object, number, objects, text, value

ROLES: tuple[str, ...] = ("reviewer", "author", "participant")
STATES: tuple[str, ...] = ("open", "merged", "declined", "all")
TASK_STATES: tuple[str, ...] = ("open", "resolved", "all")
REVIEWS: dict[str, str] = {
    "approve": "APPROVED",
    "needs_work": "NEEDS_WORK",
    "unapprove": "UNAPPROVED",
}
MAX_RESULTS_LIMIT: int = 100
DEFAULT_DIFF_CHARS: int = 20_000
MAX_DIFF_CHARS: int = 100_000
_ACTIVITY_PAGE: int = 100
MAX_LINES_PER_HIT: int = 12
_HIGHLIGHT = re.compile(r"</?em>")
_NAME = re.compile(r"^~?[A-Za-z0-9_][A-Za-z0-9_.\-]*$")
_COMMIT = re.compile(r"^[0-9a-fA-F]{4,64}$")
_ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*$")
# A deployment's lastUpdated is documented with a seconds-sized example, unlike
# every other Bitbucket timestamp; values below this are read as seconds.
_SECONDS_CUTOFF: int = 100_000_000_000


def _choice(value: str, allowed: tuple[str, ...], what: str) -> str:
    """Validate a value against a fixed set of choices.

    Args:
        value: What the caller gave, in any letter case.
        allowed: The accepted lower-case values.
        what: Name of the parameter, for the error message.

    Returns:
        The lower-cased value.

    Raises:
        AgentToolsError: If the value is not one of the choices.
    """
    cleaned = value.strip().lower()
    if cleaned not in allowed:
        raise AgentToolsError(f"'{value}' is not a valid {what}. Use one of: {', '.join(allowed)}.")
    return cleaned


def normalize_review(review: str) -> str:
    """Turn a review choice into one of the keys of ``REVIEWS``.

    Args:
        review: The caller's choice, such as "Needs work" or "approve".

    Returns:
        The validated key, such as ``needs_work``.

    Raises:
        AgentToolsError: If it is not a known review.
    """
    return _choice(re.sub(r"[\s-]+", "_", review.strip()), tuple(REVIEWS), "review")


def _name(value: str, what: str) -> str:
    """Validate a project key or repository slug before it goes into a URL.

    Args:
        value: The key or slug as given by the caller.
        what: Name of the parameter, for the error message.

    Returns:
        The value with surrounding whitespace removed.

    Raises:
        AgentToolsError: If the value has characters a key or slug cannot.
    """
    cleaned = value.strip()
    if not _NAME.fullmatch(cleaned):
        raise AgentToolsError(f"'{value}' is not a valid {what}.")
    return cleaned


def _commit(value: str) -> str:
    """Validate a commit hash before it goes into a URL.

    Args:
        value: Full or abbreviated commit hash as given by the caller.

    Returns:
        The hash with surrounding whitespace removed.

    Raises:
        AgentToolsError: If the value is not a hexadecimal hash.
    """
    cleaned = value.strip()
    if not _COMMIT.fullmatch(cleaned):
        raise AgentToolsError(f"'{value}' is not a valid commit hash.")
    return cleaned


def _timestamp(source: JsonValue, key: str) -> str | None:
    """Convert one of Bitbucket's millisecond timestamps to ISO 8601.

    Args:
        source: Decoded JSON object holding the timestamp.
        key: Name of the timestamp field.

    Returns:
        The UTC time in ISO 8601, or None when absent.
    """
    stamp = number(source, key)
    if stamp is None:
        return None
    seconds = stamp if stamp < _SECONDS_CUTOFF else stamp / 1000
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat(timespec="seconds")


def _first_link(source: JsonObject) -> str | None:
    """Read the browser URL out of an object's ``links.self`` list.

    Args:
        source: Decoded JSON object that may carry links.

    Returns:
        The first self link, or None.
    """
    links = source.get("links")
    own = objects(links.get("self")) if isinstance(links, dict) else []
    return text(own[0], "href") if own else None


def _build(item: JsonObject) -> BuildStatus:
    """Build one build result from the server's entry.

    Older servers send only state, key, name, url, description and
    ``dateAdded``; newer ones add test counts, duration and more.

    Args:
        item: One decoded build status object.

    Returns:
        The build result.
    """
    tests = item.get("testResults")
    duration = number(item, "duration")
    return BuildStatus(
        state=text(item, "state") or "UNKNOWN",
        name=text(item, "name") or text(item, "key"),
        key=text(item, "key"),
        url=text(item, "url"),
        description=text(item, "description"),
        build_number=text(item, "buildNumber"),
        ref=text(item, "ref"),
        updated=_timestamp(item, "updatedDate") or _timestamp(item, "dateAdded"),
        duration_seconds=duration / 1000 if duration is not None else None,
        tests=(
            TestResults(
                successful=number(tests, "successful") or 0,
                failed=number(tests, "failed") or 0,
                skipped=number(tests, "skipped") or 0,
            )
            if isinstance(tests, dict)
            else None
        ),
    )


def _strings(source: JsonValue) -> list[str]:
    """Return the strings inside a decoded JSON array.

    Args:
        source: Decoded JSON value expected to be an array of strings.

    Returns:
        The array's string members; empty if the value is not an array.
    """
    return [item for item in source if isinstance(item, str)] if isinstance(source, list) else []


def _ref_matcher(source: JsonValue) -> str | None:
    """Describe a required-build check's branch matcher in a few words.

    Args:
        source: Decoded ``refMatcher`` or ``exemptRefMatcher`` object.

    Returns:
        Such as "main" or "pattern: release/*", "any branch", or None if absent.
    """
    if not isinstance(source, dict):
        return None
    kind = text(source, "type", "id")
    shown = text(source, "displayId") or text(source, "id")
    if kind == "ANY_REF":
        return "any branch"
    if kind in (None, "BRANCH") or not shown:
        return shown
    return f"{kind.lower().replace('_', ' ')}: {shown}"


def _short_ids(source: JsonValue) -> list[str]:
    """List the abbreviated hashes of a decoded array of commits.

    Args:
        source: Decoded JSON array of commit objects.

    Returns:
        Each commit's short hash, or its full hash when no short one is given.
    """
    return [text(c, "displayId") or text(c, "id") or "" for c in objects(source)]


def _activity(item: JsonObject) -> Activity:
    """Build one history event from the server's activity entry.

    Args:
        item: One decoded pull request activity.

    Returns:
        The event.
    """
    comment = item.get("comment")
    replies = value(comment, "comments")
    return Activity(
        id=number(item, "id"),
        action=text(item, "action") or "UNKNOWN",
        user=text(item, "user", "displayName") or text(item, "user", "name"),
        created=_timestamp(item, "createdDate"),
        comment=text(comment, "text"),
        comment_id=number(comment, "id"),
        comment_action=text(item, "commentAction"),
        file=text(item, "commentAnchor", "path") or text(comment, "anchor", "path"),
        line=number(item, "commentAnchor", "line") or number(comment, "anchor", "line"),
        replies=len(replies) if isinstance(replies, list) else 0,
        commits_added=_short_ids(value(item, "added", "commits")),
        commits_removed=_short_ids(value(item, "removed", "commits")),
    )


class BitbucketClient:
    """Read operations on Bitbucket projects and pull requests, returning typed results."""

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
        self._required = f"{settings.path}/rest/required-builds/latest"
        self._jira = f"{settings.path}/rest/jira/latest"
        self._search = f"{settings.path}/rest/search/1.0/search"
        self._default_project = settings.default_project
        self._user_slug = settings.user_slug

    @property
    def default_project(self) -> str | None:
        """Project key used when a call names no project, if one is set."""
        return self._default_project

    def _project(self, project: str | None) -> str:
        """Pick the project for a call: the one given, else the default.

        Args:
            project: Project key from the caller, or None.

        Returns:
            The validated project key.

        Raises:
            AgentToolsError: If no project is given and none is set as default.
        """
        chosen = project.strip() if project else self._default_project
        if not chosen:
            raise AgentToolsError(
                "No project was given and no default project is set. Pass a project key, "
                "call bitbucket_set_default_project, or set default_project under "
                "[bitbucket] in the config. bitbucket_list_projects shows the keys."
            )
        return _name(chosen, "project key")

    def _limit(self, max_results: int | None) -> int:
        """Clamp a requested row count to what one page may hold.

        Args:
            max_results: Requested row cap, or None for the configured default.

        Returns:
            A page size between 1 and the limit.
        """
        return max(1, min(max_results or self._settings.default_max_results, MAX_RESULTS_LIMIT))

    def _repo(self, project: str | None, repository: str) -> tuple[str, str]:
        """Pick and validate the project key and repository slug for a call.

        Args:
            project: Project key, or None for the default project.
            repository: Repository slug.

        Returns:
            The project key and repository slug.

        Raises:
            AgentToolsError: If either is invalid or no project is known.
        """
        return self._project(project), _name(repository, "repository slug")

    def _pull_path(self, project: str | None, repository: str, pull_request_id: int) -> str:
        """Build the API path of one pull request.

        Args:
            project: Project key, or None for the default project.
            repository: Repository slug.
            pull_request_id: Pull request number within the repository.

        Returns:
            The path, without a trailing slash.

        Raises:
            AgentToolsError: If a part is invalid.
        """
        key, slug, number = self.pull_request_ref(project, repository, pull_request_id)
        return f"{self._api}/projects/{key}/repos/{slug}/pull-requests/{number}"

    async def _page(
        self, path: str, params: dict[str, str | int], what: str
    ) -> tuple[list[JsonObject], bool]:
        """Fetch one page of a paged resource.

        Args:
            path: API path of the resource.
            params: Query parameters, including ``limit``.
            what: Name of the response, used in error messages.

        Returns:
            The page's items, and whether the server has more.

        Raises:
            AgentToolsError: If the request fails.
        """
        body = as_object(await self._core.get(path, params=params), what)
        return objects(body.get("values")), body.get("isLastPage") is False

    @property
    def dry_run(self) -> bool:
        """True while the adapter blocks writes."""
        return self._core.dry_run

    def pull_request_ref(
        self, project: str | None, repository: str, pull_request_id: int
    ) -> tuple[str, str, int]:
        """Resolve and validate what names one pull request.

        Args:
            project: Project key, or None for the default project.
            repository: Repository slug.
            pull_request_id: Pull request number within the repository.

        Returns:
            The project key, repository slug and number.

        Raises:
            AgentToolsError: If a part is invalid or no project is known.
        """
        if pull_request_id < 1:
            raise AgentToolsError(f"{pull_request_id} is not a valid pull request number.")
        key, slug = self._repo(project, repository)
        return key, slug, pull_request_id

    def pull_request_url(self, project: str, repository: str, pull_request_id: int) -> str:
        """Build the browser URL of a pull request.

        Args:
            project: Project key.
            repository: Repository slug.
            pull_request_id: Pull request number.

        Returns:
            The address of the pull request's overview page.
        """
        return (
            f"{self.base_url}/projects/{project}/repos/{repository}/pull-requests/"
            f"{pull_request_id}/overview"
        )

    async def user_slug(self, required: bool = True) -> str | None:
        """Find the signed-in user's slug, asking Bitbucket once per server run.

        Bitbucket's REST API 1.0 has no "who am I" resource, so this reads the
        user name from the ``applinks/whoami`` servlet or, when that answers
        nothing (it ignores bearer tokens on some versions), from the
        ``X-AUSERNAME`` header Bitbucket puts on its responses. It then looks
        up the name's slug. ``user_slug`` under ``[bitbucket]`` skips all of this.

        Args:
            required: Raise when the user cannot be found, instead of returning None.

        Returns:
            The slug, or None when it is unknown and not required.

        Raises:
            AgentToolsError: If it is required and cannot be found.
        """
        if self._user_slug is None:
            try:
                name = (
                    await self._core.get_text(
                        f"{self._settings.path}/plugins/servlet/applinks/whoami"
                    )
                ).strip() or self._core.username
                if name:
                    found = await self._core.get(f"{self._api}/users/{quote(name, safe='')}")
                    self._user_slug = text(found, "slug") or name
            except AgentToolsError:
                if required:
                    raise
        if self._user_slug is None and required:
            raise AgentToolsError(
                "Bitbucket did not say who the access token belongs to, so the review cannot "
                "be recorded. Set user_slug under [bitbucket] in the config to your Bitbucket "
                "user slug (shown in your profile URL) and restart the server."
            )
        return self._user_slug

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
            "url": _first_link(pull),
        }

    async def server_version(self) -> str | None:
        """Fetch the Bitbucket server version.

        Returns:
            The version string, or None if the server does not report one.

        Raises:
            AgentToolsError: If the request fails.
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
            AgentToolsError: If a parameter is invalid or the request fails.
        """
        chosen_role = _choice(role, ROLES, "role")
        chosen_state = _choice(state, STATES, "state")
        params: dict[str, str | int] = {
            "role": chosen_role.upper(),
            "limit": self._limit(max_results),
            "order": "NEWEST",
        }
        if chosen_state != "all":
            params["state"] = chosen_state.upper()
        items, more = await self._page(
            f"{self._api}/dashboard/pull-requests", params, "pull request list"
        )
        pulls = [PullRequestSummary.model_validate(self._summary_fields(item)) for item in items]
        return PullRequestList(
            role=chosen_role,
            state=chosen_state,
            returned=len(pulls),
            more_available=more,
            pull_requests=pulls,
        )

    async def list_projects(
        self, name: str | None = None, max_results: int | None = None
    ) -> ProjectList:
        """List the projects the user can see.

        Args:
            name: Only projects whose name contains this text, or None for all.
            max_results: Row cap; defaults to the configured value, limited to 100.

        Returns:
            The matching projects.

        Raises:
            AgentToolsError: If the request fails.
        """
        params: dict[str, str | int] = {"limit": self._limit(max_results)}
        if name and name.strip():
            params["name"] = name.strip()
        items, more = await self._page(f"{self._api}/projects", params, "project list")
        projects = [
            Project(
                key=text(item, "key") or "",
                name=text(item, "name") or "",
                description=text(item, "description"),
                url=_first_link(item),
            )
            for item in items
        ]
        return ProjectList(returned=len(projects), more_available=more, projects=projects)

    async def set_default_project(self, project: str) -> DefaultProject:
        """Make a project the default for the rest of this server run.

        The project is looked up first, so a wrong key is caught here rather
        than on the next call.

        Args:
            project: Project key.

        Returns:
            The project now used by default.

        Raises:
            AgentToolsError: If the key is invalid or the project cannot be read.
        """
        key = _name(project, "project key")
        found = as_object(await self._core.get(f"{self._api}/projects/{key}"), "project")
        self._default_project = text(found, "key") or key
        return DefaultProject(
            key=self._default_project,
            name=text(found, "name"),
            message=(
                f"{self._default_project} is the default Bitbucket project until the server "
                "restarts. To keep it, set default_project under [bitbucket] in the config."
            ),
        )

    async def list_repositories(
        self, project: str | None = None, max_results: int | None = None
    ) -> RepositoryList:
        """List the repositories in a project.

        Args:
            project: Project key, or None for the default project.
            max_results: Row cap; defaults to the configured value, limited to 100.

        Returns:
            The repositories.

        Raises:
            AgentToolsError: If no project is known or the request fails.
        """
        key = self._project(project)
        items, more = await self._page(
            f"{self._api}/projects/{key}/repos",
            {"limit": self._limit(max_results)},
            "repository list",
        )
        repositories = [
            Repository(
                project=text(item, "project", "key") or key,
                slug=text(item, "slug") or "",
                name=text(item, "name") or "",
                description=text(item, "description"),
                url=_first_link(item),
            )
            for item in items
        ]
        return RepositoryList(
            project=key, returned=len(repositories), more_available=more, repositories=repositories
        )

    async def list_repository_pull_requests(
        self,
        repository: str,
        project: str | None = None,
        state: str = "open",
        filter_text: str | None = None,
        max_results: int | None = None,
    ) -> RepositoryPullRequests:
        """List the pull requests in one repository, whoever is involved.

        Args:
            repository: Repository slug.
            project: Project key, or None for the default project.
            state: open, merged, declined or all.
            filter_text: Only pull requests whose title or description contains this text.
            max_results: Row cap; defaults to the configured value, limited to 100.

        Returns:
            The pull requests, newest first.

        Raises:
            AgentToolsError: If a parameter is invalid or the request fails.
        """
        key = self._project(project)
        slug = _name(repository, "repository slug")
        chosen_state = _choice(state, STATES, "state")
        params: dict[str, str | int] = {
            "state": chosen_state.upper(),
            "order": "NEWEST",
            "limit": self._limit(max_results),
        }
        if filter_text and filter_text.strip():
            params["filterText"] = filter_text.strip()
        items, more = await self._page(
            f"{self._api}/projects/{key}/repos/{slug}/pull-requests", params, "pull request list"
        )
        pulls = [PullRequestSummary.model_validate(self._summary_fields(item)) for item in items]
        return RepositoryPullRequests(
            project=key,
            repository=slug,
            state=chosen_state,
            returned=len(pulls),
            more_available=more,
            pull_requests=pulls,
        )

    async def get_pull_request_commits(
        self,
        repository: str,
        pull_request_id: int,
        project: str | None = None,
        max_results: int | None = None,
    ) -> PullRequestCommits:
        """List the commits in a pull request.

        Args:
            repository: Repository slug.
            pull_request_id: Pull request number within the repository.
            project: Project key, or None for the default project.
            max_results: Row cap; defaults to the configured value, limited to 100.

        Returns:
            The commits, newest first.

        Raises:
            AgentToolsError: If a parameter is invalid or the request fails.
        """
        base = self._pull_path(project, repository, pull_request_id)
        items, more = await self._page(
            f"{base}/commits", {"limit": self._limit(max_results)}, "commit list"
        )
        commits = [
            Commit(
                id=text(item, "id") or "",
                short_id=text(item, "displayId"),
                author=text(item, "author", "displayName") or text(item, "author", "name"),
                authored=_timestamp(item, "authorTimestamp"),
                message=text(item, "message") or "",
            )
            for item in items
        ]
        return PullRequestCommits(returned=len(commits), more_available=more, commits=commits)

    async def get_pull_request_changes(
        self,
        repository: str,
        pull_request_id: int,
        project: str | None = None,
        max_results: int | None = None,
    ) -> PullRequestChanges:
        """List the files a pull request changes.

        Args:
            repository: Repository slug.
            pull_request_id: Pull request number within the repository.
            project: Project key, or None for the default project.
            max_results: Row cap; defaults to the configured value, limited to 100.

        Returns:
            The changed files.

        Raises:
            AgentToolsError: If a parameter is invalid or the request fails.
        """
        base = self._pull_path(project, repository, pull_request_id)
        items, more = await self._page(
            f"{base}/changes", {"limit": self._limit(max_results)}, "change list"
        )
        files = [
            ChangedFile(
                path=text(item, "path", "toString") or "",
                change=text(item, "type") or "UNKNOWN",
                previous_path=text(item, "srcPath", "toString"),
            )
            for item in items
        ]
        return PullRequestChanges(returned=len(files), more_available=more, files=files)

    async def get_pull_request_diff(
        self,
        repository: str,
        pull_request_id: int,
        project: str | None = None,
        context_lines: int = 3,
        max_chars: int = DEFAULT_DIFF_CHARS,
    ) -> PullRequestDiff:
        """Fetch the unified diff of a pull request, cut to a size an agent can read.

        Args:
            repository: Repository slug.
            pull_request_id: Pull request number within the repository.
            project: Project key, or None for the default project.
            context_lines: Unchanged lines to show around each change.
            max_chars: Longest diff to return, limited to 100,000 characters.

        Returns:
            The diff text and whether it was cut.

        Raises:
            AgentToolsError: If a parameter is invalid or the request fails.
        """
        base = self._pull_path(project, repository, pull_request_id)
        whole = await self._core.get_text(
            f"{base}.diff", params={"contextLines": max(0, min(context_lines, 50))}
        )
        limit = max(1, min(max_chars, MAX_DIFF_CHARS))
        return PullRequestDiff(
            diff=whole[:limit], total_chars=len(whole), truncated=len(whole) > limit
        )

    async def search_code(
        self,
        query: str,
        project: str | None = None,
        repository: str | None = None,
        max_results: int | None = None,
    ) -> CodeSearchResult:
        """Search file contents across repositories.

        This uses the endpoint Bitbucket's own search page calls. Atlassian
        does not document it, so the response is read defensively and a
        server without it is reported plainly. The default project is not
        applied: a search covers everything unless a project is given.

        Args:
            query: Search text, which may use Bitbucket's search modifiers.
            project: Only search this project (adds a ``project:`` modifier).
            repository: Only search this repository (adds a ``repo:`` modifier).
            max_results: File cap; defaults to the configured value, limited to 100.

        Returns:
            The matching files with their matching lines.

        Raises:
            AgentToolsError: If the query is empty, search is unavailable, or the request fails.
        """
        terms = query.strip()
        if not terms:
            raise AgentToolsError("The search query is empty.")
        if project and project.strip():
            terms = f"{terms} project:{_name(project, 'project key')}"
        if repository and repository.strip():
            terms = f"{terms} repo:{_name(repository, 'repository slug')}"
        payload: dict[str, JsonValue] = {
            "query": terms,
            "entities": {"code": {}},
            "limits": {"primary": self._limit(max_results), "secondary": 10},
        }
        try:
            body = as_object(await self._core.query(self._search, payload), "search")
        except NotFoundError as exc:
            raise AgentToolsError(
                "This Bitbucket did not answer at its search address. Code search uses an "
                "endpoint Atlassian does not document (rest/search/1.0/search); it may be "
                "missing from this version or switched off."
            ) from exc
        except ApiError as exc:
            if exc.status < 500:
                raise
            raise AgentToolsError(
                f"Bitbucket's code search is unavailable right now (HTTP {exc.status}). Its "
                "search index is probably down or being rebuilt, which also breaks the search "
                "page in the browser; a Bitbucket administrator has to fix it. Tell the user. "
                "Do not try to reproduce the search by listing or reading repositories one "
                "by one."
            ) from exc
        code = body.get("code")
        hits = [self._code_hit(item) for item in objects(value(code, "values"))]
        return CodeSearchResult(
            query=terms,
            total=number(code, "count"),
            returned=len(hits),
            more_available=isinstance(code, dict) and code.get("isLastPage") is False,
            hits=hits,
        )

    def _code_hit(self, item: JsonObject) -> CodeHit:
        """Build one search hit from the server's entry for a file.

        Args:
            item: One decoded entry of the search response.

        Returns:
            The hit, with highlighting markup removed from its lines.
        """
        project = text(item, "repository", "project", "key") or ""
        slug = text(item, "repository", "slug") or ""
        file = text(item, "file") or ""
        lines: list[CodeLine] = []
        contexts = item.get("hitContexts")
        for group in contexts if isinstance(contexts, list) else []:
            for entry in objects(group):
                lines.append(
                    CodeLine(
                        line=number(entry, "line"),
                        text=html.unescape(_HIGHLIGHT.sub("", text(entry, "text") or "")),
                    )
                )
        return CodeHit(
            project=project,
            repository=slug,
            file=file,
            hit_count=number(item, "hitCount"),
            lines=lines[:MAX_LINES_PER_HIT],
            url=(
                f"{self.base_url}/projects/{project}/repos/{slug}/browse/{file}"
                if project and slug and file
                else None
            ),
        )

    async def get_pull_request(
        self,
        repository: str,
        pull_request_id: int,
        project: str | None = None,
        max_comments: int = 10,
    ) -> PullRequestDetail:
        """Fetch one pull request with its description, recent comments and builds.

        Args:
            repository: Repository slug.
            pull_request_id: Pull request number within the repository.
            project: Project key, or None for the default project.
            max_comments: How many of the newest comments to include.

        Returns:
            The pull request detail.

        Raises:
            AgentToolsError: If a parameter is invalid or the request fails.
        """
        base = self._pull_path(project, repository, pull_request_id)
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
                    _build(item)
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

    async def get_commit_builds(
        self, commit_id: str, max_results: int | None = None
    ) -> CommitBuilds:
        """Fetch the build results CI servers reported for a commit.

        Build results are stored per commit, not per repository, so no
        project or repository is needed.

        Args:
            commit_id: Full or abbreviated commit hash.
            max_results: Row cap; defaults to the configured value, limited to 100.

        Returns:
            Totals by state, and the builds, newest first.

        Raises:
            AgentToolsError: If the hash is invalid or the request fails.
        """
        commit = _commit(commit_id)
        stats = as_object(
            await self._core.get(f"{self._builds}/commits/stats/{commit}"), "build statistics"
        )
        items, more = await self._page(
            f"{self._builds}/commits/{commit}",
            {"limit": self._limit(max_results), "orderBy": "NEWEST"},
            "build status",
        )
        builds = [_build(item) for item in items]
        return CommitBuilds(
            commit=commit,
            counts=BuildCounts(
                successful=number(stats, "successful") or 0,
                failed=number(stats, "failed") or 0,
                in_progress=number(stats, "inProgress") or 0,
                cancelled=number(stats, "cancelled") or 0,
                unknown=number(stats, "unknown") or 0,
            ),
            returned=len(builds),
            more_available=more,
            builds=builds,
        )

    async def list_required_builds(
        self, repository: str, project: str | None = None, max_results: int | None = None
    ) -> RequiredBuildList:
        """List the merge checks that need green builds before a merge.

        Args:
            repository: Repository slug.
            project: Project key, or None for the default project.
            max_results: Row cap; defaults to the configured value, limited to 100.

        Returns:
            The configured checks.

        Raises:
            AgentToolsError: If a parameter is invalid or the request fails.
        """
        key, slug = self._repo(project, repository)
        try:
            items, more = await self._page(
                f"{self._required}/projects/{key}/repos/{slug}/conditions",
                {"limit": self._limit(max_results)},
                "required builds",
            )
        except NotFoundError as exc:
            raise AgentToolsError(
                f"Bitbucket found no required-builds settings for {key}/{slug}. Either the "
                "repository does not exist or this Bitbucket is older than 7.14, which added "
                "required builds."
            ) from exc
        checks = [
            RequiredBuild(
                id=number(item, "id"),
                build_keys=_strings(item.get("buildParentKeys")),
                branches=_ref_matcher(item.get("refMatcher")),
                exempt_branches=_ref_matcher(item.get("exemptRefMatcher")),
            )
            for item in items
        ]
        return RequiredBuildList(
            project=key, repository=slug, returned=len(checks), more_available=more, checks=checks
        )

    async def get_deployment(
        self,
        repository: str,
        commit_id: str,
        deployment_key: str,
        environment_key: str,
        sequence_number: int,
        project: str | None = None,
    ) -> Deployment:
        """Fetch one deployment a CI or CD server reported against a commit.

        Bitbucket offers no way to list deployments, only to fetch one by its
        three identifiers, which come from the CI or CD server.

        Args:
            repository: Repository slug.
            commit_id: Commit the deployment delivered.
            deployment_key: Deployment key.
            environment_key: Environment key.
            sequence_number: Run number of the deployment.
            project: Project key, or None for the default project.

        Returns:
            The deployment.

        Raises:
            AgentToolsError: If a parameter is invalid or the request fails.
        """
        key, slug = self._repo(project, repository)
        commit = _commit(commit_id)
        if not deployment_key.strip() or not environment_key.strip():
            raise AgentToolsError("A deployment needs both its key and its environment key.")
        try:
            found = as_object(
                await self._core.get(
                    f"{self._api}/projects/{key}/repos/{slug}/commits/{commit}/deployments",
                    params={
                        "key": deployment_key.strip(),
                        "environmentKey": environment_key.strip(),
                        "deploymentSequenceNumber": sequence_number,
                    },
                ),
                "deployment",
            )
        except NotFoundError as exc:
            raise AgentToolsError(
                f"No deployment '{deployment_key}' to '{environment_key}' (run "
                f"{sequence_number}) is recorded against commit {commit} in {key}/{slug}. "
                "Bitbucket cannot list deployments, so check the identifiers on the CI or CD "
                "server; deployments also need Bitbucket 7.16 or later."
            ) from exc
        environment = found.get("environment")
        return Deployment(
            key=text(found, "key") or deployment_key.strip(),
            name=text(found, "displayName"),
            description=text(found, "description"),
            sequence_number=number(found, "deploymentSequenceNumber"),
            state=text(found, "state") or "UNKNOWN",
            environment=(
                DeploymentEnvironment(
                    key=text(environment, "key") or environment_key.strip(),
                    name=text(environment, "displayName"),
                    type=text(environment, "type"),
                    url=text(environment, "url"),
                )
                if isinstance(environment, dict)
                else None
            ),
            from_commit=text(found, "fromCommit", "id"),
            to_commit=text(found, "toCommit", "id"),
            url=text(found, "url"),
            updated=_timestamp(found, "lastUpdated"),
        )

    async def get_merge_status(
        self, repository: str, pull_request_id: int, project: str | None = None
    ) -> MergeStatus:
        """Check whether a pull request can be merged now, and what blocks it.

        Args:
            repository: Repository slug.
            pull_request_id: Pull request number within the repository.
            project: Project key, or None for the default project.

        Returns:
            Whether it can merge, conflicts, and each blocking merge check.

        Raises:
            AgentToolsError: If a parameter is invalid or the request fails.
        """
        base = self._pull_path(project, repository, pull_request_id)
        try:
            body = as_object(await self._core.get(f"{base}/merge"), "merge status")
        except ApiError as exc:
            if exc.status != HTTPStatus.CONFLICT:
                raise
            # Bitbucket answers 409 for a pull request that is already merged or declined.
            return MergeStatus(can_merge=False, conflicted=False, note=str(exc))
        return MergeStatus(
            can_merge=body.get("canMerge") is True,
            conflicted=body.get("conflicted") is True,
            outcome=text(body, "outcome"),
            vetoes=[
                MergeVeto(
                    summary=text(item, "summaryMessage") or "Blocked by a merge check.",
                    detail=text(item, "detailedMessage"),
                )
                for item in objects(body.get("vetoes"))
            ],
        )

    async def get_pull_request_activity(
        self,
        repository: str,
        pull_request_id: int,
        project: str | None = None,
        max_results: int | None = None,
    ) -> PullRequestActivity:
        """Fetch a pull request's history: pushes, comments, reviews and state changes.

        Args:
            repository: Repository slug.
            pull_request_id: Pull request number within the repository.
            project: Project key, or None for the default project.
            max_results: Row cap; defaults to the configured value, limited to 100.

        Returns:
            The events, newest first.

        Raises:
            AgentToolsError: If a parameter is invalid or the request fails.
        """
        base = self._pull_path(project, repository, pull_request_id)
        items, more = await self._page(
            f"{base}/activities", {"limit": self._limit(max_results)}, "pull request activity"
        )
        events = [_activity(item) for item in items]
        return PullRequestActivity(returned=len(events), more_available=more, activities=events)

    async def list_pull_request_tasks(
        self,
        repository: str,
        pull_request_id: int,
        project: str | None = None,
        state: str = "open",
        max_results: int | None = None,
    ) -> PullRequestTasks:
        """List a pull request's tasks, which Bitbucket stores as blocker comments.

        Args:
            repository: Repository slug.
            pull_request_id: Pull request number within the repository.
            project: Project key, or None for the default project.
            state: open, resolved or all.
            max_results: Row cap; defaults to the configured value, limited to 100.

        Returns:
            The tasks.

        Raises:
            AgentToolsError: If a parameter is invalid or the request fails.
        """
        chosen = _choice(state, TASK_STATES, "task state")
        base = self._pull_path(project, repository, pull_request_id)
        params: dict[str, str | int] = {"limit": self._limit(max_results)}
        if chosen != "all":
            params["state"] = chosen.upper()
        try:
            items, more = await self._page(f"{base}/blocker-comments", params, "task list")
        except NotFoundError as exc:
            raise AgentToolsError(
                f"Bitbucket could not list tasks on pull request {pull_request_id}. Either the "
                "pull request does not exist or this Bitbucket is older than 7.2, which moved "
                "tasks to blocker comments."
            ) from exc
        tasks = [
            PullRequestTask(
                id=number(item, "id"),
                text=text(item, "text") or "",
                state=text(item, "state") or "OPEN",
                author=text(item, "author", "displayName") or text(item, "author", "name"),
                created=_timestamp(item, "createdDate"),
                resolved_by=text(item, "resolver", "displayName"),
                resolved=_timestamp(item, "resolvedDate"),
            )
            for item in items
        ]
        return PullRequestTasks(state=chosen, returned=len(tasks), more_available=more, tasks=tasks)

    async def get_pull_request_issues(
        self, repository: str, pull_request_id: int, project: str | None = None
    ) -> PullRequestIssues:
        """List the Jira issues a pull request's commits and title mention.

        This needs Bitbucket's application link to Jira; without it the
        server has no such address.

        Args:
            repository: Repository slug.
            pull_request_id: Pull request number within the repository.
            project: Project key, or None for the default project.

        Returns:
            The linked issues.

        Raises:
            AgentToolsError: If a parameter is invalid or the request fails.
        """
        api_path = self._pull_path(project, repository, pull_request_id)
        path = f"{self._jira}{api_path.removeprefix(self._api)}/issues"
        try:
            found = await self._core.get(path)
        except NotFoundError as exc:
            raise AgentToolsError(
                f"Bitbucket could not list Jira issues for pull request {pull_request_id}. "
                "Either the pull request does not exist or this Bitbucket is not linked to Jira."
            ) from exc
        issues = [
            LinkedIssue(key=text(item, "key") or "", url=text(item, "url"))
            for item in objects(found)
            if text(item, "key")
        ]
        return PullRequestIssues(returned=len(issues), issues=issues)

    async def add_comment(
        self,
        repository: str,
        pull_request_id: int,
        comment: str,
        project: str | None = None,
        reply_to: int | None = None,
        task: bool = False,
    ) -> WriteOutcome:
        """Add a comment, reply or task to a pull request (blocked when dry-run is on).

        Args:
            repository: Repository slug.
            pull_request_id: Pull request number within the repository.
            comment: Comment text in Markdown.
            project: Project key, or None for the default project.
            reply_to: Identifier of the comment to reply to, or None for a new thread.
            task: Post it as a task (a blocker comment) that must be resolved.

        Returns:
            The write outcome.

        Raises:
            AgentToolsError: If a parameter is invalid or the request fails.
        """
        base = self._pull_path(project, repository, pull_request_id)
        if not comment.strip():
            raise AgentToolsError("Comment text must not be empty.")
        if reply_to is not None and reply_to < 1:
            raise AgentToolsError(f"{reply_to} is not a valid comment identifier.")
        payload: dict[str, JsonValue] = {"text": comment}
        if reply_to is not None:
            payload["parent"] = {"id": reply_to}
        return await self._core.write(
            "POST", f"{base}/{'blocker-comments' if task else 'comments'}", payload
        )

    async def set_review(
        self,
        repository: str,
        pull_request_id: int,
        review: str,
        project: str | None = None,
    ) -> tuple[str, WriteOutcome]:
        """Approve a pull request, mark it as needing work, or withdraw either.

        The user is looked up even in dry-run, so a missing slug is reported
        either way.

        Args:
            repository: Repository slug.
            pull_request_id: Pull request number within the repository.
            review: approve, needs_work or unapprove.
            project: Project key, or None for the default project.

        Returns:
            Bitbucket's name for the new status, and the write outcome.

        Raises:
            AgentToolsError: If a parameter is invalid, the user is unknown, or the
                request fails (for example on the user's own pull request).
        """
        choice = normalize_review(review)
        base = self._pull_path(project, repository, pull_request_id)
        slug = await self.user_slug()
        status = REVIEWS[choice]
        outcome = await self._core.write(
            "PUT", f"{base}/participants/{quote(slug or '', safe='')}", {"status": status}
        )
        return status, outcome

    async def issue_commits(
        self, issue_key: str, max_results: int | None = None
    ) -> tuple[list[IssueCommit], bool]:
        """List commits, in any repository, whose messages mention a Jira issue key.

        This uses Bitbucket's Jira integration, which needs the application
        link to Jira.

        Args:
            issue_key: Jira issue key, such as ABC-123.
            max_results: Row cap; defaults to the configured value, limited to 100.

        Returns:
            The commits, and whether the server has more.

        Raises:
            AgentToolsError: If the key is invalid, Bitbucket is not linked to Jira, or
                the request fails.
        """
        key = issue_key.strip().upper()
        if not _ISSUE_KEY.fullmatch(key):
            raise AgentToolsError(f"'{issue_key}' is not a valid issue key.")
        try:
            items, more = await self._page(
                f"{self._jira}/issues/{key}/commits",
                {"limit": self._limit(max_results), "maxChanges": 0},
                "issue commits",
            )
        except NotFoundError as exc:
            raise AgentToolsError(
                "Bitbucket could not list commits for a Jira issue. It is probably not "
                "linked to Jira."
            ) from exc
        commits = [
            IssueCommit(
                project=text(item, "repository", "project", "key") or "",
                repository=text(item, "repository", "slug") or "",
                id=text(item, "toCommit", "id") or "",
                short_id=text(item, "toCommit", "displayId"),
                author=text(item, "toCommit", "author", "displayName")
                or text(item, "toCommit", "author", "name"),
                authored=_timestamp(item.get("toCommit"), "authorTimestamp"),
                message=text(item, "toCommit", "message") or "",
            )
            for item in items
        ]
        return [c for c in commits if c.project and c.repository and c.id], more

    async def commit_pull_requests(
        self, project: str, repository: str, commit_id: str
    ) -> list[PullRequestSummary]:
        """List the pull requests in a repository that contain a commit.

        Args:
            project: Project key.
            repository: Repository slug.
            commit_id: Commit hash.

        Returns:
            The pull requests.

        Raises:
            AgentToolsError: If a part is invalid or the request fails.
        """
        key, slug = self._repo(project, repository)
        items, _ = await self._page(
            f"{self._api}/projects/{key}/repos/{slug}/commits/{_commit(commit_id)}/pull-requests",
            {"limit": MAX_RESULTS_LIMIT},
            "pull request list",
        )
        return [PullRequestSummary.model_validate(self._summary_fields(item)) for item in items]
