"""Personal access token lookup.

Sources are tried in order, explicit before implicit:

1. The environment variable ``<PRODUCT>_TOKEN`` (for example ``JIRA_TOKEN``).
2. A command, ``token_cmd``, whose output is the token (``sops -d ...``, ``pass ...``).
3. The same ``<PRODUCT>_TOKEN`` key inside an env file, ``token_env_file``.
4. The system keychain (the macOS Keychain, or the Secret Service on Linux), under
   this package's service name, where ``set-token`` puts it.

``token_cmd`` and ``token_env_file`` can be set at the top of the config, in a
``[group.<name>]`` table or in a product's own table; the most specific wins.
"""

import asyncio
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import keyring
import keyring.errors

from productivity_agent_tools.errors import ConfigError, TokenNotFoundError

KEYRING_SERVICE: str = "productivity-agent-tools"
COMMAND_TIMEOUT_SECONDS: float = 30.0
PRODUCT_PLACEHOLDER: str = "{product}"
"""Replaced, in each word of ``token_cmd``, by the token name (``jira``, ``bitbucket``)."""


@dataclass(frozen=True, slots=True)
class ResolvedToken:
    """A token and a description of where it came from.

    Attributes:
        value: The secret token. Excluded from ``repr`` so it cannot be logged by accident.
        source: Human-readable description of the source, safe to display.
    """

    value: str = field(repr=False)
    source: str


@dataclass(frozen=True, slots=True, kw_only=True)
class TokenSources:
    """Where, besides the environment and the keychain, a product's token may be found.

    Attributes:
        env_file: An env file defining ``<PRODUCT>_TOKEN``, or None.
        command: A command (program and arguments) that prints the token, or None.
            ``{product}`` in any word is replaced by the token name.
        command_timeout: How long the command may run, in seconds.
    """

    env_file: Path | None = None
    command: tuple[str, ...] | None = None
    command_timeout: float = COMMAND_TIMEOUT_SECONDS


def env_var_name(product: str) -> str:
    """Return the environment variable name for a product's token.

    Args:
        product: Product identifier such as ``"jira"``.

    Returns:
        The variable name, such as ``"JIRA_TOKEN"``.
    """
    return f"{product.upper()}_TOKEN"


def parse_env_file(text: str) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines from an env file.

    Blank lines and ``#`` comments are skipped, a leading ``export`` is
    allowed, and matching single or double quotes around a value are removed.

    Args:
        text: Contents of the env file.

    Returns:
        The variables defined in the file.
    """
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.removeprefix("export ").partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


async def _from_command(command: Sequence[str], product: str, limit_seconds: float) -> str:
    """Run ``token_cmd`` and return what it prints as the token.

    The command runs without a shell, with no input, and its output is never
    logged or shown: only its exit status and the first line of its error output
    are, so a failing ``sops`` or ``pass`` can be diagnosed.

    Args:
        command: Program and arguments, ``{product}`` not yet replaced.
        product: Token name, put in place of ``{product}``.
        limit_seconds: Seconds to wait before stopping the command.

    Returns:
        The token: the command's output with surrounding whitespace removed.

    Raises:
        ConfigError: If the command cannot be started, fails, times out, or prints
            anything other than one line.
    """
    argv = [word.replace(PRODUCT_PLACEHOLDER, product) for word in command]
    label = f"token_cmd for {product} ({argv[0]})"
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise ConfigError(f"Could not start {label}: {exc.strerror or exc}.") from exc
    try:
        async with asyncio.timeout(limit_seconds):
            stdout, stderr = await process.communicate()
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise ConfigError(f"{label} did not finish within {limit_seconds:g} seconds.") from exc
    if process.returncode != 0:
        lines = stderr.decode(errors="replace").strip().splitlines()
        reason = f": {lines[0][:200]}" if lines else ""
        raise ConfigError(f"{label} exited with status {process.returncode}{reason}")
    token = stdout.decode(errors="replace").strip()
    if not token:
        raise ConfigError(f"{label} printed nothing; it must print the token.")
    if "\n" in token:
        raise ConfigError(f"{label} printed more than one line; it must print only the token.")
    return token


async def _from_env_file(path: Path, name: str) -> str | None:
    """Look up a variable in an env file.

    Args:
        path: Path of the env file.
        name: Variable to look up.

    Returns:
        The value, or None if the file does not define it.

    Raises:
        ConfigError: If the file cannot be read.
    """
    try:
        text = await asyncio.to_thread(path.read_text, "utf-8")
    except OSError as exc:
        raise ConfigError(f"Could not read token env file {path}: {exc.strerror}.") from exc
    return parse_env_file(text).get(name) or None


async def _from_keychain(product: str) -> str | None:
    """Look up a product's token in the system keychain.

    Args:
        product: Product identifier such as ``"jira"``.

    Returns:
        The token, or None if there is no entry or the keychain is unavailable.
    """
    try:
        return await asyncio.to_thread(keyring.get_password, KEYRING_SERVICE, product)
    except keyring.errors.KeyringError:
        return None


async def resolve_token(
    product: str,
    sources: TokenSources | None = None,
    environ: Mapping[str, str] | None = None,
) -> ResolvedToken:
    """Find the personal access token for a product.

    Args:
        product: Token name, such as ``"jira"``.
        sources: The ``token_cmd`` and ``token_env_file`` that apply to the product.
        environ: Environment mapping to read; defaults to the process environment.

    Returns:
        The token and where it was found.

    Raises:
        TokenNotFoundError: If no source holds a token.
        ConfigError: If a configured env file cannot be read or the command fails.
    """
    found = sources or TokenSources()
    name = env_var_name(product)
    env = os.environ if environ is None else environ
    if from_env := env.get(name, "").strip():
        return ResolvedToken(value=from_env, source=f"environment variable {name}")
    if found.command:
        token = await _from_command(found.command, product, found.command_timeout)
        program = found.command[0].replace(PRODUCT_PLACEHOLDER, product)
        return ResolvedToken(value=token, source=f"token_cmd ({program})")
    if found.env_file is not None:
        if from_file := await _from_env_file(found.env_file, name):
            return ResolvedToken(value=from_file, source=f"env file {found.env_file}")
    if from_keychain := await _from_keychain(product):
        return ResolvedToken(value=from_keychain, source="the system keychain")
    looked = [f"environment variable {name}"]
    if found.env_file is not None:
        looked.append(f"env file {found.env_file} (no {name} in it)")
    looked.append("the system keychain")
    raise TokenNotFoundError(
        f"No {product} token found (looked in: {'; '.join(looked)}). Store one with "
        f"'productivity-agent-tools set-token {product}', export {name}, or set token_cmd "
        f"or token_env_file in the config (at the top, in a [group.<name>] table, or in "
        f"[{product}])."
    )


async def store_token(product: str, token: str) -> None:
    """Save a product's token in the system keychain, replacing any earlier one.

    Args:
        product: Product identifier such as ``"jira"``.
        token: The personal access token to store.
    """
    await asyncio.to_thread(keyring.set_password, KEYRING_SERVICE, product, token)
