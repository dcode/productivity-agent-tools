"""The ``ApiClient`` adapter for the Outlook REST API v2.0.

Adds to the shared ``HttpAdapter`` how Outlook REST reports results and
failures. Unlike the Atlassian and ServiceNow adapters there is no consent
banner, no cookie session and no sign-in redirect to interpret: this is a
public-cloud host reached with a bearer access token that the
``MicrosoftCredential`` keeps fresh. A 401 therefore means the token the
credential minted was not accepted, which for this stopgap almost always means
the stored refresh token can no longer be redeemed, so the message says to
sign in again.

Two ``Prefer`` headers are sent on every request so the data that reaches the
agent is small and unambiguous: bodies come back as plain text rather than
HTML, and event times are normalized to UTC rather than the mailbox's zone.
"""

import httpx
from pydantic import JsonValue

from productivity_agent_tools.errors import (
    ApiError,
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
)
from productivity_agent_tools.microsoft.oauth import RESEED_ADVICE
from productivity_agent_tools.transport import HttpAdapter, without_query

# Ask Outlook REST for plain-text bodies (smaller, no markup for the model to wade
# through) and for event start/end times normalized to UTC rather than the mailbox
# time zone. Several preferences go in one comma-separated header.
_PREFER: str = 'outlook.body-content-type="text", outlook.timezone="UTC"'


def _error_detail(response: httpx.Response) -> str:
    """Extract Outlook REST's ``error.code`` and ``error.message`` from a response.

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
    message = error.get("message")
    code = error.get("code")
    if isinstance(message, str) and message:
        return message
    return code if isinstance(code, str) else ""


class OutlookClient(HttpAdapter):
    """``ApiClient`` adapter for the Outlook REST API v2.0, authenticated by a bearer token."""

    diagnostic_headers = (*HttpAdapter.diagnostic_headers, "request-id", "x-ms-diagnostics")

    def _request_headers(self, method: str) -> dict[str, str]:
        """Ask Outlook for plain-text bodies and UTC times on every request.

        Args:
            method: HTTP method of the request (unused; the preference applies to all).

        Returns:
            The ``Prefer`` header.
        """
        del method
        return {"Prefer": _PREFER}

    def _interpret(self, response: httpx.Response, *, as_text: bool = False) -> JsonValue:
        """Turn a response into data or a plain-language error.

        Args:
            response: The HTTP response.
            as_text: Return the body as text instead of decoding JSON.

        Returns:
            The decoded JSON body (or the text body), or None for an empty response.

        Raises:
            AgentToolsError: When the token was not accepted, or the response is an error
                status or not the JSON that was asked for.
        """
        content_type = response.headers.get("content-type", "").lower()
        status = response.status_code
        detail = _error_detail(response) if status >= 400 else ""
        suffix = f" ({detail})" if detail else ""
        if status == 401:
            raise AuthenticationError(
                "Outlook rejected the access token (HTTP 401). The stored sign-in can probably "
                f"no longer be renewed. {RESEED_ADVICE}{suffix}"
            )
        if status in (301, 302, 303, 307, 308):
            target = without_query(response.url.join(response.headers.get("location", "")))
            raise ApiError(status, f"Outlook redirected the API call to {target} (HTTP {status}).")
        if status == 403:
            raise PermissionDeniedError(
                f"Outlook refused this request (HTTP 403). The token may lack the scope for "
                f"it, or the item may be out of reach.{suffix}"
            )
        if status == 404:
            raise NotFoundError(
                f"Not found, or not visible to your account: {response.url.path}.{suffix}"
            )
        if status == 429:
            raise ApiError(
                status,
                "Outlook is rate-limiting these requests (HTTP 429). Wait before retrying, "
                f"and do not loop.{suffix}",
            )
        if status >= 400:
            raise ApiError(status, f"Outlook returned HTTP {status}.{suffix}")
        if not response.content:
            return None
        if as_text:
            return response.text
        if "json" not in content_type:
            raise ApiError(
                status, f"Expected JSON but Outlook returned '{content_type or 'unknown'}'."
            )
        body: JsonValue = response.json()
        return body
