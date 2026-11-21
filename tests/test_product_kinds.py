"""Remote and local products, and each product's own dry-run setting."""

import contextlib
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver import MCPServer

from productivity_agent_tools import cli, products, server
from productivity_agent_tools.api import ApiClient
from productivity_agent_tools.config import (
    LocalProductSettings,
    ProductSettings,
    Settings,
    apply_overrides,
    parse_settings,
)
from productivity_agent_tools.errors import ConfigError
from productivity_agent_tools.jira.product import PRODUCT as JIRA
from productivity_agent_tools.jira.settings import JiraSettings
from productivity_agent_tools.products import (
    LocalProduct,
    Product,
    ToolContext,
    installed_products,
    remote_products,
)
from productivity_agent_tools.server import Connected, Opened, _activate, build_server
from tests.conftest import FakeJira


@dataclass(frozen=True, slots=True, kw_only=True)
class NotesSettings(LocalProductSettings):
    folder: Path


@dataclass
class Notes:
    folder: Path
    dry_run: bool
    closed: bool = False
    log: list[str] = field(default_factory=list)


def _parse_notes(raw: dict[str, Any]) -> NotesSettings:
    from productivity_agent_tools.config import dry_run_override

    return NotesSettings(folder=Path(raw["folder"]), dry_run=dry_run_override(raw, "notes"))


@contextlib.asynccontextmanager
async def _open_notes(settings: NotesSettings, dry_run: bool) -> AsyncGenerator[Notes]:
    if not settings.folder.is_dir():
        raise ConfigError(f"Notes folder {settings.folder} does not exist.")
    notes = Notes(settings.folder, dry_run)
    OPENED.append(notes)
    try:
        yield notes
    finally:
        notes.closed = True


def _register_notes(server: MCPServer, notes: Notes, context: ToolContext) -> None:
    @server.tool()
    async def notes_ping() -> str:
        """Say where the notes are."""
        return (
            f"{notes.folder.name} client_dry_run={notes.dry_run} "
            f"context_dry_run={context.dry_run} peers={sorted(context.peers)} "
            f"source={context.token_source!r} cookies={context.cookie_names}"
        )


async def _notes_doctor(notes: Notes) -> AsyncIterator[str]:
    yield f"notes folder {notes.folder.name} is readable"


OPENED: list[Notes] = []

NOTES: LocalProduct[NotesSettings, Notes] = LocalProduct(
    name="notes",
    title="Notes",
    instructions="The notes_ tools.",
    settings_type=NotesSettings,
    parse_settings=_parse_notes,
    open=_open_notes,
    register_tools=_register_notes,
    doctor=_notes_doctor,
)

KNOWN: dict[str, Product[Any, Any]] = {"jira": JIRA, "notes": NOTES}
PARSERS = {name: product.parse_settings for name, product in KNOWN.items()}


def _settings(raw: dict[str, Any]) -> Settings:
    return parse_settings(raw, parsers=PARSERS)


def _text(result: object) -> str:
    return str(getattr(result, "structured_content", result))


# --- per-product dry run ---------------------------------------------------------------


def test_a_product_may_set_its_own_dry_run() -> None:
    settings = parse_settings(
        {"base_url": "https://h.example", "jira": {"dry_run": False}, "bitbucket": {}}
    )
    assert settings.dry_run is True
    assert settings.dry_run_for(settings.products["jira"]) is False
    assert settings.dry_run_for(settings.products["bitbucket"]) is True
    loose = parse_settings({"base_url": "https://h.example", "dry_run": 0, "jira": {"dry_run": 1}})
    assert loose.dry_run_for(loose.products["jira"]) is True


def test_a_bad_product_dry_run_names_its_table() -> None:
    with pytest.raises(ConfigError, match=r"'jira\.dry_run' must be true/false"):
        parse_settings({"base_url": "https://h.example", "jira": {"dry_run": "no"}})


@pytest.mark.parametrize("flag", [True, False])
def test_the_command_line_dry_run_beats_every_table(flag: bool) -> None:
    settings = parse_settings(
        {
            "base_url": "https://h.example",
            "dry_run": not flag,
            "jira": {"dry_run": not flag},
            "bitbucket": {},
        }
    )
    forced = apply_overrides(settings, dry_run=flag)
    assert {forced.dry_run_for(p) for p in forced.products.values()} == {flag}
    assert apply_overrides(settings).products["jira"].dry_run is (not flag)


# --- local products --------------------------------------------------------------------


def test_a_local_product_needs_no_base_url(tmp_path: Path) -> None:
    settings = _settings({"notes": {"folder": str(tmp_path)}})
    assert settings.products["notes"].remote is False
    assert ProductSettings.remote is True
    with pytest.raises(ConfigError, match="No base_url is set"):
        _settings({"notes": {"folder": str(tmp_path)}, "jira": {}})


async def test_a_local_product_is_served_beside_a_remote_one(
    tmp_path: Path, make_core: Callable[..., ApiClient]
) -> None:
    settings = _settings(
        {
            "base_url": "https://h.example",
            "cookies": {"consent": "yes"},
            "notes": {"folder": str(tmp_path), "dry_run": False},
            "jira": {},
        }
    )
    notes = Notes(tmp_path, dry_run=False)
    built = build_server(
        settings,
        {"notes": Opened(notes), "jira": Connected(make_core(), "env")},
        products=KNOWN,
        integrations=[],
    )
    assert built.instructions == f"The notes_ tools. {JIRA.instructions}"
    ping = _text(await built.call_tool("notes_ping", {}))
    assert "context_dry_run=False peers=['jira'] source='' cookies=()" in ping
    status = _text(await built.call_tool("jira_status", {}))
    assert "'dry_run': True" in status


def test_a_product_is_only_activated_by_its_own_kind(
    tmp_path: Path, make_core: Callable[..., ApiClient]
) -> None:
    settings = NotesSettings(folder=tmp_path)
    with pytest.raises(TypeError, match="jira cannot use a Opened"):
        _activate(JIRA, Opened(object()), JiraSettings())
    with pytest.raises(TypeError, match="notes cannot use a Connected"):
        _activate(NOTES, Connected(make_core()), settings)
    with pytest.raises(TypeError, match="notes was given JiraSettings settings"):
        NOTES.activate(Notes(tmp_path, dry_run=True), JiraSettings())
    active = NOTES.activate(Notes(tmp_path, dry_run=True), settings)
    assert active.product is NOTES


def test_remote_products_leave_out_local_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(products, "installed_products", lambda: KNOWN)
    assert dict(remote_products()) == {"jira": JIRA}


def test_a_local_product_loads_from_an_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    point = EntryPoint(
        name="notes", value="tests.test_product_kinds:NOTES", group=products.PRODUCT_GROUP
    )
    monkeypatch.setattr(products, "entry_points", lambda group: [point])
    products.installed_products.cache_clear()
    try:
        assert dict(installed_products()) == {"notes": NOTES}
    finally:
        products.installed_products.cache_clear()


# --- the command line ------------------------------------------------------------------


async def test_the_server_opens_and_closes_a_local_product(
    tmp_path: Path, fake: FakeJira, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JIRA_TOKEN", "good-token")
    monkeypatch.setattr(cli, "installed_products", lambda: KNOWN)
    monkeypatch.setattr(server, "installed_products", lambda: KNOWN)
    monkeypatch.setattr(server, "installed_integrations", lambda: ())
    OPENED.clear()
    settings = _settings(
        {
            "base_url": "https://host.example",
            "cookies": {"consent": "yes"},
            "dry_run": False,
            "notes": {"folder": str(tmp_path)},
            "jira": {"dry_run": True},
        }
    )
    async with cli._open_server(settings, httpx.MockTransport(fake.handler)) as served:
        ping = _text(await served.call_tool("notes_ping", {}))
        assert "client_dry_run=False context_dry_run=False" in ping
        assert "'dry_run': True" in _text(await served.call_tool("jira_status", {}))
        assert [n.closed for n in OPENED] == [False]
    assert [n.closed for n in OPENED] == [True]


async def test_doctor_checks_a_local_product(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "installed_products", lambda: KNOWN)
    settings = _settings({"notes": {"folder": str(tmp_path), "dry_run": False}})
    assert await cli._doctor(Path("/cfg.toml"), settings) is True
    output = capsys.readouterr().out
    assert "[info] notes dry_run=False (its own setting)" in output
    assert f"[ok]   notes folder {tmp_path.name} is readable" in output
    assert "[info] jira is not configured" in output
    assert "extra_cookies" not in output

    missing = _settings({"notes": {"folder": str(tmp_path / "gone")}})
    assert await cli._doctor(Path("/cfg.toml"), missing) is False
    output = capsys.readouterr().out
    assert "[fail] Notes folder" in output
    assert "notes dry_run" not in output
