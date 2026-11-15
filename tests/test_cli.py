import asyncio
import json
import sys
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from mcp.server.mcpserver import MCPServer

from productivity_agent_tools import cli
from productivity_agent_tools.config import Settings
from productivity_agent_tools.errors import AgentToolsError, ConfigError
from productivity_agent_tools.microsoft.oauth import RedeemConfig
from tests.conftest import FakeJira

PEM = b"-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n"


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text('base_url = "https://host.example"\n[cookies]\nconsent = "yes"\n[jira]\n')
    return path


@pytest.fixture(autouse=True)
def token_in_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_TOKEN", "good-token")


class FakeProcess:
    def __init__(self, stdout: bytes, stderr: bytes, returncode: int) -> None:
        self._output = (stdout, stderr)
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._output


def fake_security(monkeypatch: pytest.MonkeyPatch, process: FakeProcess) -> None:
    async def spawn(*args: str, **kwargs: int) -> FakeProcess:
        assert args[:2] == ("/usr/bin/security", "find-certificate")
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)


async def test_resolve_settings_layers_flags_over_file(config_file: Path, tmp_path: Path) -> None:
    env_file = tmp_path / "t.env"
    settings = await cli._resolve_settings(config_file, False, ["route=a1"], env_file)
    assert settings.dry_run is False
    assert settings.cookies == {"consent": "yes", "route": "a1"}
    assert settings.token_env_file == env_file


async def test_doctor_reports_each_check(
    fake: FakeJira,
    make_settings: Callable[..., Settings],
    capsys: pytest.CaptureFixture[str],
) -> None:
    passed = await cli._doctor(
        Path("/cfg.toml"), make_settings(), httpx.MockTransport(fake.handler)
    )
    output = capsys.readouterr().out
    assert passed is True
    assert "config loaded from /cfg.toml" in output
    assert "token found in environment variable JIRA_TOKEN" in output
    assert "authenticated to https://host.example/jira as Pat Example" in output
    assert "Jira version 9.12.0" in output
    assert "7 open issue(s)" in output
    assert "good-token" not in output


async def test_doctor_reports_failure_without_raising(
    fake: FakeJira,
    make_settings: Callable[..., Settings],
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = make_settings(cookies={})
    passed = await cli._doctor(Path("/cfg.toml"), settings, httpx.MockTransport(fake.handler))
    assert passed is False
    assert "[fail] The server returned a web page instead of API data" in capsys.readouterr().out


async def test_serve_runs_the_stdio_server(
    fake: FakeJira, make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    served: list[str] = []

    async def run_stdio(self: MCPServer) -> None:
        served.append(self.name)

    monkeypatch.setattr(MCPServer, "run_stdio_async", run_stdio)
    await cli._serve(make_settings(), httpx.MockTransport(fake.handler))
    assert served == ["productivity-agent-tools"]


async def test_export_ca_writes_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_security(monkeypatch, FakeProcess(PEM * 3, b"", 0))
    target = tmp_path / "roots.pem"
    assert await cli._export_ca(target) == 3
    assert target.read_bytes() == PEM * 3


@pytest.mark.parametrize("process", [FakeProcess(b"", b"denied", 1), FakeProcess(b"", b"", 0)])
async def test_export_ca_fails_when_nothing_exported(
    process: FakeProcess, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_security(monkeypatch, process)
    with pytest.raises(AgentToolsError, match="Could not export"):
        await cli._export_ca(tmp_path / "roots.pem")
    assert not (tmp_path / "roots.pem").exists()


def test_serve_command_passes_resolved_settings(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Settings] = []

    async def record(settings: Settings) -> None:
        seen.append(settings)

    monkeypatch.setattr(cli, "_serve", record)
    cli.serve(config=config_file, dry_run=False, log_level="debug")
    assert (seen[0].dry_run, seen[0].cookies) == (False, {"consent": "yes"})
    assert seen[0].log_level == "debug"


def test_serve_command_exits_cleanly_on_bad_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as info:
        cli.serve(config=tmp_path / "missing.toml")
    assert info.value.code == 1
    assert "error: Config file not found" in capsys.readouterr().err


@pytest.mark.parametrize(("passed", "code"), [(True, 0), (False, 1)])
def test_doctor_command_exit_code_follows_result(
    passed: bool, code: int, config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def result(config: Path, settings: Settings) -> bool:
        return passed

    monkeypatch.setattr(cli, "_doctor", result)
    with pytest.raises(SystemExit) as info:
        cli.doctor(config=config_file)
    assert info.value.code == code


def test_doctor_command_exits_cleanly_on_bad_config(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as info:
        cli.doctor(config=tmp_path / "missing.toml")
    assert info.value.code == 1


def test_set_token_stores_what_was_typed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stored: list[tuple[str, str]] = []

    async def store(product: str, token: str) -> None:
        stored.append((product, token))

    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "  s3cret \n")
    monkeypatch.setattr(cli, "store_token", store)
    cli.set_token()
    assert stored == [("jira", "s3cret")]
    assert "s3cret" not in capsys.readouterr().out


def test_set_token_refuses_empty_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "   ")
    with pytest.raises(SystemExit) as info:
        cli.set_token()
    assert info.value.code == 1


def test_export_ca_command_reports_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_security(monkeypatch, FakeProcess(PEM * 2, b"", 0))
    cli.export_ca(tmp_path / "roots.pem")
    assert "Wrote 2 certificates" in capsys.readouterr().out


MS_CONFIG = (
    'base_url = "https://host.example"\n[jira]\n[microsoft]\n'
    'client_id = "c"\ntenant = "t"\nauthority = "login.microsoftonline.us"\n'
    'origin = "https://o"\nredirect_uri = "https://o/r"\n'
    'scope = "https://outlook.example.us/.default"\n'
)


def test_login_microsoft_seeds_from_a_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(MS_CONFIG)
    seen: dict[str, object] = {}

    async def seed(config: RedeemConfig, token: str, *, verify: bool = True) -> str:
        seen["token"] = token
        seen["client_id"] = config.client_id
        seen["verify"] = verify
        return "Seeded the Microsoft refresh token."

    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "  0.refresh \n")
    monkeypatch.setattr(cli, "seed_microsoft", seed)
    cli.login_microsoft(config=path)
    assert seen == {"token": "0.refresh", "client_id": "c", "verify": True}
    assert "Seeded the Microsoft refresh token." in capsys.readouterr().out


def test_login_microsoft_reads_a_response_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(MS_CONFIG)
    response = tmp_path / "token.json"
    response.write_text('{"refresh_token": "r-from-file", "access_token": "a"}')
    seen: dict[str, object] = {}

    async def seed(config: RedeemConfig, token: str, *, verify: bool = True) -> str:
        seen["token"] = token
        return "ok"

    monkeypatch.setattr(cli, "seed_microsoft", seed)
    cli.login_microsoft(config=path, from_response=response, verify=False)
    assert seen["token"] == "r-from-file"


def test_login_microsoft_without_a_table_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('base_url = "https://host.example"\n[jira]\n')
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "r")
    with pytest.raises(SystemExit) as info:
        cli.login_microsoft(config=path)
    assert info.value.code == 1


def test_login_microsoft_reports_an_unreadable_response_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(MS_CONFIG)
    with pytest.raises(SystemExit) as info:
        cli.login_microsoft(config=path, from_response=tmp_path)  # a directory, not a file
    assert info.value.code == 1


def test_login_microsoft_refuses_an_empty_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(MS_CONFIG)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "   ")
    with pytest.raises(SystemExit) as info:
        cli.login_microsoft(config=path)
    assert info.value.code == 1


def test_export_ca_command_exits_cleanly_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_security(monkeypatch, FakeProcess(b"", b"denied", 1))
    with pytest.raises(SystemExit) as info:
        cli.export_ca(tmp_path / "roots.pem")
    assert info.value.code == 1


def test_main_dispatches_to_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "sys.argv", ["productivity-agent-tools", "doctor", "--config", str(tmp_path / "no.toml")]
    )
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 1
    assert ConfigError.__name__ not in capsys.readouterr().err


async def test_call_lists_tools_with_their_parameters(
    fake: FakeJira, make_settings: Callable[..., Settings]
) -> None:
    listing = await cli._call(make_settings(), None, (), httpx.MockTransport(fake.handler))
    assert "jira_search jql=... [max_results=...]\n    Search Jira with an ad-hoc JQL" in listing
    assert "jira_status\n    Check the Jira connection" in listing
    assert "bitbucket_" not in listing


async def test_call_runs_a_tool_and_types_its_arguments(
    fake: FakeJira, make_settings: Callable[..., Settings]
) -> None:
    transport = httpx.MockTransport(fake.handler)
    output = await cli._call(
        make_settings(), "jira_search", ("jql=project = ABC", "max_results=5"), transport
    )
    assert json.loads(output)["issues"][0]["key"] == "ABC-1"
    sent = dict(fake.requests[-1].url.params)
    assert (sent["jql"], sent["maxResults"]) == ("project = ABC", "5")

    comment = await cli._call(
        make_settings(), "jira_add_comment", ("key=ABC-1", "body=123"), transport
    )
    assert json.loads(comment)["request"]["body"] == {"body": "123"}
    assert json.loads(comment)["dry_run"] is True
    assert fake.writes() == []


@pytest.mark.parametrize(
    ("tool", "pairs", "message"),
    [
        ("nope", (), "'nope' is not a tool"),
        ("jira_search", ("jql",), "must be written as name=value"),
        ("jira_search", ("jqll=x",), "Parameters: jql, max_results"),
        ("jira_search", ("jql=x", "max_results=lots"), "must be a number, true/false or JSON"),
        ("jira_search", ("jql=BROKEN",), "Bad JQL"),
        ("jira_search", (), "Field required"),
    ],
)
async def test_call_explains_what_went_wrong(
    fake: FakeJira,
    make_settings: Callable[..., Settings],
    tool: str,
    pairs: tuple[str, ...],
    message: str,
) -> None:
    with pytest.raises(AgentToolsError, match=message):
        await cli._call(make_settings(), tool, pairs, httpx.MockTransport(fake.handler))


def test_call_command_prints_the_result_or_exits_with_the_error(
    config_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[tuple[str | None, tuple[str, ...]]] = []

    async def run(
        settings: Settings, tool: str | None, pairs: tuple[str, ...], field: str | None = None
    ) -> str:
        seen.append((tool, pairs))
        if tool == "bad":
            raise AgentToolsError("no such luck")
        return "RESULT"

    monkeypatch.setattr(cli, "_call", run)
    cli.call("jira_search", "jql=x", config=config_file)
    assert capsys.readouterr().out == "RESULT\n"
    assert seen == [("jira_search", ("jql=x",))]
    with pytest.raises(SystemExit) as info:
        cli.call("bad", config=config_file)
    assert info.value.code == 1
    assert "error: no such luck" in capsys.readouterr().err


def test_text_parameters_are_detected_from_the_schema() -> None:
    assert cli._accepts_text({"type": "string"})
    assert cli._accepts_text({"anyOf": [{"type": "string"}, {"type": "null"}]})
    assert not cli._accepts_text({"anyOf": [{"type": "integer"}, {"type": "null"}]})
    assert cli._accepts_text("not a schema")
    # A named choice of words, as pydantic describes a `type X = Literal[...]` alias.
    definitions = {
        "BranchType": {"enum": ["feature", "bugfix"], "type": "string"},
        "Replacement": {"type": "object"},
    }
    assert cli._accepts_text({"$ref": "#/$defs/BranchType", "default": "feature"}, definitions)
    assert not cli._accepts_text({"$ref": "#/$defs/Replacement"}, definitions)
    assert not cli._accepts_text({"$ref": "#/$defs/Missing"}, definitions)
    assert not cli._accepts_text({"$ref": "#/$defs/BranchType"})
    schema = {
        "properties": {"branch_type": {"$ref": "#/$defs/BranchType"}},
        "$defs": definitions,
    }
    assert cli._tool_arguments(schema, ("branch_type=feature",)) == {"branch_type": "feature"}


async def test_call_can_return_one_field_with_text_as_written(
    fake: FakeJira, make_settings: Callable[..., Settings]
) -> None:
    transport = httpx.MockTransport(fake.handler)
    settings = make_settings()
    assert await cli._call(settings, "jira_status", (), transport, field="display_name") == (
        "Pat Example"
    )
    assert json.loads(await cli._call(settings, "jira_status", (), transport, field="dry_run"))
    with pytest.raises(AgentToolsError, match=r"no field 'nope'\. Fields: base_url, jira_version"):
        await cli._call(settings, "jira_status", (), transport, field="nope")


def test_diff_field_is_coloured_only_in_a_terminal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    diff = "--- a\n+++ b\n-old\n+new"
    cli._show(diff, "diff")
    cli._show("plain", "title")
    cli._show("{}", None)
    assert capsys.readouterr().out == f"{diff}\nplain\n{{}}\n"

    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setenv("FORCE_COLOR", "1")
    cli._show(diff, "diff")
    coloured = capsys.readouterr().out
    assert "\x1b[" in coloured
    assert "+new" in coloured
    cli._show("plain", "title")
    assert capsys.readouterr().out == "plain\n"


async def test_no_configured_product_is_reported_not_served(
    make_settings: Callable[..., Settings], capsys: pytest.CaptureFixture[str]
) -> None:
    empty = make_settings(products={})
    with pytest.raises(
        ConfigError,
        match=r"Add a \[bitbucket\], \[confluence\], \[jira\], \[outlook_calendar\], "
        r"\[outlook_mail\] or \[servicenow\] table",
    ):
        await cli._serve(empty)
    with pytest.raises(ConfigError, match="No product is configured"):
        await cli._call(empty, None, ())
    assert await cli._doctor(Path("/cfg.toml"), empty) is False
    assert "[fail] No product is configured" in capsys.readouterr().out
