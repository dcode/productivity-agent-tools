"""The HTTP machinery every adapter shares, whatever kind of server it talks to.

``HttpAdapter`` implements the ``ApiClient`` protocol up to the point where a
response has to be understood: TLS trust, asking the credential provider for
headers before every request, extra cookies, dry-run for writes, logging
without secrets, and plain-language connectivity errors. It never follows a
redirect and keeps no cookies the server sets, so credentials only ever go to
the configured host. A subclass says how its kind of server reports results
and errors by implementing ``_interpret``.
"""

import ssl
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import TracebackType
from typing import ClassVar, Self

import httpx
import structlog
import truststore
from pydantic import JsonValue

from productivity_agent_tools.api import WriteOutcome
from productivity_agent_tools.config import Connection
from productivity_agent_tools.credentials import HeaderProvider
from productivity_agent_tools.errors import ConfigError, ConnectivityError

_log = structlog.get_logger(__name__)


def build_ssl_context(ca_bundle: Path | None) -> ssl.SSLContext:
    """Create a verifying TLS context.

    With no bundle, certificates are verified against the operating system's
    trust store (the Keychain on macOS, the certificate store on Windows, the
    system CA files on Linux), so an organization's internal CAs that the
    browser already trusts are trusted here too.

    Args:
        ca_bundle: PEM file of trusted roots, or None for the system trust store.

    Returns:
        A TLS context that verifies the server certificate.

    Raises:
        ConfigError: If the bundle is missing or not valid PEM.
    """
    if ca_bundle is None:
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    try:
        return ssl.create_default_context(cafile=str(ca_bundle))
    except (OSError, ssl.SSLError) as exc:
        raise ConfigError(
            f"Could not load CA bundle {ca_bundle}. Run 'productivity-agent-tools export-ca'."
        ) from exc


def is_certificate_error(exc: BaseException) -> bool:
    """Tell whether a transport failure was a rejected server certificate.

    The wording differs between OpenSSL and the operating system's verifier, so
    the exception chain is checked for the certificate error type first and
    the message text second.

    Args:
        exc: The transport error raised by the HTTP client.

    Returns:
        True if the server's certificate failed verification.
    """
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        current = current.__cause__ or current.__context__
    return "certificate" in str(exc).lower()


def without_query(url: httpx.URL) -> str:
    """Render a URL without its query string or fragment, which can carry sign-in state.

    Args:
        url: The URL to render.

    Returns:
        Scheme, host and path only.
    """
    return str(url.copy_with(query=None, fragment=None))


def response_event(
    response: httpx.Response, elapsed_seconds: float, diagnostic_headers: Iterable[str]
) -> dict[str, object]:
    """Collect the loggable facts about a response.

    Bodies, cookie values and query strings are left out; cookies the server
    set are listed by name only.

    Args:
        response: The HTTP response.
        elapsed_seconds: Time the request took.
        diagnostic_headers: Names of the response headers worth logging: ones that say
            who answered and why, without carrying secrets.

    Returns:
        Fields for the ``http_response`` log event.
    """
    event: dict[str, object] = {
        "method": response.request.method,
        "path": response.url.path,
        "status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "bytes": len(response.content),
        "elapsed_ms": round(elapsed_seconds * 1000),
    }
    location = response.headers.get("location")
    if location:
        event["location"] = without_query(response.url.join(location))
    set_cookies = [value.partition("=")[0] for value in response.headers.get_list("set-cookie")]
    if set_cookies:
        event["set_cookie_names"] = set_cookies
    for name in diagnostic_headers:
        if name in response.headers:
            event[name.replace("-", "_")] = response.headers[name]
    return event


class HttpAdapter:
    """``ApiClient`` machinery for one connection, to be subclassed per kind of server.

    The client is stateless between requests: cookies the server sets are
    discarded, so every request authenticates with its credential headers alone.

    Attributes:
        diagnostic_headers: Response headers logged at debug level, by name.
    """

    diagnostic_headers: ClassVar[tuple[str, ...]] = ("server", "via", "www-authenticate")

    def __init__(
        self,
        *,
        base_url: str,
        credentials: HeaderProvider,
        ca_bundle: Path | None = None,
        cookies: Mapping[str, str] | None = None,
        dry_run: bool = True,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Create the client.

        Args:
            base_url: Scheme and host of the server, plus any prefix shared by its products.
            credentials: Supplies the authentication headers, asked before every request.
            ca_bundle: PEM file of trusted roots, or None for the system default.
            cookies: Extra cookies to send with every request, by name.
            dry_run: When True, writes are described but never sent.
            timeout_seconds: Per-request timeout.
            transport: Replacement transport, used by tests.

        Raises:
            ConfigError: If the CA bundle cannot be loaded.
        """
        self.base_url = base_url.rstrip("/")
        self.dry_run = dry_run
        self.username: str | None = None
        self.cookie_names: tuple[str, ...] = tuple(cookies or {})
        self._credentials = credentials
        self._cookie_header = "; ".join(
            f"{name}={value}" for name, value in (cookies or {}).items()
        )
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Accept": "application/json"},
            timeout=timeout_seconds,
            follow_redirects=False,
            verify=build_ssl_context(ca_bundle) if transport is None else True,
            transport=transport,
        )

    @classmethod
    def from_connection(
        cls,
        connection: Connection,
        credentials: HeaderProvider,
        *,
        dry_run: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> Self:
        """Create a client for one product's resolved connection.

        Args:
            connection: Where and how to reach the product.
            credentials: Supplies the authentication headers.
            dry_run: When True, writes are described but never sent.
            transport: Replacement transport, used by tests.

        Returns:
            A configured client.
        """
        return cls(
            base_url=connection.base_url,
            credentials=credentials,
            ca_bundle=connection.ca_bundle,
            cookies=connection.cookies,
            dry_run=dry_run,
            timeout_seconds=connection.timeout_seconds,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        """Enter the async context.

        Returns:
            This client.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the client on leaving the async context.

        Args:
            exc_type: Exception type, if one was raised.
            exc: Exception instance, if one was raised.
            traceback: Traceback, if an exception was raised.
        """
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying connection pool."""
        await self._http.aclose()

    async def get(self, path: str, params: dict[str, str | int] | None = None) -> JsonValue:
        """Send a read request and return the parsed JSON body.

        Args:
            path: Request path relative to the base URL.
            params: Optional query parameters.

        Returns:
            The decoded JSON body, or None for an empty response.

        Raises:
            AgentToolsError: For any connectivity, auth, or API failure.
        """
        return await self._request("GET", path, params=params)

    async def get_text(self, path: str, params: dict[str, str | int] | None = None) -> str:
        """Send a read request for a plain-text resource and return its body.

        Args:
            path: Request path relative to the base URL.
            params: Optional query parameters.

        Returns:
            The response body as text; empty for an empty response.

        Raises:
            AgentToolsError: For any connectivity, auth, or API failure.
        """
        body = await self._request("GET", path, params=params, as_text=True)
        return body if isinstance(body, str) else ""

    async def query(self, path: str, payload: dict[str, JsonValue]) -> JsonValue:
        """Send a read that the server only accepts as a POST with a JSON body.

        Not gated by dry-run: only use it for endpoints that change nothing.

        Args:
            path: Request path relative to the base URL.
            payload: JSON body describing what to read.

        Returns:
            The decoded JSON body, or None for an empty response.

        Raises:
            AgentToolsError: For any connectivity, auth, or API failure.
        """
        return await self._request("POST", path, payload=payload)

    async def write(self, method: str, path: str, payload: dict[str, JsonValue]) -> WriteOutcome:
        """Send a write request, unless dry-run is on.

        Args:
            method: HTTP method, such as ``"POST"``.
            path: Request path relative to the base URL.
            payload: JSON body to send.

        Returns:
            The outcome, with ``sent=False`` when dry-run blocked the request.

        Raises:
            AgentToolsError: For any connectivity, auth, or API failure.
        """
        if self.dry_run:
            _log.info("write_blocked_by_dry_run", method=method, path=path)
            return WriteOutcome(sent=False, method=method, path=path, payload=payload)
        response = await self._request(method, path, payload=payload)
        return WriteOutcome(sent=True, method=method, path=path, payload=payload, response=response)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        payload: dict[str, JsonValue] | None = None,
        as_text: bool = False,
    ) -> JsonValue:
        """Send one request and interpret the response.

        Args:
            method: HTTP method.
            path: Request path relative to the base URL.
            params: Optional query parameters.
            payload: Optional JSON body.
            as_text: Ask for plain text and return the body undecoded.

        Returns:
            The decoded JSON body (or the text body), or None for an empty response.

        Raises:
            AgentToolsError: For any connectivity, auth, or API failure.
        """
        extra: dict[str, str] = dict(await self._credentials())
        cookies = [value for value in (extra.get("Cookie"), self._cookie_header) if value]
        if cookies:
            extra["Cookie"] = "; ".join(cookies)
        extra.update(self._request_headers(method))
        if as_text:
            extra["Accept"] = "text/plain"
        _log.debug(
            "http_request",
            method=method,
            url=f"{self.base_url}{path}",
            param_names=sorted(params or {}),
            cookie_names=list(self.cookie_names),
            has_body=payload is not None,
        )
        started = time.perf_counter()
        try:
            response = await self._http.request(
                method, path, params=params, json=payload, headers=extra
            )
        except httpx.TimeoutException as exc:
            _log.warning("http_timeout", method=method, path=path, error=type(exc).__name__)
            raise ConnectivityError(
                f"Timed out reaching {self.base_url}. Check your network or VPN connection."
            ) from exc
        except httpx.TransportError as exc:
            _log.warning("http_transport_error", method=method, path=path, error=repr(exc))
            if is_certificate_error(exc):
                raise ConnectivityError(
                    "The server's TLS certificate is not trusted. If its CA is not in the "
                    "system trust store, set ca_bundle in the config to a PEM file that "
                    "contains it."
                ) from exc
            raise ConnectivityError(
                f"Could not connect to {self.base_url}. Check your network or VPN connection."
            ) from exc
        finally:
            self._http.cookies.clear()
        elapsed = time.perf_counter() - started
        _log.debug("http_response", **response_event(response, elapsed, self.diagnostic_headers))
        self._observe(response)
        return self._interpret(response, as_text=as_text)

    def _request_headers(self, method: str) -> dict[str, str]:
        """Name the headers this kind of server wants on a request, beyond authentication.

        Args:
            method: HTTP method of the request.

        Returns:
            Extra headers; none by default.
        """
        del method
        return {}

    def _observe(self, response: httpx.Response) -> None:
        """Note anything a response says about the connection, such as who is signed in.

        Called for every response, before it is interpreted. Does nothing by default.

        Args:
            response: The HTTP response.
        """

    def _interpret(self, response: httpx.Response, *, as_text: bool = False) -> JsonValue:
        """Turn a response into data or a plain-language error.

        Args:
            response: The HTTP response.
            as_text: Return the body as text instead of decoding JSON.

        Returns:
            The decoded JSON body (or the text body), or None for an empty response.

        Raises:
            AgentToolsError: When the response is not the data that was asked for.
        """
        raise NotImplementedError
