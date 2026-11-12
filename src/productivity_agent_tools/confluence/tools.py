"""MCP tool definitions for Confluence.

Only confluence_create_page and confluence_update_page change anything in
Confluence, and dry-run blocks both.
"""

from mcp.server.mcpserver import MCPServer

from productivity_agent_tools.api import WriteOutcome
from productivity_agent_tools.confluence.client import (
    BodyFormat,
    ConfluenceClient,
    SpaceType,
    UpdateMode,
)
from productivity_agent_tools.confluence.models import (
    ConfluenceStatus,
    DefaultSpace,
    Page,
    PageList,
    PageWriteResult,
    SpaceList,
)
from productivity_agent_tools.errors import AgentToolsError
from productivity_agent_tools.payload import number, text
from productivity_agent_tools.products import ToolContext
from productivity_agent_tools.repeats import RepeatGuard
from productivity_agent_tools.tooling import READ, SESSION_SETTING, WRITE, anticipated
from productivity_agent_tools.writes import once, report


def _write_result(
    action: str,
    outcome: WriteOutcome,
    *,
    title: str,
    space: str,
    page_id: str | None,
    done: str,
    base_url: str,
) -> PageWriteResult:
    """Convert a write outcome into the tool's result model.

    Args:
        action: Short name of the action.
        outcome: Outcome from the API adapter.
        title: The page's title.
        space: Space key.
        page_id: The page's id when known before the write.
        done: Sentence describing the change when it was sent.
        base_url: Confluence's browser base URL, for the page link.

    Returns:
        The tool result.
    """
    response = outcome.response
    sent_id = text(response, "id") or page_id
    webui = text(response, "_links", "webui")
    return PageWriteResult.model_validate(
        {
            **report(action, "Confluence", outcome, done),
            "page_id": sent_id,
            "title": title,
            "space": space,
            "version": number(response, "version", "number"),
            "url": f"{base_url}{webui}" if webui else None,
        }
    )


def register_confluence_tools(
    server: MCPServer,
    confluence: ConfluenceClient,
    context: ToolContext,
    repeats: RepeatGuard[PageWriteResult] | None = None,
) -> None:
    """Register every Confluence tool on an MCP server.

    Args:
        server: The MCP server to add tools to.
        confluence: Confluence client the tools call.
        context: Dry-run, token source and extra cookie names for the status tool.
        repeats: Guard against identical repeated writes; a default one is made when None.
    """
    guard = repeats if repeats is not None else RepeatGuard[PageWriteResult]()

    @server.tool(annotations=READ)
    @anticipated
    async def confluence_status() -> ConfluenceStatus:
        """Check the Confluence connection: who is signed in, their personal space, and dry-run.

        Call this first if another Confluence tool fails, to see what is wrong.
        The personal_space key is where the user's own pages, such as an
        "About me" page, usually go.
        """
        profile = await confluence.myself()
        username = text(profile, "username")
        return ConfluenceStatus(
            base_url=confluence.base_url,
            confluence_version=await confluence.server_version(),
            token_source=context.token_source,
            username=username,
            display_name=text(profile, "displayName"),
            personal_space=await confluence.personal_space(username) if username else None,
            default_space=confluence.default_space,
            dry_run=context.dry_run,
            extra_cookies=list(context.cookie_names),
        )

    @server.tool(annotations=READ)
    @anticipated
    async def confluence_list_spaces(
        space_type: SpaceType | None = None, max_results: int | None = None
    ) -> SpaceList:
        """List the Confluence spaces the user can see, with their keys.

        Args:
            space_type: global or personal; leave out for both.
            max_results: Maximum rows to return (default from config, at most 100).
        """
        return await confluence.list_spaces(space_type, max_results)

    @server.tool(annotations=SESSION_SETTING)
    @anticipated
    async def confluence_set_default_space(space: str) -> DefaultSpace:
        """Choose the space other Confluence tools use when a call names no space.

        Use this when the user says which space they work in. It lasts until
        the server restarts and changes nothing in Confluence.

        Args:
            space: Space key, such as OPS or ~jdoe for a personal space.
        """
        chosen = await confluence.set_default_space(space)
        return DefaultSpace(
            key=chosen.key,
            name=chosen.name,
            message=f"Space {chosen.key} is now the default until the server restarts.",
        )

    @server.tool(annotations=READ)
    @anticipated
    async def confluence_search(cql: str, max_results: int | None = None) -> PageList:
        """Search Confluence pages and blog posts with CQL, returning ids and titles.

        CQL examples: type = page AND space = OPS AND title ~ "runbook";
        siteSearch ~ "release checklist"; creator = currentUser() ORDER BY
        lastmodified DESC; label = onboarding. Use confluence_get_page with an
        id to read a page.

        Args:
            cql: Confluence Query Language query.
            max_results: Maximum rows to return (default from config, at most 100).
        """
        return await confluence.search(cql, max_results)

    @server.tool(annotations=READ)
    @anticipated
    async def confluence_get_page(
        page_id: str | None = None,
        title: str | None = None,
        space: str | None = None,
        max_chars: int | None = None,
    ) -> Page:
        """Read a page: its body in storage format, version, place in the tree and labels.

        Give either page_id, or the exact title (with space, unless a default
        space is set). Note the version: confluence_update_page needs it.

        Args:
            page_id: Content id, from confluence_search.
            title: Exact page title, instead of page_id.
            space: Space key for a title lookup; leave out to use the default space.
            max_chars: Cut the body here (default 20000, at most 500000).
        """
        if page_id:
            return await confluence.get_page(page_id, max_chars)
        if title and title.strip():
            return await confluence.find_page(title, space, max_chars)
        raise AgentToolsError("Give a page_id, or a title (and space).")

    @server.tool(annotations=READ)
    @anticipated
    async def confluence_list_child_pages(page_id: str, max_results: int | None = None) -> PageList:
        """List the pages directly under a page.

        Args:
            page_id: Content id of the parent page.
            max_results: Maximum rows to return (default from config, at most 100).
        """
        return await confluence.child_pages(page_id, max_results)

    @server.tool(annotations=WRITE)
    @anticipated
    async def confluence_create_page(
        title: str,
        body: str,
        space: str | None = None,
        parent_id: str | None = None,
        body_format: BodyFormat = "markdown",
    ) -> PageWriteResult:
        """Create a page. Visible to everyone who can see the space and attributed to the user.

        Confirm the title, space, parent and content with the user first. The
        title must be new in the space.

        Write the body in Markdown (the default): headings, lists, tables,
        links, **bold**, fenced code blocks (shown as Confluence code blocks)
        and images by URL. Raw HTML in Markdown is shown as text. For
        Confluence macros, layouts or mentions, pass body_format="storage" with
        storage-format XHTML instead.

        In dry-run mode nothing is sent and the result shows the request that
        would have been made, with the body converted to storage format. Call
        this once per page: an identical request within 30 seconds is not sent
        again.

        Args:
            title: Title of the new page; it must be unique in the space.
            body: Page content in body_format.
            space: Space key; leave out to use the default space. For the user's
                own pages, use personal_space from confluence_status.
            parent_id: Put the page under this page; leave out for the space's top level.
            body_format: markdown (default) or storage.
        """
        key = confluence.space(space)
        clean_title = title.strip()

        async def send() -> PageWriteResult:
            used, outcome = await confluence.create_page(
                clean_title, body, key, parent_id, body_format
            )
            return _write_result(
                "create_page",
                outcome,
                title=clean_title,
                space=used,
                page_id=None,
                done=f"Created page '{clean_title}' in space {used}.",
                base_url=confluence.base_url,
            )

        signature = ("create_page", key, clean_title, body, parent_id, body_format)
        return await once(guard, signature, send)

    @server.tool(annotations=WRITE)
    @anticipated
    async def confluence_update_page(
        page_id: str,
        version: int,
        body: str | None = None,
        title: str | None = None,
        mode: UpdateMode = "replace",
        body_format: BodyFormat = "markdown",
        parent_id: str | None = None,
        version_comment: str | None = None,
        minor_edit: bool = False,
    ) -> PageWriteResult:
        """Edit a page, saving a new version. Visible to everyone who can see the page.

        Read the page with confluence_get_page first and pass the version it
        showed: if anyone has changed the page since, nothing is saved and the
        result says to read it again. Confirm the change with the user first.

        mode=replace swaps the whole body for the one given, so start from the
        body you read (in body_format="storage") or rewrite all of it. Never
        replace with a body that was truncated. mode=append or prepend adds the
        given text after or before the existing body, which keeps the rest
        untouched. Leave body out to change only the title or parent.

        Markdown (the default) supports headings, lists, tables, links, bold,
        fenced code blocks and images by URL; use body_format="storage" for
        storage-format XHTML, such as the body read from confluence_get_page.

        In dry-run mode nothing is sent and the result shows the request that
        would have been made. Call this once per edit: an identical edit within
        30 seconds is not sent again.

        Args:
            page_id: Content id of the page.
            version: The page version you read, from confluence_get_page.
            body: New content in body_format; leave out to keep the body.
            title: New title; leave out to keep it.
            mode: replace (default), append or prepend.
            body_format: markdown (default) or storage.
            parent_id: Move the page under this page; leave out to keep its place.
            version_comment: Short note shown in the page history.
            minor_edit: True to skip notifying the page's watchers.
        """

        async def send() -> PageWriteResult:
            update = await confluence.plan_update(page_id, version, body, title, body_format, mode)
            outcome = await confluence.update_page(update, parent_id, version_comment, minor_edit)
            space = update.page.space or ""
            return _write_result(
                "update_page",
                outcome,
                title=update.title,
                space=space,
                page_id=update.page.id,
                done=f"Saved version {update.version} of '{update.title}' in space {space}.",
                base_url=confluence.base_url,
            )

        signature = (
            "update_page",
            page_id.strip(),
            version,
            body,
            title,
            mode,
            body_format,
            parent_id,
            version_comment,
            minor_edit,
        )
        return await once(guard, signature, send)
