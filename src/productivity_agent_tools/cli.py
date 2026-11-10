"""Command-line entry point: run the MCP server and the setup commands."""

import asyncio
import getpass
import sys
from pathlib import Path
from typing import Annotated

import httpx
import structlog
from cyclopts import App, Parameter

from productivity_agent_tools import __version__
from productivity_agent_tools.agents import (
    DEFAULT_EXTENSION_NAME,
    SUPPORTED_AGENTS,
    configure_agent,
    find_launch,
)
from productivity_agent_tools.config import (
    DEFAULT_CONFIG_PATH,
    Settings,
    apply_overrides,
    load_settings,
)
from productivity_agent_tools.errors import AtlassianError
from productivity_agent_tools.http import AtlassianClient
from productivity_agent_tools.jira.client import JiraClient
from productivity_agent_tools.logs import LOG_LEVELS, configure_logging
from productivity_agent_tools.server import build_server
from productivity_agent_tools.tokens import resolve_token, store_token

_KEYCHAINS: tuple[str, ...] = (
    "/Library/Keychains/System.keychain",
    "/System/Library/Keychains/SystemRootCertificates.keychain",
)

_log = structlog.get_logger(__name__)

app = App(
    name="productivity-agent-tools",
    help="MCP server and setup commands for self-hosted Atlassian tools.",
    version=__version__,
)

ConfigOption = Annotated[
    Path, Parameter(name="--config", help="Path to the TOML configuration file.")
]
DryRunOption = Annotated[
    bool | None,
    Parameter(
        name="--dry-run",
        help="Block all writes and only show what would be sent. "
        "Overrides the config file; the built-in default is on.",
    ),
]
CookieOption = Annotated[
    list[str] | None,
    Parameter(
        name="--cookie",
        negative="",
        help="Extra cookie to send with every request, as name=value. Repeatable. "
        "Adds to, and overrides by name, the [cookies] table in the config file.",
    ),
]
TokenEnvFileOption = Annotated[
    Path | None,
    Parameter(
        name="--token-env-file",
        help="Env file defining JIRA_TOKEN. Overrides the config file.",
    ),
]
LogLevelOption = Annotated[
    str | None,
    Parameter(
        name="--log-level",
        help=f"Least severe level logged to stderr: {', '.join(LOG_LEVELS)}. debug adds one "
        "line per HTTP request and response (no token, cookie values or bodies). "
        "Overrides the config file; the built-in default is info.",
    ),
]


async def _resolve_settings(
    config: Path,
    dry_run: bool | None,
    cookie: list[str] | None,
    token_env_file: Path | None,
    log_level: str | None = None,
) -> Settings:
    """Load the config file, apply command-line overrides and set up logging.

    Args:
        config: Path to the TOML configuration file.
        dry_run: Override for dry-run, or None.
        cookie: Extra ``name=value`` cookies from the command line, or None.
        token_env_file: Override for the token env file, or None.
        log_level: Override for the log level, or None.

    Returns:
        The resolved settings.

    Raises:
        ConfigError: If the config file is missing or invalid.
    """
    settings = apply_overrides(
        await load_settings(config),
        dry_run=dry_run,
        cookies=cookie,
        token_env_file=token_env_file,
        log_level=log_level,
    )
    configure_logging(settings.log_level)
    return settings


async def _serve(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
    """Run the MCP server over stdio until the client disconnects.

    Args:
        settings: The resolved settings.
        transport: Replacement HTTP transport, used by tests.

    Raises:
        AtlassianError: If the token or CA bundle cannot be loaded.
    """
    token = await resolve_token("jira", settings.token_env_file)
    connection = settings.connection_for(settings.jira)
    _log.info(
        "server_starting",
        jira_base_url=connection.base_url,
        dry_run=settings.dry_run,
        extra_cookies=sorted(connection.cookies),
        token_source=token.source,
        log_level=settings.log_level,
    )
    async with AtlassianClient.from_connection(
        connection, token.value, dry_run=settings.dry_run, transport=transport
    ) as core:
        await build_server(settings, core, token.source).run_stdio_async()


async def _doctor(
    config: Path, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> bool:
    """Run the connectivity checks and print one line per check.

    Args:
        config: Path of the config file that was loaded.
        settings: The resolved settings.
        transport: Replacement HTTP transport, used by tests.

    Returns:
        True if every check passed.
    """
    print(f"[ok]   config loaded from {config}")
    connection = settings.connection_for(settings.jira)
    print(
        f"[info] dry_run={settings.dry_run} extra_cookies={','.join(connection.cookies) or 'none'}"
    )
    print(f"[info] ca_bundle={connection.ca_bundle or 'system trust store'}")
    try:
        token = await resolve_token("jira", settings.token_env_file)
        print(f"[ok]   token found in {token.source}")
        async with AtlassianClient.from_connection(
            connection, token.value, dry_run=settings.dry_run, transport=transport
        ) as core:
            jira = JiraClient(core, settings.jira)
            profile = await jira.myself()
            print(f"[ok]   authenticated to {jira.base_url} as {profile.get('displayName')}")
            print(f"[ok]   Jira version {await jira.server_version()}")
            result = await jira.search(settings.jira.presets.get("mine-open", ""), 1)
            print(f"[ok]   search works ({result.total} open issue(s) assigned to you)")
    except AtlassianError as exc:
        print(f"[fail] {exc}")
        return False
    return True


async def _export_ca(output: Path) -> int:
    """Export the keychain's trusted roots to a PEM bundle.

    Args:
        output: File to write.

    Returns:
        The number of certificates written.

    Raises:
        AtlassianError: If the ``security`` tool fails or finds nothing.
    """
    process = await asyncio.create_subprocess_exec(
        "/usr/bin/security",
        "find-certificate",
        "-a",
        "-p",
        *_KEYCHAINS,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    count = stdout.count(b"BEGIN CERTIFICATE")
    if process.returncode != 0 or count == 0:
        raise AtlassianError(
            f"Could not export certificates: {stderr.decode(errors='replace').strip()}"
        )
    await asyncio.to_thread(output.write_bytes, stdout)
    return count


def _fail(exc: AtlassianError) -> None:
    """Print an error to stderr and exit with status 1.

    Args:
        exc: The error to report.
    """
    print(f"error: {exc}", file=sys.stderr)
    sys.exit(1)


@app.command
def serve(
    *,
    config: ConfigOption = DEFAULT_CONFIG_PATH,
    dry_run: DryRunOption = None,
    cookie: CookieOption = None,
    token_env_file: TokenEnvFileOption = None,
    log_level: LogLevelOption = None,
) -> None:
    """Run the MCP server over stdio (this is what Goose launches)."""

    async def run() -> None:
        await _serve(await _resolve_settings(config, dry_run, cookie, token_env_file, log_level))

    try:
        asyncio.run(run())
    except AtlassianError as exc:
        _fail(exc)


@app.command
def doctor(
    *,
    config: ConfigOption = DEFAULT_CONFIG_PATH,
    dry_run: DryRunOption = None,
    cookie: CookieOption = None,
    token_env_file: TokenEnvFileOption = None,
    log_level: LogLevelOption = None,
) -> None:
    """Check configuration, token, TLS trust and login. Read-only."""
    expanded = config.expanduser()

    async def run() -> bool:
        settings = await _resolve_settings(config, dry_run, cookie, token_env_file, log_level)
        return await _doctor(expanded, settings)

    try:
        passed = asyncio.run(run())
    except AtlassianError as exc:
        _fail(exc)
    else:
        sys.exit(0 if passed else 1)


@app.command(name="set-token")
def set_token(product: str = "jira") -> None:
    """Store a personal access token in the macOS Keychain (hidden prompt).

    Args:
        product: Which product the token is for.
    """
    token = getpass.getpass(f"{product} personal access token (hidden): ").strip()
    if not token:
        _fail(AtlassianError("No token entered; nothing was stored."))
    asyncio.run(store_token(product, token))
    print(f"Stored the {product} token in the macOS Keychain.")


@app.command(name="export-ca")
def export_ca(output: Path = Path("~/ca-bundle.pem")) -> None:
    """Export the keychain's trusted root certificates to a PEM bundle.

    Args:
        output: Where to write the bundle.
    """
    target = output.expanduser()
    try:
        count = asyncio.run(_export_ca(target))
    except AtlassianError as exc:
        _fail(exc)
    else:
        print(f"Wrote {count} certificates to {target}. Set ca_bundle to this path.")


@app.command(name="configure-agent")
def configure_agent_command(
    *,
    agent: Annotated[
        str,
        Parameter(name="--agent", help=f"Agent to configure: {', '.join(SUPPORTED_AGENTS)}."),
    ] = "goose",
    name: Annotated[
        str, Parameter(name="--name", help="Name to register the server under in the agent.")
    ] = DEFAULT_EXTENSION_NAME,
    config: Annotated[
        Path | None,
        Parameter(
            name="--config",
            help="Tool configuration file the agent should start the server with. "
            "Leave out to use the default location.",
        ),
    ] = None,
    agent_config: Annotated[
        Path | None,
        Parameter(
            name="--agent-config",
            help="The agent's own configuration file. Leave out to use its default location.",
        ),
    ] = None,
    show: Annotated[
        bool,
        Parameter(name="--show", negative="", help="Print the entry without changing any file."),
    ] = False,
) -> None:
    """Add this server to an agent's configuration, pointing at this installation."""
    try:
        change = configure_agent(
            agent, find_launch(config), path=agent_config, name=name, write=not show
        )
    except AtlassianError as exc:
        _fail(exc)
    else:
        if not change.written:
            print(f"Would write this to {change.path}:\n")
            print(change.entry, end="")
            return
        verb = "Updated" if change.replaced else "Added"
        print(f"{verb} the '{name}' extension in {change.path}:\n")
        print(change.entry)
        if change.backup:
            print(f"The previous file was saved as {change.backup}.")
        print(f"Restart {agent} to load it. Writes stay blocked until dry_run is turned off.")


def main() -> None:
    """Run the command-line application."""
    app(sys.argv[1:])
