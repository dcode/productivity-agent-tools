"""How a product's requests are authenticated, kept apart from the adapters.

An adapter is not handed a secret. It is handed a ``HeaderProvider`` and asks
it for the authentication headers before every request, so a credential that
changes while the server runs (a refreshed OAuth token, a new browser
session) is picked up without reconnecting. A personal access token is the
simplest case: a provider that always answers with the same bearer header.

Each product names a ``CredentialResolver``, which finds the credential when
the product is connected and says where it came from.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from productivity_agent_tools.tokens import resolve_token


class HeaderProvider(Protocol):
    """Supplies the authentication headers for one request."""

    async def __call__(self) -> Mapping[str, str]:
        """Return the headers that authenticate a request made now.

        Returns:
            Header names and values. The values are secrets and must not be logged.

        Raises:
            AgentToolsError: If the credential is gone or can no longer be renewed.
        """
        ...


@dataclass(frozen=True, slots=True)
class Credential:
    """A product's way of authenticating, and a description of where it came from.

    Attributes:
        headers: Supplies the authentication headers. Excluded from ``repr`` so that
            nothing it holds can be logged by accident.
        source: Human-readable description of the source, safe to display.
        label: What kind of credential it is, as shown beside the source.
        describe: Describes the source as it is now, for a status tool, when that can
            change while the server runs (a new sign-in); None when ``source`` stays true.
    """

    headers: HeaderProvider = field(repr=False)
    source: str
    label: str = "token"
    describe: Callable[[], Awaitable[str]] | None = field(default=None, repr=False)


class CredentialResolver(Protocol):
    """Finds a product's credential when the product is connected."""

    async def __call__(self, name: str, env_file: Path | None) -> Credential:
        """Find the credential stored under a name.

        Args:
            name: Name the credential is stored under, such as ``"jira"``.
            env_file: The configured token env file, for resolvers that read one.

        Returns:
            The credential and where it was found.

        Raises:
            AgentToolsError: If there is no usable credential.
        """
        ...


def bearer_token(token: str) -> HeaderProvider:
    """Wrap a fixed token in a provider that always sends it as a bearer credential.

    Args:
        token: The access token.

    Returns:
        A provider answering with the same ``Authorization`` header every time.
    """
    headers = {"Authorization": f"Bearer {token}"}

    async def provide() -> Mapping[str, str]:
        return headers

    return provide


async def personal_access_token(name: str, env_file: Path | None) -> Credential:
    """Resolve a personal access token from the environment, an env file or the keychain.

    Args:
        name: Name the token is stored under, such as ``"jira"``.
        env_file: Optional env file to consult after the environment.

    Returns:
        A bearer credential built from the token, and where the token was found.

    Raises:
        TokenNotFoundError: If no source holds a token.
        ConfigError: If a configured env file cannot be read.
    """
    token = await resolve_token(name, env_file)
    return Credential(headers=bearer_token(token.value), source=token.source)
