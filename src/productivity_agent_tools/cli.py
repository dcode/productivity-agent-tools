"""Command-line entry point: run the MCP server and the setup commands."""

import asyncio
import contextlib
import getpass
import json
import sys
from collections.abc import AsyncGenerator, Sequence
from pathlib import Path
from typing import Annotated, Any

import httpx
import structlog
from cyclopts import App, Parameter
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from rich.console import Console
from rich.syntax import Syntax

from productivity_agent_tools import __version__
from productivity_agent_tools.agents import (
    DEFAULT_EXTENSION_NAME,
    SUPPORTED_AGENTS,
    configure_agent,
    find_launch,
)
from productivity_agent_tools.config import (
    DEFAULT_CONFIG_PATH,
    ProductSettings,
    Settings,
    apply_overrides,
    load_settings,
)
from productivity_agent_tools.credentials import Credential
from productivity_agent_tools.errors import AgentToolsError, ConfigError
from productivity_agent_tools.logs import LOG_LEVELS, configure_logging
from productivity_agent_tools.microsoft.login import extract_refresh_token
from productivity_agent_tools.microsoft.login import seed as seed_microsoft
from productivity_agent_tools.microsoft.session import TOKEN_NAME as MICROSOFT
from productivity_agent_tools.microsoft.settings import load_microsoft_settings, redeem_config
from productivity_agent_tools.microsoft.signin import sign_in as sign_in_microsoft
from productivity_agent_tools.products import Product, installed_products
from productivity_agent_tools.server import Connected, build_server
from productivity_agent_tools.tokens import env_var_name, store_token

_KEYCHAINS: tuple[str, ...] = (
    "/Library/Keychains/System.keychain",
    "/System/Library/Keychains/SystemRootCertificates.keychain",
)

_log = structlog.get_logger(__name__)


def _no_products() -> str:
    """Explain that no product is configured, naming the installed ones.

    Returns:
        The message.
    """
    names = [f"[{name}]" for name in installed_products()]
    tables = ", ".join(names[:-1]) + f" or {names[-1]}" if len(names) > 1 else "".join(names)
    return (
        f"No product is configured. Add a {tables} table to the config; an empty "
        "table is enough to turn a product on with its defaults."
    )


app = App(
    name="productivity-agent-tools",
    help="MCP server and setup commands for work tools: Jira, Bitbucket and Confluence "
    "(Data Center), ServiceNow, and Outlook mail and calendar.",
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
        help="Env file of token lines such as JIRA_TOKEN=... (the product name in capitals, "
        "then _TOKEN). Overrides token_env_file at every level of the config file.",
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


@contextlib.asynccontextmanager
async def _open_server(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> AsyncGenerator[MCPServer]:
    """Connect to every configured product and build the MCP server.

    Args:
        settings: The resolved settings.
        transport: Replacement HTTP transport, used by tests.

    Yields:
        The MCP server, with its connections open until the block exits.

    Raises:
        AgentToolsError: If a token or CA bundle cannot be loaded.
    """
    if not settings.products:
        raise ConfigError(_no_products())
    _log.info(
        "server_starting",
        products=list(settings.products),
        dry_run=settings.dry_run,
        log_level=settings.log_level,
    )
    products = installed_products()
    async with contextlib.AsyncExitStack() as stack:
        connected: dict[str, Connected] = {}
        # Products that share a token name (the two Outlook products share "microsoft")
        # share one resolved credential, so an OAuth token is minted and its rotating
        # refresh token is renewed by a single provider, never raced between two.
        credentials: dict[str, Credential] = {}
        for name, product_settings in settings.products.items():
            product = products[name]
            if product.token not in credentials:
                credentials[product.token] = await product.resolve_credential(
                    product.token, settings.token_sources_for(product_settings)
                )
            credential = credentials[product.token]
            client = await stack.enter_async_context(
                product.adapter(
                    settings.connection_for(product_settings),
                    credential.headers,
                    dry_run=settings.dry_run,
                    transport=transport,
                )
            )
            connected[name] = Connected(client, credential.source, credential.describe)
        yield build_server(settings, connected)


async def _serve(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
    """Run the MCP server over stdio until the client disconnects.

    Args:
        settings: The resolved settings.
        transport: Replacement HTTP transport, used by tests.

    Raises:
        AgentToolsError: If a token or CA bundle cannot be loaded.
    """
    async with _open_server(settings, transport) as server:
        await server.run_stdio_async()


def _accepts_text(schema: object, definitions: dict[str, Any] | None = None) -> bool:
    """Tell whether a tool parameter's schema allows a string.

    Args:
        schema: JSON schema of one parameter.
        definitions: The tool schema's ``$defs``, which a parameter may point into. A
            named choice of words, such as a branch type, is described that way.

    Returns:
        True if a plain string is a valid value.
    """
    if not isinstance(schema, dict):
        return True
    if schema.get("type") == "string":
        return True
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        target = (definitions or {}).get(reference.removeprefix("#/$defs/"))
        return target is not None and _accepts_text(target, definitions)
    return any(_accepts_text(option, definitions) for option in schema.get("anyOf", []))


def _tool_arguments(schema: dict[str, Any], pairs: tuple[str, ...]) -> dict[str, Any]:
    """Turn ``name=value`` words into arguments typed the way a tool expects.

    A value for a text parameter is used as written. Anything else is read as
    JSON, so ``max_results=5`` is a number and ``flag=true`` a boolean.

    Args:
        schema: The tool's input schema.
        pairs: The ``name=value`` words from the command line.

    Returns:
        The arguments by name.

    Raises:
        AgentToolsError: If a word is malformed, names an unknown parameter, or has a bad value.
    """
    properties: dict[str, Any] = schema.get("properties") or {}
    known = ", ".join(properties) or "none"
    arguments: dict[str, Any] = {}
    for pair in pairs:
        name, separator, raw = pair.partition("=")
        if not separator:
            raise AgentToolsError(f"Argument '{pair}' must be written as name=value.")
        if name not in properties:
            raise AgentToolsError(f"'{name}' is not a parameter of this tool. Parameters: {known}.")
        if _accepts_text(properties[name], schema.get("$defs")):
            arguments[name] = raw
            continue
        try:
            arguments[name] = json.loads(raw)
        except ValueError as exc:
            raise AgentToolsError(
                f"The value of '{name}' must be a number, true/false or JSON, got '{raw}'."
            ) from exc
    return arguments


def _describe_tools(tools: Sequence[Any]) -> str:
    """Render the tool list: name, parameters and the first line of each description.

    Args:
        tools: The tools the server offers.

    Returns:
        One block of text, a tool per paragraph.
    """
    blocks: list[str] = []
    for tool in sorted(tools, key=lambda item: item.name):
        schema: dict[str, Any] = tool.input_schema or {}
        required = set(schema.get("required") or [])
        parameters = " ".join(
            f"{name}=..." if name in required else f"[{name}=...]"
            for name in schema.get("properties") or {}
        )
        summary = (tool.description or "").strip().splitlines()[0:1]
        blocks.append(f"{tool.name} {parameters}".rstrip() + "".join(f"\n    {s}" for s in summary))
    return "\n".join(blocks)


async def _call(
    settings: Settings,
    tool: str | None,
    pairs: tuple[str, ...],
    transport: httpx.AsyncBaseTransport | None = None,
    field: str | None = None,
) -> str:
    """Run one tool directly, with no agent, and render its result.

    Args:
        settings: The resolved settings.
        tool: Name of the tool, or None to list the tools instead.
        pairs: ``name=value`` arguments for the tool.
        transport: Replacement HTTP transport, used by tests.
        field: Return only this top-level field of the result; text is returned as written.

    Returns:
        The tool's result as indented JSON, the chosen field, or the tool list.

    Raises:
        AgentToolsError: If the tool, an argument or the field is unknown, or the tool fails.
    """
    async with _open_server(settings, transport) as server:
        tools = {item.name: item for item in await server.list_tools()}
        if tool is None:
            return _describe_tools(list(tools.values()))
        if tool not in tools:
            raise AgentToolsError(
                f"'{tool}' is not a tool. Run 'call' with no tool name to list them."
            )
        arguments = _tool_arguments(tools[tool].input_schema or {}, pairs)
        try:
            result = await server.call_tool(tool, arguments)
        except ToolError as exc:
            raise AgentToolsError(str(exc)) from exc
        content = getattr(result, "structured_content", None)
        if field is not None:
            if not isinstance(content, dict) or field not in content:
                fields = ", ".join(content) if isinstance(content, dict) else "none"
                raise AgentToolsError(f"The result has no field '{field}'. Fields: {fields}.")
            content = content[field]
            if isinstance(content, str):
                return content
        return json.dumps(content, indent=2, ensure_ascii=False)


# Result fields worth colouring when a person reads them in a terminal.
_FIELD_LEXERS: dict[str, str] = {"diff": "diff"}


def _show(text: str, field: str | None) -> None:
    """Print a result, coloured when it is a known kind of text shown in a terminal.

    Piped or redirected output is always the plain text, so scripts and
    agents get exactly what the tool returned.

    Args:
        text: The rendered result.
        field: The field that was asked for, if any.
    """
    lexer = _FIELD_LEXERS.get(field or "")
    if lexer and sys.stdout.isatty():
        Console().print(Syntax(text, lexer, theme="ansi_dark", background_color="default"))
    else:
        print(text)


async def _doctor_product(
    settings: Settings,
    product: Product[Any, Any],
    product_settings: ProductSettings,
    transport: httpx.AsyncBaseTransport | None,
) -> bool:
    """Check one product's token, connection and its own reads, printing one line per check.

    Args:
        settings: The resolved settings.
        product: The product.
        product_settings: The product's settings.
        transport: Replacement HTTP transport, used by tests.

    Returns:
        True if every check passed.
    """
    connection = settings.connection_for(product_settings)
    print(f"[info] {product.name} extra_cookies={','.join(connection.cookies) or 'none'}")
    try:
        credential = await product.resolve_credential(
            product.token, settings.token_sources_for(product_settings)
        )
        print(f"[ok]   {product.name} {credential.label} found in {credential.source}")
        async with product.adapter(
            connection, credential.headers, dry_run=settings.dry_run, transport=transport
        ) as core:
            async for line in product.activate(core, product_settings).doctor():
                print(f"[ok]   {line}")
    except AgentToolsError as exc:
        print(f"[fail] {exc}")
        return False
    return True


async def _doctor(
    config: Path, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> bool:
    """Run the connectivity checks for every configured product.

    Args:
        config: Path of the config file that was loaded.
        settings: The resolved settings.
        transport: Replacement HTTP transport, used by tests.

    Returns:
        True if every check passed.
    """
    print(f"[ok]   config loaded from {config}")
    print(f"[info] dry_run={settings.dry_run}")
    print(f"[info] ca_bundle={settings.ca_bundle or 'system trust store'}")
    if not settings.products:
        print(f"[fail] {_no_products()}")
        return False
    passed = True
    for name, product in installed_products().items():
        product_settings = settings.products.get(name)
        if product_settings is None:
            print(f"[info] {name} is not configured (add a [{name}] table to turn it on)")
        elif not await _doctor_product(settings, product, product_settings, transport):
            passed = False
    return passed


async def _export_ca(output: Path) -> int:
    """Export the keychain's trusted roots to a PEM bundle.

    Args:
        output: File to write.

    Returns:
        The number of certificates written.

    Raises:
        AgentToolsError: If the ``security`` tool fails or finds nothing.
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
        raise AgentToolsError(
            f"Could not export certificates: {stderr.decode(errors='replace').strip()}"
        )
    await asyncio.to_thread(output.write_bytes, stdout)
    return count


def _fail(exc: AgentToolsError) -> None:
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
    """Run the MCP server over stdio. This is what an agent such as Goose starts."""

    async def run() -> None:
        await _serve(await _resolve_settings(config, dry_run, cookie, token_env_file, log_level))

    try:
        asyncio.run(run())
    except AgentToolsError as exc:
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
    """Check the config, then each configured product: its credential, connection and a read.

    Read-only. Exits non-zero if any check fails.
    """
    expanded = config.expanduser()

    async def run() -> bool:
        settings = await _resolve_settings(config, dry_run, cookie, token_env_file, log_level)
        return await _doctor(expanded, settings)

    try:
        passed = asyncio.run(run())
    except AgentToolsError as exc:
        _fail(exc)
    else:
        sys.exit(0 if passed else 1)


@app.command(name="call")
def call(
    tool: Annotated[
        str | None, Parameter(help="Name of the tool to run, such as jira_search.")
    ] = None,
    /,
    *arguments: Annotated[str, Parameter(help="The tool's arguments, each written as name=value.")],
    config: ConfigOption = DEFAULT_CONFIG_PATH,
    dry_run: DryRunOption = None,
    cookie: CookieOption = None,
    token_env_file: TokenEnvFileOption = None,
    log_level: LogLevelOption = None,
    field: Annotated[
        str | None,
        Parameter(
            name="--field",
            help="Print only this field of the result. Text is printed as written, not as "
            "JSON, and a diff is coloured when shown in a terminal.",
        ),
    ] = None,
) -> None:
    """Run one tool directly, without an agent, and print its result as JSON.

    With no tool name, lists the tools and their parameters. Writes obey
    dry-run exactly as they do under an agent.
    """

    async def run() -> str:
        settings = await _resolve_settings(config, dry_run, cookie, token_env_file, log_level)
        return await _call(settings, tool, arguments, field=field)

    try:
        _show(asyncio.run(run()), field)
    except AgentToolsError as exc:
        _fail(exc)


FromResponseOption = Annotated[
    Path | None,
    Parameter(
        name="--from-response",
        help="Microsoft only: a file holding the token response captured from the browser; "
        "its refresh_token is used. Leave out to paste the refresh token at a hidden prompt.",
    ),
]
VerifyOption = Annotated[
    bool,
    Parameter(
        name="--verify",
        help="Microsoft only: redeem the refresh token once to confirm it works.",
    ),
]


def _token_targets() -> dict[str, str]:
    """Name every token ``set-token`` can store, with what it is for.

    A token shared by several products (the Outlook pair) is listed once. A
    product that signs in through the browser and nothing else is not listed.

    Returns:
        A one-line description of each token, by token name, in name order.
    """
    titles: dict[str, list[str]] = {}
    for product in installed_products().values():
        if product.login is None:
            titles.setdefault(product.token, []).append(product.title)
    described = {
        name: f"{', '.join(found)} personal access token" for name, found in titles.items()
    }
    if MICROSOFT in described:
        outlook = described[MICROSOFT].removesuffix(" personal access token")
        described[MICROSOFT] = (
            f"{outlook}: a refresh token captured by hand (normally use 'login {MICROSOFT}')"
        )
    return dict(sorted(described.items()))


def _choose_token(targets: dict[str, str]) -> str:
    """Ask which token to store, from a numbered list.

    Args:
        targets: The tokens that can be stored, as ``_token_targets`` lists them.

    Returns:
        The chosen token name.

    Raises:
        AgentToolsError: If there is no terminal to ask in, or the answer is not one of them.
    """
    names = list(targets)
    listing = "\n".join(f"  {n}. {name}: {targets[name]}" for n, name in enumerate(names, 1))
    if not sys.stdin.isatty():
        raise AgentToolsError(
            f"Name the token to store, such as 'set-token {names[0]}'. Tokens:\n{listing}"
        )
    print(f"Which token do you want to store?\n{listing}")
    answer = input(f"Number or name [1-{len(names)}]: ").strip().lower()
    if answer.isdigit() and 1 <= int(answer) <= len(names):
        return names[int(answer) - 1]
    if answer in targets:
        return answer
    raise AgentToolsError(f"'{answer}' is not one of the tokens listed; nothing was stored.")


def _read_text(path: Path) -> str:
    """Read a UTF-8 text file, expanding ``~`` in its path.

    Args:
        path: The file.

    Returns:
        Its contents.

    Raises:
        OSError: If it cannot be read.
    """
    return path.expanduser().read_text("utf-8")


async def _seed_microsoft_token(config: Path, from_response: Path | None, verify: bool) -> str:
    """Store a Microsoft refresh token captured by hand, pasted or from a response file.

    Args:
        config: Path to the TOML configuration file; its ``[microsoft]`` table is read.
        from_response: File holding the captured JSON token response, or None to prompt.
        verify: Redeem the token once to confirm it works.

    Returns:
        A sentence to show the user.

    Raises:
        AgentToolsError: If the file cannot be read, nothing is entered, there is no
            ``[microsoft]`` table, or verification fails.
    """
    if from_response is not None:
        try:
            text = await asyncio.to_thread(_read_text, from_response)
        except OSError as exc:
            raise AgentToolsError(f"Could not read {from_response}: {exc.strerror}.") from exc
    else:
        text = getpass.getpass(
            "Microsoft refresh token (hidden; 'login microsoft' collects one for you): "
        )
    ms_settings = await load_microsoft_settings(config)
    token = extract_refresh_token(text)
    if not token:
        raise AgentToolsError("No refresh token given; nothing was stored.")
    return await seed_microsoft(
        redeem_config(ms_settings),
        token,
        verify=verify,
        window_hours=ms_settings.sign_in_hours,
    )


async def _set_token(
    product: str | None, config: Path, from_response: Path | None, verify: bool
) -> str:
    """Store one token in the system keychain, asking which when none is named.

    Args:
        product: The token or product name, or None to choose from a list.
        config: Path to the TOML configuration file (read for Microsoft only).
        from_response: Microsoft only: a captured token response to read.
        verify: Microsoft only: redeem the refresh token once to confirm it.

    Returns:
        A sentence to show the user.

    Raises:
        AgentToolsError: If the name is not a token, the product signs in through the
            browser, an option does not apply, or nothing was entered.
    """
    targets = _token_targets()
    name = (product or "").strip().lower() or _choose_token(targets)
    if name in _microsoft_names():
        return await _seed_microsoft_token(config, from_response, verify)
    if from_response is not None:
        raise AgentToolsError(f"--from-response is for 'set-token {MICROSOFT}' only.")
    signs_in = sorted(n for n, p in installed_products().items() if p.login and p.token == name)
    if signs_in:
        raise AgentToolsError(
            f"'{name}' has no token to store: it signs in through the browser. "
            f"Run 'productivity-agent-tools login {signs_in[0]}'."
        )
    if name not in targets:
        raise AgentToolsError(
            f"'{name}' is not a token this tool stores. Tokens: {', '.join(targets)}."
        )
    token = getpass.getpass(f"{targets[name]} for {name} (hidden): ").strip()
    if not token:
        raise AgentToolsError("No token entered; nothing was stored.")
    await store_token(name, token)
    variable = env_var_name(name)
    return (
        f"Stored the {name} token in the system keychain. An exported {variable}, a "
        "token_cmd or an env file entry, if you have one, is used before it."
    )


@app.command(name="set-token")
def set_token(
    product: str | None = None,
    /,
    *,
    config: ConfigOption = DEFAULT_CONFIG_PATH,
    from_response: FromResponseOption = None,
    verify: VerifyOption = True,
) -> None:
    """Store a token in the system keychain, typed at a hidden prompt.

    With no name, lists the tokens and asks which one. 'microsoft' stores an
    Outlook refresh token captured by hand; 'login microsoft' is the normal way
    to get one. Products that sign in through the browser (servicenow) have no
    token to store: use 'login'.

    Args:
        product: Which token, such as jira, bitbucket, confluence or microsoft.
        config: Path to the TOML configuration file.
        from_response: Microsoft only: a captured token response file.
        verify: Microsoft only: redeem the token once to confirm it.
    """
    try:
        print(asyncio.run(_set_token(product, config, from_response, verify)))
    except AgentToolsError as exc:
        _fail(exc)


def _microsoft_names() -> frozenset[str]:
    """Names ``login`` accepts for the Microsoft sign-in: the token and its products."""
    shared = {name for name, p in installed_products().items() if p.token == MICROSOFT}
    return frozenset({MICROSOFT, *shared})


async def _login_microsoft(config: Path) -> str:
    """Sign in to Microsoft through the browser and store the refresh token.

    The OAuth client comes from the ``[microsoft]`` table, not from a product's,
    because the Outlook products share one sign-in.

    Args:
        config: Path to the TOML configuration file.

    Returns:
        A sentence to show the user.

    Raises:
        AgentToolsError: If there is no ``[microsoft]`` table or the sign-in fails.
    """
    return await sign_in_microsoft(await load_microsoft_settings(config))


async def _login(settings: Settings, name: str) -> str:
    """Sign in to one product interactively and store the result.

    Args:
        settings: The resolved settings.
        name: The product to sign in to.

    Returns:
        A sentence to show the user.

    Raises:
        AgentToolsError: If the product has no interactive sign-in, is not configured,
            or the sign-in fails.
    """
    products = installed_products()
    product = products.get(name)
    if product is None or product.login is None:
        able = ", ".join(sorted({MICROSOFT, *(n for n, p in products.items() if p.login)}))
        raise ConfigError(
            f"'{name}' has no interactive sign-in. Products that do: {able}. "
            "The others use a token: run set-token."
        )
    product_settings = settings.products.get(name)
    if product_settings is None:
        raise ConfigError(f"{name} is not configured. Add a [{name}] table to the config.")
    return await product.login(settings.connection_for(product_settings), product_settings)


@app.command(name="login")
def login(
    product: str,
    *,
    config: ConfigOption = DEFAULT_CONFIG_PATH,
    log_level: LogLevelOption = None,
) -> None:
    """Sign in to a product through the browser and store the result in the system keychain.

    'microsoft' (or either Outlook product) signs in to Microsoft once for both
    Outlook products, using the client in the [microsoft] table.

    Args:
        product: Which product to sign in to, such as servicenow or microsoft.
        config: Path to the TOML configuration file.
        log_level: Least severe level logged to stderr.
    """
    name = product.strip().lower()

    async def run() -> str:
        settings = await _resolve_settings(config, None, None, None, log_level)
        if name in _microsoft_names():
            return await _login_microsoft(config)
        return await _login(settings, name)

    try:
        print(asyncio.run(run()))
    except AgentToolsError as exc:
        _fail(exc)


@app.command(name="login-microsoft", show=False)
def login_microsoft(
    *,
    config: ConfigOption = DEFAULT_CONFIG_PATH,
    from_response: FromResponseOption = None,
    verify: VerifyOption = True,
) -> None:
    """Deprecated: use 'set-token microsoft' (or, normally, 'login microsoft')."""
    print(
        "note: 'login-microsoft' is deprecated and will be removed; use "
        "'set-token microsoft', or 'login microsoft' to sign in through the browser.",
        file=sys.stderr,
    )
    try:
        print(asyncio.run(_seed_microsoft_token(config, from_response, verify)))
    except AgentToolsError as exc:
        _fail(exc)


@app.command(name="export-ca")
def export_ca(output: Path = Path("~/ca-bundle.pem")) -> None:
    """Export the macOS keychain's trusted root certificates to a PEM bundle.

    Args:
        output: Where to write the bundle.
    """
    target = output.expanduser()
    try:
        count = asyncio.run(_export_ca(target))
    except AgentToolsError as exc:
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
    except AgentToolsError as exc:
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
