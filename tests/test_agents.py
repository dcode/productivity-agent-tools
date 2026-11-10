import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from productivity_agent_tools import agents, cli
from productivity_agent_tools.agents import Launch, configure_agent, find_launch, goose_config_path
from productivity_agent_tools.errors import ConfigError

LAUNCH = Launch(command="/opt/venv/bin/productivity-agent-tools", args=("serve",))
EXISTING = """\
# my goose config
GOOSE_MODEL: some-model
extensions:
  developer:
    bundled: true  # keep me
    enabled: true
"""


def load(path: Path) -> dict:
    return YAML(typ="safe").load(path.read_text())


def test_adds_the_extension_and_keeps_everything_else(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    target.write_text(EXISTING)
    change = configure_agent("Goose", LAUNCH, path=target)
    assert (change.written, change.replaced) == (True, False)
    assert change.backup == tmp_path / "config.yaml.bak"
    assert change.backup.read_text() == EXISTING
    text = target.read_text()
    assert text.startswith(EXISTING)
    assert "# keep me" in text
    assert load(target)["extensions"]["productivity"] == {
        "name": "productivity",
        "type": "stdio",
        "cmd": "/opt/venv/bin/productivity-agent-tools",
        "args": ["serve"],
        "enabled": True,
        "timeout": 300,
    }
    assert "cmd: /opt/venv/bin/productivity-agent-tools\n" in change.entry


def test_creates_the_file_when_goose_has_none(tmp_path: Path) -> None:
    target = tmp_path / "goose" / "config.yaml"
    change = configure_agent("goose", LAUNCH, path=target)
    assert change.backup is None
    assert list(load(target)["extensions"]) == ["productivity"]


def test_update_replaces_the_launch_and_keeps_the_users_choices(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    target.write_text(
        "extensions:\n  jira:\n    name: jira\n    type: stdio\n    cmd: uv\n"
        "    args: [run, serve]\n    enabled: false\n    timeout: 60\n"
    )
    change = configure_agent("goose", LAUNCH, path=target, name="jira")
    assert change.replaced is True
    entry = load(target)["extensions"]["jira"]
    assert (entry["cmd"], entry["args"]) == (LAUNCH.command, ["serve"])
    assert (entry["enabled"], entry["timeout"]) == (False, 60)


def test_show_changes_nothing(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    target.write_text(EXISTING)
    change = configure_agent("goose", LAUNCH, path=target, write=False)
    assert (change.written, change.backup) == (False, None)
    assert target.read_text() == EXISTING
    assert not (tmp_path / "config.yaml.bak").exists()


@pytest.mark.parametrize(
    "content", ["extensions: [a, b]\n", "- just\n- a list\n", "a: [unclosed\n"]
)
def test_unexpected_goose_config_is_left_alone(tmp_path: Path, content: str) -> None:
    target = tmp_path / "config.yaml"
    target.write_text(content)
    with pytest.raises(ConfigError, match=r"layout of a Goose config|not valid YAML"):
        configure_agent("goose", LAUNCH, path=target)
    assert target.read_text() == content


def test_unknown_agent_lists_the_supported_ones() -> None:
    with pytest.raises(ConfigError, match="Supported: goose"):
        configure_agent("emacs", LAUNCH)


def test_launch_prefers_the_command_beside_the_running_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "bin" / "productivity-agent-tools"
    script.parent.mkdir()
    script.touch()
    monkeypatch.setattr(sys, "executable", str(tmp_path / "bin" / "python"))
    assert find_launch() == Launch(command=str(script), args=("serve",))
    assert find_launch(Path("/etc/tools.toml")).args == ("serve", "--config", "/etc/tools.toml")


def test_launch_falls_back_to_path_then_explains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(agents.shutil, "which", lambda name: "/usr/local/bin/" + name)
    assert find_launch().command == "/usr/local/bin/productivity-agent-tools"
    monkeypatch.setattr(agents.shutil, "which", lambda name: None)
    with pytest.raises(ConfigError, match="uv sync"):
        find_launch()


def test_goose_config_location_per_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/xdg")
    assert goose_config_path() == Path("/xdg/goose/config.yaml")
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert goose_config_path() == Path.home() / ".config/goose/config.yaml"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", "/roaming")
    assert goose_config_path() == Path("/roaming/Block/goose/config/config.yaml")
    monkeypatch.delenv("APPDATA")
    assert goose_config_path().parts[-4:] == ("Block", "goose", "config", "config.yaml")


def test_command_writes_shows_and_reports_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "find_launch", lambda config: LAUNCH)
    target = tmp_path / "config.yaml"

    cli.configure_agent_command(agent_config=target, show=True)
    assert "Would write this to" in capsys.readouterr().out
    assert not target.exists()

    cli.configure_agent_command(agent_config=target)
    first = capsys.readouterr().out
    assert "Added the 'productivity' extension" in first
    assert "saved as" not in first

    cli.configure_agent_command(agent_config=target)
    second = capsys.readouterr().out
    assert "Updated the 'productivity' extension" in second
    assert f"saved as {target}.bak" in second

    with pytest.raises(SystemExit) as info:
        cli.configure_agent_command(agent="emacs", agent_config=target)
    assert info.value.code == 1
    assert "not a supported agent" in capsys.readouterr().err
