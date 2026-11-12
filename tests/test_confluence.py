"""Confluence: client, tools, settings and doctor, against a fake server."""

import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from productivity_agent_tools import cli
from productivity_agent_tools.config import Settings, parse_settings
from productivity_agent_tools.confluence.client import (
    MAX_BODY_CHARS,
    ConfluenceClient,
    page_id_of,
    space_key_of,
    to_storage,
)
from productivity_agent_tools.confluence.settings import ConfluenceSettings
from productivity_agent_tools.errors import (
    AgentToolsError,
    ConfigError,
    NotFoundError,
    PermissionDeniedError,
)
from productivity_agent_tools.http import AtlassianClient
from productivity_agent_tools.server import Connected, build_server

API = "/confluence/rest/api"
TOKEN = "cf-token"


def _content(page_id: str, title: str, body: str, version: int = 3) -> dict[str, Any]:
    return {
        "id": page_id,
        "type": "page",
        "title": title,
        "space": {"key": "~pat"},
        "version": {
            "number": version,
            "when": "2026-11-10T09:00:00.000Z",
            "by": {"displayName": "Pat Example"},
        },
        "ancestors": [{"id": "1", "title": "Pat Example Home"}, {"title": "no id"}],
        "metadata": {"labels": {"results": [{"name": "profile"}, {"prefix": "global"}]}},
        "body": {"storage": {"value": body, "representation": "storage"}},
        "operations": [
            {"operation": "read", "targetType": "page"},
            {"operation": "update", "targetType": "page"},
        ],
        "_links": {"webui": f"/pages/viewpage.action?pageId={page_id}"},
    }


class FakeConfluence:
    """Answers the Confluence endpoints the client uses, keeping pages in memory."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.pages: dict[str, dict[str, Any]] = {
            "100": _content("100", "About me", "<p>Hello</p>"),
        }
        self.personal_space: httpx.Response = httpx.Response(
            200, json={"key": "~pat", "name": "Pat Example", "type": "personal"}
        )
        self.server_info: httpx.Response = httpx.Response(200, json={"version": "10.2.20"})
        self.next_link = False
        self.profile: dict[str, Any] = {"username": "pat", "displayName": "Pat Example"}

    def writes(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method in ("POST", "PUT")]

    def _list(self, results: list[dict[str, Any]]) -> httpx.Response:
        links: dict[str, str] = {"next": "/rest/api/more"} if self.next_link else {}
        return httpx.Response(200, json={"results": results, "_links": links})

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"statusCode": 401, "message": "Unauthorized"})
        path, params = request.url.path, request.url.params
        if path == f"{API}/user/current":
            return httpx.Response(200, json=self.profile)
        if path == f"{API}/server-information":
            return self.server_info
        if path == f"{API}/space/~pat":
            return self.personal_space
        if path == f"{API}/space/OPS":
            return httpx.Response(
                200,
                json={"key": "OPS", "name": "Operations", "type": "global", "_links": {}},
            )
        if path.startswith(f"{API}/space/"):
            return httpx.Response(404, json={"statusCode": 404, "message": "No space"})
        if path == f"{API}/space":
            return self._list(
                [
                    {
                        "key": "OPS",
                        "name": "Operations",
                        "type": "global",
                        "_links": {"webui": "/display/OPS"},
                    },
                    {"key": "~pat", "type": "personal"},
                ]
            )
        if path == f"{API}/content/search":
            return self._list(list(self.pages.values()))
        if path == f"{API}/content" and request.method == "GET":
            found = [
                p
                for p in self.pages.values()
                if p["title"] == params["title"] and p["space"]["key"] == params["spaceKey"]
            ]
            return self._list(found)
        if path == f"{API}/content" and request.method == "POST":
            body = json.loads(request.content)
            created = _content("200", body["title"], body["body"]["storage"]["value"], 1)
            self.pages["200"] = created
            return httpx.Response(200, json=created)
        if path == f"{API}/content/100/child/page":
            return self._list([self.pages["100"]])
        if path.startswith(f"{API}/content/"):
            page_id = path.removeprefix(f"{API}/content/")
            page = self.pages.get(page_id)
            if page is None:
                return httpx.Response(404, json={"statusCode": 404, "message": "No content"})
            if request.method == "PUT":
                body = json.loads(request.content)
                if body["version"]["number"] != page["version"]["number"] + 1:
                    return httpx.Response(409, json={"statusCode": 409, "message": "Conflict"})
                page.update(
                    title=body["title"],
                    version={**page["version"], "number": body["version"]["number"]},
                    body={"storage": body["body"]["storage"]},
                )
            return httpx.Response(200, json=page)
        return httpx.Response(404, json={"statusCode": 404, "message": f"Unexpected {path}"})


@pytest.fixture
def confluence() -> FakeConfluence:
    return FakeConfluence()


@pytest.fixture
async def make_cf_api(
    confluence: FakeConfluence,
) -> AsyncIterator[Callable[..., AtlassianClient]]:
    created: list[AtlassianClient] = []

    def build(token: str = TOKEN, dry_run: bool = True) -> AtlassianClient:
        api = AtlassianClient(
            base_url="https://host.example",
            token=token,
            dry_run=dry_run,
            transport=httpx.MockTransport(confluence.handler),
        )
        created.append(api)
        return api

    yield build
    for api in created:
        await api.aclose()


@pytest.fixture
def client(make_cf_api: Callable[..., AtlassianClient]) -> ConfluenceClient:
    return ConfluenceClient(make_cf_api(), ConfluenceSettings(default_space="~pat"))


def _settings(**confluence: Any) -> Settings:
    return parse_settings({"base_url": "https://host.example", "confluence": confluence})


def _structured(result: Any) -> dict[str, Any]:
    assert not result.is_error, result.content
    return dict(result.structured_content)


# ---- settings and validation ---------------------------------------------------------


def test_confluence_settings_have_defaults_and_overrides() -> None:
    plain = _settings().product("confluence", ConfluenceSettings)
    assert plain == ConfluenceSettings()
    custom = _settings(path="", default_space=" ~pat ", default_max_results=5)
    assert custom.product("confluence", ConfluenceSettings) == ConfluenceSettings(
        path="", default_space="~pat", default_max_results=5
    )
    with pytest.raises(ConfigError, match=r"confluence\.default_max_results"):
        _settings(default_max_results=0)


def test_ids_keys_and_bodies_are_validated() -> None:
    assert (page_id_of(" 42 "), page_id_of(42)) == ("42", "42")
    assert space_key_of(" ~jane.doe@corp ") == "~jane.doe@corp"
    with pytest.raises(AgentToolsError, match="not a page id"):
        page_id_of("abc")
    with pytest.raises(AgentToolsError, match="not a space key"):
        space_key_of("two words")
    with pytest.raises(AgentToolsError, match="must not be empty"):
        to_storage("  ", "markdown")
    with pytest.raises(AgentToolsError, match="body_format must be"):
        to_storage("x", cast(Any, "wiki"))
    assert to_storage(" <p>x</p> ", "storage") == "<p>x</p>"
    assert to_storage("**x**", "markdown") == "<p><strong>x</strong></p>"


# ---- reads ---------------------------------------------------------------------------


async def test_reads_build_compact_results(
    confluence: FakeConfluence, client: ConfluenceClient
) -> None:
    assert client.base_url == "https://host.example/confluence"
    assert client.settings.path == "/confluence"
    assert await client.server_version() == "10.2.20"
    assert await client.personal_space("pat") == "~pat"

    spaces = await client.list_spaces("global", 500)
    assert dict(confluence.requests[-1].url.params) == {"limit": "100", "type": "global"}
    assert [s.key for s in spaces.spaces] == ["OPS", "~pat"]
    assert spaces.spaces[0].url == "https://host.example/confluence/display/OPS"
    assert (spaces.returned, spaces.more_available) == (2, False)
    await client.list_spaces()
    assert "type" not in confluence.requests[-1].url.params

    confluence.next_link = True
    found = await client.search('title ~ "about"', 2)
    assert confluence.requests[-1].url.params["cql"] == 'title ~ "about"'
    (row,) = found.pages
    assert (row.id, row.space, row.version, row.last_modified_by) == (
        "100",
        "~pat",
        3,
        "Pat Example",
    )
    assert row.url == "https://host.example/confluence/pages/viewpage.action?pageId=100"
    assert found.more_available is True
    with pytest.raises(AgentToolsError, match="CQL query must not be empty"):
        await client.search(" ")

    children = await client.child_pages("100")
    assert children.pages[0].title == "About me"


async def test_missing_server_information_and_personal_space_read_as_none(
    confluence: FakeConfluence, client: ConfluenceClient
) -> None:
    confluence.server_info = httpx.Response(404, json={"statusCode": 404, "message": "nope"})
    confluence.personal_space = httpx.Response(404, json={"statusCode": 404, "message": "no"})
    assert await client.server_version() is None
    assert await client.personal_space("pat") is None


async def test_get_page_by_id_or_title_with_truncation(
    confluence: FakeConfluence, client: ConfluenceClient
) -> None:
    page = await client.get_page("100")
    assert confluence.requests[-1].url.params["expand"].startswith("body.storage")
    assert (page.title, page.body, page.truncated, page.body_chars) == (
        "About me",
        "<p>Hello</p>",
        False,
        12,
    )
    assert [a.id for a in page.ancestors] == ["1", ""]
    assert page.labels == ["profile"]

    cut = await client.get_page("100", 4)
    assert (cut.body, cut.truncated) == ("<p>H", True)

    by_title = await client.find_page(" About me ")
    assert by_title.id == "100"
    with pytest.raises(NotFoundError, match="no page titled 'Missing' in space ~pat"):
        await client.find_page("Missing")


async def test_space_falls_back_to_the_default_and_can_be_changed(
    make_cf_api: Callable[..., AtlassianClient],
) -> None:
    bare = ConfluenceClient(make_cf_api(), ConfluenceSettings())
    with pytest.raises(AgentToolsError, match="No space was given"):
        bare.space(None)
    assert bare.space(" OPS ") == "OPS"
    chosen = await bare.set_default_space("OPS")
    assert (chosen.name, bare.default_space, bare.space(None)) == ("Operations", "OPS", "OPS")
    with pytest.raises(NotFoundError):
        await bare.set_default_space("NOPE")
    assert bare.default_space == "OPS"


# ---- writes --------------------------------------------------------------------------


async def test_create_checks_the_title_and_sends_storage_format(
    confluence: FakeConfluence, make_cf_api: Callable[..., AtlassianClient]
) -> None:
    live = ConfluenceClient(make_cf_api(dry_run=False), ConfluenceSettings(default_space="~pat"))
    with pytest.raises(AgentToolsError, match=r"already has a page titled 'About me' \(id 100\)"):
        await live.create_page("About me", "# Hi")
    with pytest.raises(AgentToolsError, match="title must not be empty"):
        await live.create_page(" ", "# Hi")
    assert confluence.writes() == []

    space, outcome = await live.create_page(" Team notes ", "# Hi", parent_id="100")
    (sent,) = confluence.writes()
    assert space == "~pat" and outcome.sent
    assert json.loads(sent.content) == {
        "type": "page",
        "title": "Team notes",
        "space": {"key": "~pat"},
        "body": {"storage": {"value": "<h1>Hi</h1>", "representation": "storage"}},
        "ancestors": [{"id": "100"}],
    }


async def test_plan_update_checks_the_version_and_combines_bodies(
    client: ConfluenceClient,
) -> None:
    with pytest.raises(AgentToolsError, match="Give a new body, a new title"):
        await client.plan_update("100", 3, title=" ")
    with pytest.raises(AgentToolsError, match="mode must be"):
        await client.plan_update("100", 3, "x", mode=cast(Any, "merge"))
    with pytest.raises(AgentToolsError, match="now at version 3, not 2: someone changed it"):
        await client.plan_update("100", 2, "x")

    replaced = await client.plan_update("100", 3, "New")
    assert (replaced.body, replaced.title, replaced.version) == ("<p>New</p>", "About me", 4)
    appended = await client.plan_update("100", 3, "More", mode="append")
    assert appended.body == "<p>Hello</p>\n<p>More</p>"
    prepended = await client.plan_update(
        "100", 3, "<p>Top</p>", body_format="storage", mode="prepend"
    )
    assert prepended.body == "<p>Top</p>\n<p>Hello</p>"
    renamed = await client.plan_update("100", 3, title=" About Pat ")
    assert (renamed.body, renamed.title) == ("<p>Hello</p>", "About Pat")


async def test_a_page_the_user_may_not_edit_says_so_and_refuses_the_edit(
    confluence: FakeConfluence, make_cf_api: Callable[..., AtlassianClient]
) -> None:
    live = ConfluenceClient(make_cf_api(dry_run=False), ConfluenceSettings())
    assert (await live.get_page("100")).can_edit is True
    confluence.pages["100"]["operations"] = [{"operation": "read", "targetType": "page"}]
    assert (await live.get_page("100")).can_edit is False
    with pytest.raises(PermissionDeniedError, match="do not have permission to edit page 100"):
        await live.plan_update("100", 3, "x")
    assert confluence.writes() == []
    del confluence.pages["100"]["operations"]
    assert (await live.get_page("100")).can_edit is None
    assert (await live.plan_update("100", 3, "x")).version == 4


async def test_plan_update_refuses_pages_too_long_to_edit_whole(
    confluence: FakeConfluence, client: ConfluenceClient
) -> None:
    confluence.pages["100"]["body"]["storage"]["value"] = "x" * (MAX_BODY_CHARS + 1)
    with pytest.raises(AgentToolsError, match="edit it in the browser"):
        await client.plan_update("100", 3, "more", mode="append")


async def test_update_sends_the_next_version_with_comment_and_parent(
    confluence: FakeConfluence, make_cf_api: Callable[..., AtlassianClient]
) -> None:
    live = ConfluenceClient(make_cf_api(dry_run=False), ConfluenceSettings())
    update = await live.plan_update("100", 3, "Hi again")
    outcome = await live.update_page(update, "1", " Refreshed ", minor_edit=True)
    (sent,) = confluence.writes()
    assert outcome.sent and sent.method == "PUT"
    assert json.loads(sent.content) == {
        "id": "100",
        "type": "page",
        "title": "About me",
        "space": {"key": "~pat"},
        "version": {"number": 4, "minorEdit": True, "message": "Refreshed"},
        "body": {"storage": {"value": "<p>Hi again</p>", "representation": "storage"}},
        "ancestors": [{"id": "1"}],
    }
    plain = await live.plan_update("100", 4, title="About Pat")
    await live.update_page(plain, version_comment=" ")
    body = json.loads(confluence.writes()[-1].content)
    assert "ancestors" not in body and body["version"] == {"number": 5, "minorEdit": False}


# ---- tools ---------------------------------------------------------------------------


async def test_tools_are_registered_with_read_and_write_annotations(
    make_cf_api: Callable[..., AtlassianClient],
) -> None:
    server = build_server(_settings(), {"confluence": Connected(make_cf_api(), "cf source")})
    tools = {t.name: t for t in await server.list_tools()}
    assert sorted(tools) == [
        "confluence_create_page",
        "confluence_get_page",
        "confluence_list_child_pages",
        "confluence_list_spaces",
        "confluence_search",
        "confluence_set_default_space",
        "confluence_status",
        "confluence_update_page",
    ]
    writes = sorted(
        name
        for name, tool in tools.items()
        if tool.annotations is None or not tool.annotations.read_only_hint
    )
    assert writes == [
        "confluence_create_page",
        "confluence_set_default_space",
        "confluence_update_page",
    ]
    assert (server.instructions or "").startswith("The confluence_ tools")

    status = _structured(await server.call_tool("confluence_status", {}))
    assert status == {
        "base_url": "https://host.example/confluence",
        "confluence_version": "10.2.20",
        "token_source": "cf source",
        "username": "pat",
        "display_name": "Pat Example",
        "personal_space": "~pat",
        "default_space": None,
        "dry_run": True,
        "extra_cookies": [],
    }


async def test_read_tools(make_cf_api: Callable[..., AtlassianClient]) -> None:
    server = build_server(_settings(default_space="~pat"), {"confluence": Connected(make_cf_api())})
    by_id = _structured(await server.call_tool("confluence_get_page", {"page_id": "100"}))
    by_title = _structured(await server.call_tool("confluence_get_page", {"title": "About me"}))
    assert by_id == by_title and by_id["version"] == 3
    with pytest.raises(ToolError, match="Give a page_id, or a title"):
        await server.call_tool("confluence_get_page", {})
    found = _structured(await server.call_tool("confluence_search", {"cql": "type = page"}))
    assert found["returned"] == 1
    spaces = _structured(await server.call_tool("confluence_list_spaces", {}))
    assert spaces["returned"] == 2
    children = _structured(
        await server.call_tool("confluence_list_child_pages", {"page_id": "100"})
    )
    assert children["pages"][0]["id"] == "100"
    chosen = _structured(await server.call_tool("confluence_set_default_space", {"space": "OPS"}))
    assert chosen["message"] == "Space OPS is now the default until the server restarts."


async def test_status_without_a_username_skips_the_personal_space(
    confluence: FakeConfluence, make_cf_api: Callable[..., AtlassianClient]
) -> None:
    confluence.profile = {"displayName": "Someone"}
    server = build_server(_settings(), {"confluence": Connected(make_cf_api())})
    status = _structured(await server.call_tool("confluence_status", {}))
    assert (status["username"], status["personal_space"]) == (None, None)


async def test_create_tool_dry_run_then_live_without_repeats(
    confluence: FakeConfluence, make_cf_api: Callable[..., AtlassianClient]
) -> None:
    page = {"title": "Pat's projects", "body": "## Now\n\n- uploads", "space": "~pat"}
    dry = build_server(_settings(), {"confluence": Connected(make_cf_api())})
    blocked = _structured(await dry.call_tool("confluence_create_page", page))
    assert (blocked["sent"], blocked["page_id"], blocked["url"]) == (False, None, None)
    assert blocked["message"].startswith("DRY RUN: nothing was sent to Confluence")
    assert blocked["request"]["body"]["body"]["storage"]["value"] == (
        "<h2>Now</h2>\n<ul>\n<li>uploads</li>\n</ul>"
    )
    assert confluence.writes() == []

    live = build_server(
        _settings(dry_run=False), {"confluence": Connected(make_cf_api(dry_run=False))}
    )
    first = _structured(await live.call_tool("confluence_create_page", page))
    again = _structured(await live.call_tool("confluence_create_page", page))
    assert (first["sent"], first["page_id"], first["version"]) == (True, "200", 1)
    assert first["url"] == "https://host.example/confluence/pages/viewpage.action?pageId=200"
    assert first["message"] == "Created page 'Pat's projects' in space ~pat."
    assert again["repeat_suppressed"] is True
    assert len(confluence.writes()) == 1


async def test_update_tool_saves_a_version_and_reports_conflicts(
    confluence: FakeConfluence, make_cf_api: Callable[..., AtlassianClient]
) -> None:
    live = build_server(_settings(), {"confluence": Connected(make_cf_api(dry_run=False))})
    edit = {"page_id": "100", "version": 3, "body": "Thanks for reading.", "mode": "append"}
    saved = _structured(await live.call_tool("confluence_update_page", edit))
    assert (saved["sent"], saved["version"], saved["page_id"]) == (True, 4, "100")
    assert saved["message"] == "Saved version 4 of 'About me' in space ~pat."
    assert confluence.pages["100"]["body"]["storage"]["value"] == (
        "<p>Hello</p>\n<p>Thanks for reading.</p>"
    )
    again = _structured(await live.call_tool("confluence_update_page", edit))
    assert again["repeat_suppressed"] is True

    stale = {**edit, "body": "Other text"}
    with pytest.raises(ToolError, match="now at version 4, not 3"):
        await live.call_tool("confluence_update_page", stale)
    assert len(confluence.writes()) == 1


# ---- product wiring ------------------------------------------------------------------


async def test_doctor_checks_confluence(
    confluence: FakeConfluence,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CONFLUENCE_TOKEN", TOKEN)
    transport = httpx.MockTransport(confluence.handler)
    assert await cli._doctor(Path("/cfg.toml"), _settings(), transport) is True
    output = capsys.readouterr().out
    assert "[ok]   confluence token found in environment variable CONFLUENCE_TOKEN" in output
    assert "[ok]   authenticated to https://host.example/confluence as Pat Example" in output
    assert "[ok]   Confluence version 10.2.20" in output
    assert "[ok]   search works (1 page(s) you have edited)" in output

    confluence.next_link = True
    assert await cli._doctor(Path("/cfg.toml"), _settings(), transport) is True
    assert "(1+ page(s)" in capsys.readouterr().out
