"""Shared async HTTP core used by every product module.

Handles the things that are the same for Jira, Bitbucket and Confluence on
one host: TLS trust, bearer-token authentication, any extra cookies a proxy
in front of the applications requires, dry-run for writes, and translating
failures into plain-language errors.
"""

import itertools
import ssl
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

import httpx
import structlog
import truststore
from pydantic import JsonValue

from productivity_agent_tools.config import Settings
from productivity_agent_tools.errors import (
    ApiError,
    AtlassianError,
    AuthenticationError,
    ConfigError,
    ConnectivityError,
    InterstitialPageError,
    NotFoundError,
    PermissionDeniedError,
)

_log = structlog.get_logger(__name__)

# Response headers that say who answered and why, without carrying secrets.
_DIAGNOSTIC_HEADERS: tuple[str, ...] = (
    "server",
    "via",
    "www-authenticate",
    "x-seraph-loginreason",
    "x-arequestid",
)


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


def _build_ssl_context(ca_bundle: Path | None) -> ssl.SSLContext:
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


def _is_certificate_error(exc: BaseException) -> bool:
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


def _without_query(url: httpx.URL) -> str:
    """Render a URL without its query string or fragment, which can carry sign-in state.

    Args:
        url: The URL to render.

    Returns:
        Scheme, host and path only.
    """
    return str(url.copy_with(query=None, fragment=None))


def _address_hint(url: httpx.URL) -> str:
    """Point out a request address that looks assembled wrongly from the config.

    A sub-path given in both ``base_url`` and a product path repeats a segment,
    and stray slashes in either leave an empty one. Both can be legitimate, so
    this is only offered alongside an error, as a question.

    Args:
        url: The address that was requested.

    Returns:
        A question to append to the error, or an empty string.
    """
    segments = url.path.split("/")[1:]
    shown = _without_query(url)
    fix = "If not, correct base_url or the product path (for example path under [jira])."
    for first, second in itertools.pairwise(segments):
        if first and first == second:
            return f"Did you mean to repeat '/{first}' in the address {shown}? {fix}"
    if "" in segments[:-1]:
        return f"Did you mean to have '//' in the address {shown}? {fix}"
    return ""


def _response_event(response: httpx.Response, elapsed_seconds: float) -> dict[str, object]:
    """Collect the loggable facts about a response.

    Bodies, cookie values and query strings are left out; cookies the server
    set are listed by name only.

    Args:
        response: The HTTP response.
        elapsed_seconds: Time the request took.

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
        event["location"] = _without_query(response.url.join(location))
    set_cookies = [value.partition("=")[0] for value in response.headers.get_list("set-cookie")]
    if set_cookies:
        event["set_cookie_names"] = set_cookies
    for name in _DIAGNOSTIC_HEADERS:
        if name in response.headers:
            event[name.replace("-", "_")] = response.headers[name]
    return event


def _error_detail(response: httpx.Response) -> str:
    """Extract Jira-style error messages from a response.

    Args:
        response: The failed response.

    Returns:
        A short description, or an empty string when none is available.
    """
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    parts: list[str] = [str(m) for m in body.get("errorMessages") or []]
    errors = body.get("errors")
    if isinstance(errors, dict):
        parts.extend(f"{name}: {message}" for name, message in errors.items())
    if not parts and isinstance(body.get("message"), str):
        parts.append(body["message"])
    return "; ".join(parts)


class AtlassianClient:
    """Async HTTP client for one Atlassian host.

    The client is stateless between requests: cookies the server sets are
    discarded, so every request authenticates with the bearer token alone.
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        ca_bundle: Path | None = None,
        cookies: Mapping[str, str] | None = None,
        dry_run: bool = True,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Create the client.

        Args:
            base_url: Scheme and host of the server, plus any prefix shared by its products.
            token: Personal access token sent as a bearer credential.
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
        self.cookie_names: tuple[str, ...] = tuple(cookies or {})
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if cookies:
            headers["Cookie"] = "; ".join(f"{name}={value}" for name, value in cookies.items())
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout_seconds,
            follow_redirects=False,
            verify=_build_ssl_context(ca_bundle) if transport is None else True,
            transport=transport,
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> Self:
        """Create a client from resolved settings.

        Args:
            settings: The resolved settings.
            token: Personal access token.
            transport: Replacement transport, used by tests.

        Returns:
            A configured client.
        """
        return cls(
            base_url=settings.base_url,
            token=token,
            ca_bundle=settings.ca_bundle,
            cookies=settings.cookies,
            dry_run=settings.dry_run,
            timeout_seconds=settings.timeout_seconds,
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
            AtlassianError: For any connectivity, auth, or API failure.
        """
        return await self._request("GET", path, params=params)

    async def write(self, method: str, path: str, payload: dict[str, JsonValue]) -> WriteOutcome:
        """Send a write request, unless dry-run is on.

        Args:
            method: HTTP method, such as ``"POST"``.
            path: Request path relative to the base URL.
            payload: JSON body to send.

        Returns:
            The outcome, with ``sent=False`` when dry-run blocked the request.

        Raises:
            AtlassianError: For any connectivity, auth, or API failure.
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
    ) -> JsonValue:
        """Send one request and interpret the response.

        Args:
            method: HTTP method.
            path: Request path relative to the base URL.
            params: Optional query parameters.
            payload: Optional JSON body.

        Returns:
            The decoded JSON body, or None for an empty response.

        Raises:
            AtlassianError: For any connectivity, auth, or API failure.
        """
        extra = {"X-Atlassian-Token": "no-check"} if method != "GET" else None
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
            if _is_certificate_error(exc):
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
        _log.debug("http_response", **_response_event(response, time.perf_counter() - started))
        try:
            return self._interpret(response)
        except AtlassianError as exc:
            hint = _address_hint(response.url)
            if hint:
                exc.args = (f"{exc} {hint}", *exc.args[1:])
            raise

    def _interpret(self, response: httpx.Response) -> JsonValue:
        """Turn a response into data or a plain-language error.

        Args:
            response: The HTTP response.

        Returns:
            The decoded JSON body, or None for an empty response.

        Raises:
            AtlassianError: When the response is a web page or an error status.
        """
        content_type = response.headers.get("content-type", "").lower()
        status = response.status_code
        if 200 <= status < 300 and content_type.startswith("text/html"):
            if self.cookie_names:
                sent = ", ".join(self.cookie_names)
                raise InterstitialPageError(
                    "The server returned a web page instead of API data, even though the "
                    f"configured cookies ({sent}) were sent. Check that their names and "
                    "values are still what the site expects."
                )
            raise InterstitialPageError(
                "The server returned a web page instead of API data. Something in front of "
                "it, such as a consent banner or a login page, may be intercepting requests. "
                "If a cookie gets you past it in a browser, add it under [cookies] in the "
                "config or pass --cookie name=value."
            )
        detail = _error_detail(response) if status >= 400 else ""
        suffix = f" ({detail})" if detail else ""
        if status == 401:
            raise AuthenticationError(
                "The server rejected the access token (HTTP 401). It may have expired or "
                f"been revoked; create a new one and run set-token.{suffix}"
            )
        if status == 403:
            raise PermissionDeniedError(f"You do not have permission for this action.{suffix}")
        if status == 404:
            raise NotFoundError(
                f"Not found, or not visible to your account: {response.url.path}.{suffix}"
            )
        if status >= 400:
            raise ApiError(status, f"The server returned HTTP {status}.{suffix}")
        if status in (301, 302, 303, 307, 308):
            raise ApiError(status, self._redirect_message(response))
        if status == 204 or not response.content:
            return None
        if "json" not in content_type:
            raise ApiError(
                status, f"Expected JSON but the server returned '{content_type or 'unknown'}'."
            )
        body: JsonValue = response.json()
        return body

    def _redirect_message(self, response: httpx.Response) -> str:
        """Describe a redirect so the user can tell what intercepted the call.

        The destination is named without its query string, which can carry
        sign-in state, and cookies are named without their values.

        Args:
            response: The redirect response.

        Returns:
            A plain-language description with the most likely fixes.
        """
        source = response.url
        location = response.headers.get("location", "")
        opening = (
            f"The server redirected the API call to {source.path} (HTTP {response.status_code})"
        )
        if not location:
            return f"{opening} without saying where to, which usually means a login page."
        target = source.join(location)
        shown = _without_query(target)
        if target.host != source.host:
            return (
                f"{opening} to {shown}, on a different host. If that is a sign-in service, "
                "whatever sits in front of the application is not accepting the access token "
                "for API calls. If it is the site's real address, set base_url to it."
            )
        if target.scheme == source.scheme and target.path.rstrip("/") == source.path.rstrip("/"):
            return (
                f"{opening} back to the same address, so something in front of it is waiting "
                f"for a cookie it did not get. {self._cookie_hint()}"
            )
        return (
            f"{opening} to {shown}. Open that address in a browser to see what it is. If it "
            f"is a consent or login page: {self._cookie_hint()} If it is the application "
            "under a different address, correct base_url or the product path (for example "
            "path under [jira]) in the config."
        )

    def _cookie_hint(self) -> str:
        """Say what to check about extra cookies, naming the ones that were sent.

        Returns:
            One sentence of advice.
        """
        if self.cookie_names:
            sent = ", ".join(self.cookie_names)
            return (
                f"The configured cookies ({sent}) were sent, so check their names and values "
                "against what a browser holds after you get past that page; more than one "
                "cookie may be required."
            )
        return (
            "If a cookie gets you past it in a browser, add it under [cookies] in the config "
            "or pass --cookie name=value."
        )
