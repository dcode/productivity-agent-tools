"""Result models returned by the Confluence MCP tools."""

from pydantic import BaseModel, Field

from productivity_agent_tools.writes import WriteReport


class ConfluenceStatus(BaseModel):
    """Connection status reported by the confluence_status tool."""

    base_url: str = Field(description="Confluence base URL in use.")
    confluence_version: str | None = Field(default=None, description="Server version.")
    token_source: str = Field(description="Where the access token was read from.")
    username: str | None = Field(default=None, description="Signed-in user's username.")
    display_name: str | None = Field(default=None, description="Signed-in user's display name.")
    personal_space: str | None = Field(
        default=None,
        description="Key of the user's personal space, such as ~jdoe; None if they have none.",
    )
    default_space: str | None = Field(
        default=None, description="Space key used when a tool call names no space."
    )
    dry_run: bool = Field(description="True if page creation and edits are currently blocked.")
    extra_cookies: list[str] = Field(
        description="Names of the extra cookies sent with each request (values are not shown)."
    )


class Space(BaseModel):
    """A Confluence space."""

    key: str = Field(description="Space key, used by the other confluence_ tools.")
    name: str | None = Field(default=None, description="Space name.")
    type: str | None = Field(default=None, description="global or personal.")
    url: str | None = Field(default=None, description="Browser URL of the space.")


class DefaultSpace(BaseModel):
    """Result of choosing the default Confluence space."""

    key: str = Field(description="Space key now used when a tool call names no space.")
    name: str | None = Field(default=None, description="Space name.")
    message: str = Field(description="Plain-language summary, including how long it lasts.")


class SpaceList(BaseModel):
    """Spaces the user can see."""

    returned: int = Field(description="Number of spaces in this result.")
    more_available: bool = Field(description="True if more spaces exist than were returned.")
    spaces: list[Space] = Field(description="The spaces.")


class PageSummary(BaseModel):
    """One page or blog post, without its body."""

    id: str = Field(description="Content id, used by the other confluence_ tools.")
    title: str = Field(description="Title.")
    type: str = Field(description="page or blogpost.")
    space: str | None = Field(default=None, description="Space key.")
    version: int | None = Field(default=None, description="Current version number.")
    last_modified: str | None = Field(default=None, description="When it last changed.")
    last_modified_by: str | None = Field(default=None, description="Who last changed it.")
    url: str | None = Field(default=None, description="Browser URL.")


class PageList(BaseModel):
    """Pages found by a search or listed under a parent."""

    returned: int = Field(description="Number of pages in this result.")
    more_available: bool = Field(description="True if more matched than were returned.")
    pages: list[PageSummary] = Field(description="The pages.")


class PageAncestor(BaseModel):
    """A page above another in the page tree."""

    id: str = Field(description="Content id.")
    title: str = Field(description="Title.")


class Page(PageSummary):
    """A page with its body in Confluence storage format."""

    ancestors: list[PageAncestor] = Field(
        description="Pages above this one, from the space's top down to its parent."
    )
    labels: list[str] = Field(description="Labels on the page.")
    can_edit: bool | None = Field(
        default=None,
        description="Whether the user may edit this page; None if the server did not say. "
        "When False, confluence_update_page will be refused: tell the user they need edit "
        "access from the page's owner or a space administrator.",
    )
    body: str = Field(
        description="Body in Confluence storage format (XHTML with ac: and ri: elements)."
    )
    body_chars: int = Field(description="Length of the full body in characters.")
    truncated: bool = Field(
        description="True if body was cut at max_chars. Never send a truncated body back "
        "with confluence_update_page mode=replace: the cut-off part would be lost."
    )


class Replacement(BaseModel):
    """One exact piece of a page's storage-format body to swap for another."""

    find: str = Field(
        description="Text to find, copied exactly from the body confluence_get_page returned. "
        "It must occur exactly once; include neighbouring markup to make it unique."
    )
    replace: str = Field(
        description="Storage-format text to put in its place (escape & as &amp; and < as "
        "&lt; in plain text). Empty removes the found text."
    )


class PageWriteResult(WriteReport):
    """Outcome of a Confluence write tool."""

    page_id: str | None = Field(
        default=None, description="The page's content id; None for a page not yet created."
    )
    title: str = Field(description="The page's title.")
    space: str = Field(description="Space key.")
    version: int | None = Field(
        default=None, description="The page's version after the change, when it was sent."
    )
    url: str | None = Field(default=None, description="Browser URL, when the change was sent.")
