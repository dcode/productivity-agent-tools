"""Register this MCP server with an agent host, such as Goose.

The agent launches the server by absolute path to the command installed in
this environment, so it does not depend on the agent's working directory, on
its ``PATH``, or on argument order for a wrapper such as ``uv run``.
"""

import io
import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError

from productivity_agent_tools.errors import ConfigError

SCRIPT_NAME: str = "productivity-agent-tools"
DEFAULT_EXTENSION_NAME: str = "productivity"
_START_TIMEOUT_SECONDS: int = 300


@dataclass(frozen=True, slots=True)
class Launch:
    """How an agent should start the server.

    Attributes:
        command: Absolute path of the program to run.
        args: Arguments to pass to it.
    """

    command: str
    args: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AgentChange:
    """What configuring an agent did, or would do.

    Attributes:
        path: The agent's configuration file.
        entry: The extension entry, rendered as the agent's config format.
        written: False when only a preview was asked for.
        replaced: True when an entry of the same name was already there.
        backup: Copy of the previous file, or None when there was none or nothing was written.
    """

    path: Path
    entry: str
    written: bool
    replaced: bool
    backup: Path | None


def find_launch(config: Path | None = None) -> Launch:
    """Work out the command that starts the server from this installation.

    The command installed next to the running Python (the environment's
    ``bin`` or ``Scripts`` directory) is preferred, then one on ``PATH``.

    Args:
        config: Tool configuration file to pass with ``--config``, or None for the default.

    Returns:
        The launch command.

    Raises:
        ConfigError: If the installed command cannot be found.
    """
    suffix = ".exe" if sys.platform == "win32" else ""
    beside_python = Path(sys.executable).absolute().parent / f"{SCRIPT_NAME}{suffix}"
    on_path = shutil.which(SCRIPT_NAME)
    if beside_python.is_file():
        command = beside_python
    elif on_path:
        command = Path(on_path).absolute()
    else:
        raise ConfigError(
            f"Could not find the installed '{SCRIPT_NAME}' command next to {sys.executable} "
            "or on PATH. Install the project first (for example with 'uv sync')."
        )
    args = ("serve",) if config is None else ("serve", "--config", str(config.expanduser()))
    return Launch(command=str(command), args=args)


def goose_config_path() -> Path:
    """Locate Goose's configuration file for this operating system.

    Returns:
        The path Goose reads, whether or not it exists yet.
    """
    if sys.platform == "win32":
        roaming = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(roaming) / "Block" / "goose" / "config" / "config.yaml"
    config_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(config_home) / "goose" / "config.yaml"


def _configure_goose(launch: Launch, path: Path | None, name: str, write: bool) -> AgentChange:
    """Add or update this server's stdio extension in Goose's config.

    Everything else in the file, comments included, is left as it was.

    Args:
        launch: How Goose should start the server.
        path: Goose's configuration file, or None for its default location.
        name: Key and display name of the extension.
        write: False to only report what would be written.

    Returns:
        A description of the change.

    Raises:
        ConfigError: If the existing file is not YAML Goose could have written.
    """
    target = (path or goose_config_path()).expanduser()
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 4096  # never fold a long path onto its own line
    try:
        document = yaml.load(target.read_text(encoding="utf-8")) if target.is_file() else None
    except (YAMLError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{target} is not valid YAML; fix or move it and try again.") from exc
    if document is None:
        document = CommentedMap()
    extensions = (
        document.setdefault("extensions", CommentedMap()) if isinstance(document, dict) else None
    )
    if not isinstance(extensions, dict):
        raise ConfigError(f"{target} does not have the layout of a Goose config; left unchanged.")
    existing = extensions.get(name)
    entry = existing if isinstance(existing, dict) else CommentedMap()
    entry.update({"name": name, "type": "stdio", "cmd": launch.command, "args": list(launch.args)})
    entry.setdefault("enabled", True)
    entry.setdefault("timeout", _START_TIMEOUT_SECONDS)
    extensions[name] = entry

    rendered = io.StringIO()
    yaml.dump({"extensions": {name: entry}}, rendered)
    backup: Path | None = None
    if write:
        whole = io.StringIO()
        yaml.dump(document, whole)
        if target.is_file():
            backup = target.with_name(f"{target.name}.bak")
            shutil.copy2(target, backup)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(whole.getvalue(), encoding="utf-8")
    return AgentChange(
        path=target,
        entry=rendered.getvalue(),
        written=write,
        replaced=existing is not None,
        backup=backup,
    )


_AGENTS: dict[str, Callable[[Launch, Path | None, str, bool], AgentChange]] = {
    "goose": _configure_goose,
}
SUPPORTED_AGENTS: tuple[str, ...] = tuple(_AGENTS)


def configure_agent(
    agent: str,
    launch: Launch,
    *,
    path: Path | None = None,
    name: str = DEFAULT_EXTENSION_NAME,
    write: bool = True,
) -> AgentChange:
    """Register the server with an agent.

    Args:
        agent: Which agent to configure; one of ``SUPPORTED_AGENTS``.
        launch: How the agent should start the server.
        path: The agent's configuration file, or None for its default location.
        name: Name to register the server under.
        write: False to only report what would be written.

    Returns:
        A description of the change.

    Raises:
        ConfigError: If the agent is not supported or its config cannot be updated.
    """
    configure = _AGENTS.get(agent.strip().lower())
    if configure is None:
        raise ConfigError(
            f"'{agent}' is not a supported agent. Supported: {', '.join(SUPPORTED_AGENTS)}."
        )
    return configure(launch, path, name, write)
