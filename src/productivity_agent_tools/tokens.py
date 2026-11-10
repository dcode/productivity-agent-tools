"""Personal access token lookup.

Sources are tried in order, explicit before implicit:

1. The environment variable ``<PRODUCT>_TOKEN`` (for example ``JIRA_TOKEN``).
2. The same key inside an env file, when a path is configured.
3. The macOS Keychain, under this package's service name.
"""

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import keyring
import keyring.errors

from productivity_agent_tools.errors import ConfigError, TokenNotFoundError

KEYRING_SERVICE: str = "productivity-agent-tools"


@dataclass(frozen=True, slots=True)
class ResolvedToken:
    """A token and a description of where it came from.

    Attributes:
        value: The secret token. Excluded from ``repr`` so it cannot be logged by accident.
        source: Human-readable description of the source, safe to display.
    """

    value: str = field(repr=False)
    source: str


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
    env_file: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> ResolvedToken:
    """Find the personal access token for a product.

    Args:
        product: Product identifier such as ``"jira"``.
        env_file: Optional env file to consult after the environment.
        environ: Environment mapping to read; defaults to the process environment.

    Returns:
        The token and where it was found.

    Raises:
        TokenNotFoundError: If no source holds a token.
        ConfigError: If a configured env file cannot be read.
    """
    name = env_var_name(product)
    env = os.environ if environ is None else environ
    if from_env := env.get(name, "").strip():
        return ResolvedToken(value=from_env, source=f"environment variable {name}")
    if env_file is not None:
        if from_file := await _from_env_file(env_file, name):
            return ResolvedToken(value=from_file, source=f"env file {env_file}")
    if from_keychain := await _from_keychain(product):
        return ResolvedToken(value=from_keychain, source="macOS Keychain")
    raise TokenNotFoundError(
        f"No {product} token found. Run 'productivity-agent-tools set-token', "
        f"set {name}, or point token_env_file at a file defining {name}."
    )


async def store_token(product: str, token: str) -> None:
    """Save a product's token in the system keychain.

    Args:
        product: Product identifier such as ``"jira"``.
        token: The personal access token to store.
    """
    await asyncio.to_thread(keyring.set_password, KEYRING_SERVICE, product, token)
