"""Confluence Data Center REST client built on an ``ApiClient`` adapter."""

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote

from pydantic import JsonValue

from productivity_agent_tools.api import ApiClient, WriteOutcome
from productivity_agent_tools.confluence.markdown import markdown_to_storage
from productivity_agent_tools.confluence.models import (
    Page,
    PageAncestor,
    PageList,
    PageSummary,
    Space,
    SpaceList,
)
from productivity_agent_tools.confluence.settings import ConfluenceSettings
from productivity_agent_tools.errors import AgentToolsError, NotFoundError
from productivity_agent_tools.payload import JsonObject, as_object, number, objects, text, value

MAX_RESULTS_LIMIT: int = 100
DEFAULT_BODY_CHARS: int = 20_000
MAX_BODY_CHARS: int = 500_000
_PAGE_EXPAND: str = "body.storage,version,space,ancestors,metadata.labels"
_SUMMARY_EXPAND: str = "version,space"
_PAGE_ID = re.compile(r"^[0-9]+$")
# Global keys are letters and digits; personal ones are "~" plus a username or user key.
_SPACE_KEY = re.compile(r"^~?[A-Za-z0-9_.@+\-]+$")

type BodyFormat = Literal["markdown", "storage"]
type UpdateMode = Literal["replace", "append", "prepend"]
type SpaceType = Literal["global", "personal"]


def page_id_of(page_id: str | int) -> str:
    """Validate a content id.

    Args:
        page_id: The id as given by the caller.

    Returns:
        The id as a string of digits.

    Raises:
        AgentToolsError: If it is not a number.
    """
    cleaned = str(page_id).strip()
    if not _PAGE_ID.fullmatch(cleaned):
        raise AgentToolsError(
            f"'{page_id}' is not a page id. Ids are numbers; confluence_search and "
            "confluence_get_page show them."
        )
    return cleaned


def space_key_of(key: str) -> str:
    """Validate a space key.

    Args:
        key: The key as given by the caller, such as ``OPS`` or ``~jdoe``.

    Returns:
        The key without surrounding whitespace.

    Raises:
        AgentToolsError: If it is not shaped like a space key.
    """
    cleaned = key.strip()
    if not _SPACE_KEY.fullmatch(cleaned):
        raise AgentToolsError(
            f"'{key}' is not a space key. confluence_list_spaces shows them; a personal "
            "space's key is ~ followed by the username."
        )
    return cleaned


def to_storage(body: str, body_format: BodyFormat) -> str:
    """Turn a page body as the agent wrote it into storage format.

    Args:
        body: The body text.
        body_format: ``markdown`` to convert, or ``storage`` to use as written.

    Returns:
        The body in storage format.

    Raises:
        AgentToolsError: If the body is empty or the format is unknown.
    """
    if not body.strip():
        raise AgentToolsError("The page body must not be empty.")
    if body_format == "markdown":
        return markdown_to_storage(body)
    if body_format == "storage":
        return body.strip()
    raise AgentToolsError(f"body_format must be markdown or storage, not '{body_format}'.")


@dataclass(frozen=True, slots=True)
class PageUpdate:
    """A page edit worked out against the page's current state.

    Attributes:
        page: The page as it was before the edit, with its full body.
        title: The title after the edit.
        body: The full body after the edit, in storage format.
        version: The version number the edit creates.
    """

    page: Page
    title: str
    body: str
    version: int


class ConfluenceClient:
    """Operations on Confluence spaces and pages, returning typed results."""

    def __init__(self, core: ApiClient, settings: ConfluenceSettings) -> None:
        """Create the client.

        Args:
            core: API adapter connected to Confluence's host.
            settings: Confluence module settings.
        """
        self._core = core
        self._settings = settings
        self._api = f"{settings.path}/rest/api"
        self._default_space = settings.default_space

    @property
    def settings(self) -> ConfluenceSettings:
        """The Confluence module settings this client was built with."""
        return self._settings

    @property
    def base_url(self) -> str:
        """Browser base URL of this Confluence instance."""
        return f"{self._core.base_url}{self._settings.path}"

    @property
    def default_space(self) -> str | None:
        """Space key used when a call names no space, if one is set."""
        return self._default_space

    async def set_default_space(self, key: str) -> Space:
        """Choose the space used when a call names none, for the rest of this session.

        Args:
            key: Space key, checked against the server.

        Returns:
            The space.

        Raises:
            AgentToolsError: If the key is invalid, the space does not exist or is hidden
                from the user, or the request fails.
        """
        space = await self.get_space(key)
        self._default_space = space.key
        return space

    async def get_space(self, key: str) -> Space:
        """Fetch one space.

        Args:
            key: Space key.

        Returns:
            The space.

        Raises:
            AgentToolsError: If the key is invalid, the space does not exist or is hidden
                from the user, or the request fails.
        """
        clean = space_key_of(key)
        found = await self._core.get(f"{self._api}/space/{quote(clean, safe='~')}")
        return self._space(as_object(found, "space"))

    def _space(self, item: JsonObject) -> Space:
        """Build the view of a space.

        Args:
            item: One decoded space object.

        Returns:
            The space.
        """
        return Space(
            key=text(item, "key") or "",
            name=text(item, "name"),
            type=text(item, "type"),
            url=self._url(item),
        )

    def space(self, key: str | None) -> str:
        """Pick the space for a call: the one given, else the default.

        Args:
            key: Space key from the caller, or None.

        Returns:
            The validated space key.

        Raises:
            AgentToolsError: If no space is given and none is set as default.
        """
        chosen = key.strip() if key else self._default_space
        if not chosen:
            raise AgentToolsError(
                "No space was given and no default space is set. Pass a space key, call "
                "confluence_set_default_space, or set default_space under [confluence] in "
                "the config. confluence_status shows the user's personal space."
            )
        return space_key_of(chosen)

    def _limit(self, max_results: int | None) -> int:
        """Clamp a requested row count to what one page of results may hold.

        Args:
            max_results: Requested row cap, or None for the configured default.

        Returns:
            A count between 1 and the limit.
        """
        return max(1, min(max_results or self._settings.default_max_results, MAX_RESULTS_LIMIT))

    def _url(self, item: JsonValue) -> str | None:
        """Build an item's browser URL from its ``webui`` link.

        Args:
            item: Decoded space or content object.

        Returns:
            The URL on this connection's host, or None when the item has no link.
        """
        webui = text(item, "_links", "webui")
        return f"{self.base_url}{webui}" if webui else None

    def _summary(self, item: JsonObject) -> PageSummary:
        """Build the compact view of a page.

        Args:
            item: One decoded content object.

        Returns:
            The page summary.
        """
        return PageSummary(
            id=text(item, "id") or "",
            title=text(item, "title") or "",
            type=text(item, "type") or "page",
            space=text(item, "space", "key"),
            version=number(item, "version", "number"),
            last_modified=text(item, "version", "when"),
            last_modified_by=text(item, "version", "by", "displayName"),
            url=self._url(item),
        )

    def _page(self, item: JsonObject, max_chars: int | None) -> Page:
        """Build the full view of a page.

        Args:
            item: One decoded content object, expanded with its body and ancestors.
            max_chars: Cut the body at this many characters; None keeps all of it.

        Returns:
            The page.
        """
        body = text(item, "body", "storage", "value") or ""
        limit = len(body) if max_chars is None else max(1, min(max_chars, MAX_BODY_CHARS))
        return Page(
            **self._summary(item).model_dump(),
            ancestors=[
                PageAncestor(id=text(a, "id") or "", title=text(a, "title") or "")
                for a in objects(value(item, "ancestors"))
            ],
            labels=[
                name
                for label in objects(value(item, "metadata", "labels", "results"))
                if (name := text(label, "name"))
            ],
            body=body[:limit],
            body_chars=len(body),
            truncated=len(body) > limit,
        )

    async def myself(self) -> JsonObject:
        """Fetch the signed-in user's profile.

        Returns:
            The decoded profile, with ``username`` and ``displayName``.

        Raises:
            AgentToolsError: If the request fails.
        """
        return as_object(await self._core.get(f"{self._api}/user/current"), "user")

    async def server_version(self) -> str | None:
        """Fetch the Confluence version.

        Returns:
            The version, or None if the server does not report it.

        Raises:
            AgentToolsError: If the request fails for a reason other than a missing resource.
        """
        try:
            return text(await self._core.get(f"{self._api}/server-information"), "version")
        except NotFoundError:
            return None

    async def personal_space(self, username: str) -> str | None:
        """Find a user's personal space.

        Args:
            username: The user's username.

        Returns:
            The space key, or None if the user has no personal space.

        Raises:
            AgentToolsError: If the request fails for a reason other than a missing space.
        """
        try:
            found = await self._core.get(f"{self._api}/space/{quote('~' + username, safe='~')}")
        except NotFoundError:
            return None
        return text(found, "key")

    async def list_spaces(
        self, space_type: SpaceType | None = None, max_results: int | None = None
    ) -> SpaceList:
        """List the spaces the user can see.

        Args:
            space_type: Only ``global`` or only ``personal`` spaces; None for both.
            max_results: Row cap.

        Returns:
            The spaces.

        Raises:
            AgentToolsError: If the request fails.
        """
        params: dict[str, str | int] = {"limit": self._limit(max_results)}
        if space_type is not None:
            params["type"] = space_type
        body = await self._core.get(f"{self._api}/space", params)
        spaces = [self._space(item) for item in objects(value(body, "results"))]
        return SpaceList(
            returned=len(spaces),
            more_available=text(body, "_links", "next") is not None,
            spaces=spaces,
        )

    async def search(self, cql: str, max_results: int | None = None) -> PageList:
        """Find content with a CQL query.

        Args:
            cql: The query, such as ``space = OPS AND title ~ "runbook"``.
            max_results: Row cap.

        Returns:
            The matching content, without bodies.

        Raises:
            AgentToolsError: If the query is empty or invalid, or the request fails.
        """
        if not cql.strip():
            raise AgentToolsError("The CQL query must not be empty.")
        body = await self._core.get(
            f"{self._api}/content/search",
            {"cql": cql, "limit": self._limit(max_results), "expand": _SUMMARY_EXPAND},
        )
        return self._page_list(body)

    def _page_list(self, body: JsonValue) -> PageList:
        """Build a page list from a paged content response.

        Args:
            body: The decoded response.

        Returns:
            The pages.
        """
        pages = [self._summary(item) for item in objects(value(body, "results"))]
        return PageList(
            returned=len(pages),
            more_available=text(body, "_links", "next") is not None,
            pages=pages,
        )

    async def get_page(self, page_id: str | int, max_chars: int | None = None) -> Page:
        """Fetch a page with its body.

        Args:
            page_id: Content id.
            max_chars: Cut the body at this many characters; None for the default.

        Returns:
            The page.

        Raises:
            AgentToolsError: If the id is invalid or the request fails.
        """
        found = await self._core.get(
            f"{self._api}/content/{page_id_of(page_id)}", {"expand": _PAGE_EXPAND}
        )
        return self._page(as_object(found, "page"), max_chars or DEFAULT_BODY_CHARS)

    async def _find(self, space: str, title: str, expand: str) -> JsonObject | None:
        """Look a page up by its exact title in a space.

        Args:
            space: Validated space key.
            title: The exact title.
            expand: Properties to expand.

        Returns:
            The decoded page, or None if there is none with that title.

        Raises:
            AgentToolsError: If the request fails.
        """
        body = await self._core.get(
            f"{self._api}/content",
            {"spaceKey": space, "title": title, "type": "page", "expand": expand},
        )
        found = objects(value(body, "results"))
        return found[0] if found else None

    async def find_page(
        self, title: str, space: str | None = None, max_chars: int | None = None
    ) -> Page:
        """Fetch a page by its exact title.

        Args:
            title: The page's exact title.
            space: Space key; None for the default space.
            max_chars: Cut the body at this many characters; None for the default.

        Returns:
            The page.

        Raises:
            AgentToolsError: If there is no such page, an argument is invalid, or the
                request fails.
        """
        key = self.space(space)
        found = await self._find(key, title.strip(), _PAGE_EXPAND)
        if found is None:
            raise NotFoundError(
                f"There is no page titled '{title.strip()}' in space {key}. Titles must match "
                'exactly; confluence_search with title ~ "..." finds near matches.'
            )
        return self._page(found, max_chars or DEFAULT_BODY_CHARS)

    async def child_pages(self, page_id: str | int, max_results: int | None = None) -> PageList:
        """List the pages directly under a page.

        Args:
            page_id: Content id of the parent.
            max_results: Row cap.

        Returns:
            The child pages.

        Raises:
            AgentToolsError: If the id is invalid or the request fails.
        """
        body = await self._core.get(
            f"{self._api}/content/{page_id_of(page_id)}/child/page",
            {"limit": self._limit(max_results), "expand": _SUMMARY_EXPAND},
        )
        return self._page_list(body)

    async def create_page(
        self,
        title: str,
        body: str,
        space: str | None = None,
        parent_id: str | int | None = None,
        body_format: BodyFormat = "markdown",
    ) -> tuple[str, WriteOutcome]:
        """Create a page (blocked when dry-run is on).

        The title is checked first, even in dry-run, because Confluence
        refuses a second page with the same title in a space.

        Args:
            title: The new page's title.
            body: The body, in ``body_format``.
            space: Space key; None for the default space.
            parent_id: Content id of the page to put it under; None for the space's top level.
            body_format: ``markdown`` or ``storage``.

        Returns:
            The space key used, and the write outcome.

        Raises:
            AgentToolsError: If an argument is invalid, the title is taken, or the request
                fails.
        """
        key = self.space(space)
        clean_title = title.strip()
        if not clean_title:
            raise AgentToolsError("The page title must not be empty.")
        storage = to_storage(body, body_format)
        payload: dict[str, JsonValue] = {
            "type": "page",
            "title": clean_title,
            "space": {"key": key},
            "body": {"storage": {"value": storage, "representation": "storage"}},
        }
        if parent_id is not None:
            payload["ancestors"] = [{"id": page_id_of(parent_id)}]
        existing = await self._find(key, clean_title, "version")
        if existing is not None:
            raise AgentToolsError(
                f"Space {key} already has a page titled '{clean_title}' (id "
                f"{text(existing, 'id')}). Edit it with confluence_update_page, or choose "
                "another title."
            )
        return key, await self._core.write("POST", f"{self._api}/content", payload)

    async def plan_update(
        self,
        page_id: str | int,
        expected_version: int,
        body: str | None = None,
        title: str | None = None,
        body_format: BodyFormat = "markdown",
        mode: UpdateMode = "replace",
    ) -> PageUpdate:
        """Work out an edit against the page as it is now.

        Args:
            page_id: Content id.
            expected_version: The version the caller read; the edit is refused if the
                page has changed since.
            body: New body text in ``body_format``, or None to keep the body.
            title: New title, or None to keep it.
            body_format: ``markdown`` or ``storage``.
            mode: ``replace`` the body, or ``append`` or ``prepend`` the new text to it.

        Returns:
            The planned edit.

        Raises:
            AgentToolsError: If nothing would change, the page changed since it was read,
                an argument is invalid, or the request fails.
        """
        if body is None and (title is None or not title.strip()):
            raise AgentToolsError("Give a new body, a new title, or both.")
        if mode not in ("replace", "append", "prepend"):
            raise AgentToolsError(f"mode must be replace, append or prepend, not '{mode}'.")
        page = await self.get_page(page_id, MAX_BODY_CHARS)
        if page.truncated:
            raise AgentToolsError(
                f"Page {page.id} is longer than {MAX_BODY_CHARS} characters; edit it in the "
                "browser."
            )
        if page.version != expected_version:
            raise AgentToolsError(
                f"Page {page.id} is now at version {page.version}, not {expected_version}: "
                "someone changed it after it was read. Read it again with "
                "confluence_get_page, redo the edit on the new text, and pass its version."
            )
        new_body = page.body
        if body is not None:
            storage = to_storage(body, body_format)
            new_body = {
                "replace": storage,
                "append": f"{page.body}\n{storage}",
                "prepend": f"{storage}\n{page.body}",
            }[mode]
        return PageUpdate(
            page=page,
            title=title.strip() if title and title.strip() else page.title,
            body=new_body,
            version=expected_version + 1,
        )

    async def update_page(
        self,
        update: PageUpdate,
        parent_id: str | int | None = None,
        version_comment: str | None = None,
        minor_edit: bool = False,
    ) -> WriteOutcome:
        """Save a planned edit as the page's next version (blocked when dry-run is on).

        Args:
            update: The edit from ``plan_update``.
            parent_id: Move the page under this page; None keeps its place.
            version_comment: Note shown in the page history.
            minor_edit: True to skip notifying watchers.

        Returns:
            The write outcome.

        Raises:
            AgentToolsError: If the parent id is invalid or the request fails.
        """
        version: dict[str, JsonValue] = {"number": update.version, "minorEdit": minor_edit}
        if version_comment and version_comment.strip():
            version["message"] = version_comment.strip()
        payload: dict[str, JsonValue] = {
            "id": update.page.id,
            "type": update.page.type,
            "title": update.title,
            "space": {"key": update.page.space or ""},
            "version": version,
            "body": {"storage": {"value": update.body, "representation": "storage"}},
        }
        if parent_id is not None:
            payload["ancestors"] = [{"id": page_id_of(parent_id)}]
        return await self._core.write("PUT", f"{self._api}/content/{update.page.id}", payload)
