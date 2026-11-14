"""The ``ApiClient`` adapter for a ServiceNow instance.

Adds to the shared ``HttpAdapter`` how ServiceNow reports results and
failures. The one failure that matters most is an expired browser session,
which ServiceNow signals in several ways (HTTP 401, a redirect to the sign-in
service, or an answer served to a guest); each becomes the same instruction
to sign in again.
"""

import httpx
from pydantic import JsonValue

from productivity_agent_tools.errors import (
    ApiError,
    AuthenticationError,
    InterstitialPageError,
    NotFoundError,
    PermissionDeniedError,
)
from productivity_agent_tools.servicenow.session import SIGN_IN_ADVICE
from productivity_agent_tools.transport import HttpAdapter, without_query


def _error_detail(response: httpx.Response) -> str:
    """Extract ServiceNow's ``error.message`` and ``error.detail`` from a response.

    Args:
        response: The failed response.

    Returns:
        A short description, or an empty string when none is available.
    """
    try:
        body = response.json()
    except ValueError:
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, str):
        return error
    if not isinstance(error, dict):
        return ""
    parts = [error.get("message"), error.get("detail")]
    return ": ".join(part for part in parts if isinstance(part, str) and part)


class ServiceNowClient(HttpAdapter):
    """``ApiClient`` adapter for one ServiceNow instance, authenticated by a browser session."""

    diagnostic_headers = (*HttpAdapter.diagnostic_headers, "x-is-logged-in")

    def _interpret(self, response: httpx.Response, *, as_text: bool = False) -> JsonValue:
        """Turn a response into data or a plain-language error.

        Args:
            response: The HTTP response.
            as_text: Return the body as text instead of decoding JSON.

        Returns:
            The decoded JSON body (or the text body), or None for an empty response.

        Raises:
            AgentToolsError: When the session was not accepted, or the response is a web
                page or an error status.
        """
        content_type = response.headers.get("content-type", "").lower()
        status = response.status_code
        detail = _error_detail(response) if status >= 400 else ""
        suffix = f" ({detail})" if detail else ""
        if status == 401:
            raise AuthenticationError(
                "ServiceNow did not accept the saved browser session (HTTP 401); it has "
                f"probably expired. {SIGN_IN_ADVICE}{suffix}"
            )
        if status in (301, 302, 303, 307, 308):
            target = without_query(response.url.join(response.headers.get("location", "")))
            raise AuthenticationError(
                f"ServiceNow redirected the API call to {target} (HTTP {status}), which "
                f"usually means the browser session has expired. {SIGN_IN_ADVICE}"
            )
        if status < 400 and response.headers.get("x-is-logged-in", "").lower() == "false":
            # Some endpoints answer a guest with HTTP 200 and no data instead of refusing.
            raise AuthenticationError(
                f"ServiceNow answered as if nobody were signed in, so the browser session "
                f"has expired. {SIGN_IN_ADVICE}"
            )
        if status == 403:
            raise PermissionDeniedError(f"You do not have permission for this action.{suffix}")
        if status == 404:
            raise NotFoundError(
                f"Not found, or not visible to your account: {response.url.path}.{suffix}"
            )
        if status >= 400:
            raise ApiError(status, f"ServiceNow returned HTTP {status}.{suffix}")
        if not response.content:
            return None
        if as_text:
            return response.text
        if content_type.startswith("text/html"):
            raise InterstitialPageError(
                "ServiceNow returned a web page instead of API data. The browser session "
                f"may have expired. {SIGN_IN_ADVICE}"
            )
        if "json" not in content_type:
            raise ApiError(
                status, f"Expected JSON but ServiceNow returned '{content_type or 'unknown'}'."
            )
        body: JsonValue = response.json()
        return body
