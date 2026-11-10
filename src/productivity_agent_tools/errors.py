"""Exception types shared by every product module.

Each error carries a plain-language message that is safe to show to the agent
and the user. Messages never contain token values.
"""


class AtlassianError(Exception):
    """Base class for every error this package raises on purpose."""


class ConfigError(AtlassianError):
    """The configuration file or a command-line override is invalid."""


class TokenNotFoundError(AtlassianError):
    """No personal access token could be found in any configured source."""


class ConnectivityError(AtlassianError):
    """The server could not be reached (VPN down, DNS, TLS trust, timeout)."""


class InterstitialPageError(AtlassianError):
    """A web page came back where API data was expected.

    This usually means something in front of the application, such as a
    consent banner or a login page, intercepted the request.
    """


class AuthenticationError(AtlassianError):
    """The server rejected the token (HTTP 401)."""


class PermissionDeniedError(AtlassianError):
    """The authenticated user may not perform this action (HTTP 403)."""


class NotFoundError(AtlassianError):
    """The requested resource does not exist or is not visible (HTTP 404)."""


class ApiError(AtlassianError):
    """Any other unexpected response from the server.

    Attributes:
        status: HTTP status code of the response.
    """

    def __init__(self, status: int, message: str) -> None:
        """Create the error.

        Args:
            status: HTTP status code of the response.
            message: Plain-language description of what went wrong.
        """
        super().__init__(message)
        self.status = status
