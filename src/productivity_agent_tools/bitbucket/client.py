"""Bitbucket Data Center REST client (API 1.0) built on an ``ApiClient`` adapter."""

import html
import re
from datetime import UTC, datetime

from pydantic import JsonValue

from productivity_agent_tools.api import ApiClient
from productivity_agent_tools.bitbucket.models import (
    BuildStatus,
    ChangedFile,
    CodeHit,
    CodeLine,
    CodeSearchResult,
    Commit,
    DefaultProject,
    Project,
    ProjectList,
    PullRequestChanges,
    PullRequestComment,
    PullRequestCommits,
    PullRequestDetail,
    PullRequestDiff,
    PullRequestList,
    PullRequestSummary,
    Repository,
    RepositoryList,
    RepositoryPullRequests,
    Reviewer,
)
from productivity_agent_tools.config import BitbucketSettings
from productivity_agent_tools.errors import ApiError, AtlassianError, NotFoundError
from productivity_agent_tools.payload import JsonObject, as_object, number, objects, text, value

ROLES: tuple[str, ...] = ("reviewer", "author", "participant")
STATES: tuple[str, ...] = ("open", "merged", "declined", "all")
MAX_RESULTS_LIMIT: int = 100
DEFAULT_DIFF_CHARS: int = 20_000
MAX_DIFF_CHARS: int = 100_000
_ACTIVITY_PAGE: int = 100
MAX_LINES_PER_HIT: int = 12
_HIGHLIGHT = re.compile(r"</?em>")
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
        self._search = f"{settings.path}/rest/search/1.0/search"
        self._default_project = settings.default_project

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
            AtlassianError: If no project is given and none is set as default.
        """
        chosen = project.strip() if project else self._default_project
        if not chosen:
            raise AtlassianError(
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

    def _pull_path(self, project: str | None, repository: str, pull_request_id: int) -> str:
        """Build the API path of one pull request.

        Args:
            project: Project key, or None for the default project.
            repository: Repository slug.
            pull_request_id: Pull request number within the repository.

        Returns:
            The path, without a trailing slash.

        Raises:
            AtlassianError: If a part is invalid.
        """
        if pull_request_id < 1:
            raise AtlassianError(f"{pull_request_id} is not a valid pull request number.")
        return (
            f"{self._api}/projects/{self._project(project)}"
            f"/repos/{_name(repository, 'repository slug')}/pull-requests/{pull_request_id}"
        )

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
            AtlassianError: If the request fails.
        """
        body = as_object(await self._core.get(path, params=params), what)
        return objects(body.get("values")), body.get("isLastPage") is False

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
            AtlassianError: If the request fails.
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
            AtlassianError: If the key is invalid or the project cannot be read.
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
            AtlassianError: If no project is known or the request fails.
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
            AtlassianError: If a parameter is invalid or the request fails.
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
            AtlassianError: If a parameter is invalid or the request fails.
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
            AtlassianError: If a parameter is invalid or the request fails.
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
            AtlassianError: If a parameter is invalid or the request fails.
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
            AtlassianError: If the query is empty, search is unavailable, or the request fails.
        """
        terms = query.strip()
        if not terms:
            raise AtlassianError("The search query is empty.")
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
            raise AtlassianError(
                "This Bitbucket did not answer at its search address. Code search uses an "
                "endpoint Atlassian does not document (rest/search/1.0/search); it may be "
                "missing from this version or switched off."
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
            AtlassianError: If a parameter is invalid or the request fails.
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
