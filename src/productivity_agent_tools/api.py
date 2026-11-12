"""The interface every API adapter provides to the product modules.

A product module (Jira today) talks to its service through an ``ApiClient``
and knows nothing about how requests are authenticated, verified or logged.
Each kind of service gets one adapter: ``AtlassianClient`` for Atlassian Data
Center hosts, and later others, such as one for Microsoft Graph with its own
sign-in. The safety switch lives in the interface so that no adapter can
forget it: ``write`` must send nothing while ``dry_run`` is on.
"""

from dataclasses import dataclass
from typing import Protocol

from pydantic import JsonValue


@dataclass(frozen=True, slots=True)
class WriteOutcome:
    """Result of a write request, whether or not it was sent.

    Attributes:
        sent: False when dry-run blocked the request.
        method: HTTP method of the request.
        path: Request path relative to the base URL.
        payload: JSON body of the request.
        response: Parsed response body when sent; None otherwise.
    """

    sent: bool
    method: str
    path: str
    payload: dict[str, JsonValue]
    response: JsonValue = None


class ApiClient(Protocol):
    """An authenticated JSON API on one base URL.

    Failures are raised as ``AgentToolsError`` subclasses carrying a
    plain-language message that is safe to show to the user and the agent.

    Attributes:
        base_url: Address every request path is relative to, with no trailing slash.
        dry_run: When True, ``write`` describes the request but sends nothing.
    """

    base_url: str
    dry_run: bool

    async def get(self, path: str, params: dict[str, str | int] | None = None) -> JsonValue:
        """Send a read request and return the parsed JSON body.

        Args:
            path: Request path relative to the base URL.
            params: Optional query parameters.

        Returns:
            The decoded JSON body, or None for an empty response.
        """
        ...

    async def get_text(self, path: str, params: dict[str, str | int] | None = None) -> str:
        """Send a read request for a plain-text resource, such as a raw diff.

        Args:
            path: Request path relative to the base URL.
            params: Optional query parameters.

        Returns:
            The response body as text; empty for an empty response.
        """
        ...

    async def query(self, path: str, payload: dict[str, JsonValue]) -> JsonValue:
        """Send a read that the service only accepts as a POST with a JSON body.

        This is not gated by dry-run, so it must only ever be pointed at an
        endpoint that changes nothing, such as a search.

        Args:
            path: Request path relative to the base URL.
            payload: JSON body describing what to read.

        Returns:
            The decoded JSON body, or None for an empty response.
        """
        ...

    async def write(self, method: str, path: str, payload: dict[str, JsonValue]) -> WriteOutcome:
        """Send a write request, unless dry-run is on.

        Args:
            method: HTTP method, such as ``"POST"``.
            path: Request path relative to the base URL.
            payload: JSON body to send.

        Returns:
            The outcome, with ``sent=False`` when dry-run blocked the request.
        """
        ...

    async def aclose(self) -> None:
        """Release the adapter's connections."""
        ...
