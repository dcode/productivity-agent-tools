"""The ``ApiClient`` adapter for Atlassian Data Center products.

Adds to the shared ``HttpAdapter`` the things that are the same for Jira,
Bitbucket and Confluence: reading who is signed in, getting writes past the
cross-site request check, and translating their errors, redirects and
intercepting pages into plain language. One client serves one product's
connection, so products on different hosts each get their own.
"""

import itertools
from urllib.parse import unquote

import httpx
from pydantic import JsonValue

from productivity_agent_tools.errors import (
    AgentToolsError,
    ApiError,
    AuthenticationError,
    InterstitialPageError,
    NotFoundError,
    PermissionDeniedError,
)
from productivity_agent_tools.transport import HttpAdapter, without_query


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
    shown = without_query(url)
    fix = "If not, correct base_url or the product path (for example path under [jira])."
    for first, second in itertools.pairwise(segments):
        if first and first == second:
            return f"Did you mean to repeat '/{first}' in the address {shown}? {fix}"
    if "" in segments[:-1]:
        return f"Did you mean to have '//' in the address {shown}? {fix}"
    return ""


def _error_detail(response: httpx.Response) -> str:
    """Extract Jira- or Bitbucket-style error messages from a response.

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
    elif isinstance(errors, list):
        parts.extend(
            str(item["message"])
            for item in errors
            if isinstance(item, dict) and item.get("message")
        )
    if not parts and isinstance(body.get("message"), str):
        parts.append(body["message"])
    return "; ".join(parts)


class AtlassianClient(HttpAdapter):
    """``ApiClient`` adapter for one Atlassian Data Center connection."""

    diagnostic_headers = (
        *HttpAdapter.diagnostic_headers,
        "x-seraph-loginreason",
        "x-arequestid",
        "x-ausername",
    )

    def _request_headers(self, method: str) -> dict[str, str]:
        """Ask Atlassian's cross-site request check to let writes through.

        Args:
            method: HTTP method of the request.

        Returns:
            The header that does so, for anything but a GET.
        """
        return {} if method == "GET" else {"X-Atlassian-Token": "no-check"}

    def _observe(self, response: httpx.Response) -> None:
        """Remember who is signed in: Atlassian products say so on every authenticated response.

        Args:
            response: The HTTP response.
        """
        signed_in = unquote(response.headers.get("x-ausername", "")).strip()
        if signed_in and signed_in != "anonymous":
            self.username = signed_in

    def _interpret(self, response: httpx.Response, *, as_text: bool = False) -> JsonValue:
        """Turn a response into data or a plain-language error, questioning an odd address.

        Args:
            response: The HTTP response.
            as_text: Return the body as text instead of decoding JSON.

        Returns:
            The decoded JSON body (or the text body), or None for an empty response.

        Raises:
            AgentToolsError: When the response is a web page or an error status.
        """
        try:
            return self._read(response, as_text=as_text)
        except AgentToolsError as exc:
            hint = _address_hint(response.url)
            if hint:
                exc.args = (f"{exc} {hint}", *exc.args[1:])
            raise

    def _read(self, response: httpx.Response, *, as_text: bool = False) -> JsonValue:
        """Turn a response into data or a plain-language error.

        Args:
            response: The HTTP response.
            as_text: Return the body as text instead of decoding JSON.

        Returns:
            The decoded JSON body (or the text body), or None for an empty response.

        Raises:
            AgentToolsError: When the response is a web page or an error status.
        """
        content_type = response.headers.get("content-type", "").lower()
        status = response.status_code
        if 200 <= status < 300 and not response.content:
            # A successful write often has no body, and some servers still label
            # it text/html. Nothing came back, so it is not an intercepting page.
            return None
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
        if as_text:
            return response.text
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
        shown = without_query(target)
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
