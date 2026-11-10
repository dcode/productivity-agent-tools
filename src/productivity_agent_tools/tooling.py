"""Pieces shared by every product's MCP tool definitions."""

import functools
from collections.abc import Awaitable, Callable

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from productivity_agent_tools.errors import AtlassianError

READ = ToolAnnotations(read_only_hint=True, open_world_hint=True)
LOCAL_READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True
)


def anticipated[**P, R](tool: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Report this package's own errors to the model with their message intact.

    The MCP SDK hides the text of unexpected exceptions from the model. Errors
    raised on purpose here carry plain-language guidance (for example "check
    your network or VPN connection"), so they are re-raised as the SDK's anticipated
    ``ToolError``, which is passed through.

    Args:
        tool: The async tool function to wrap.

    Returns:
        The wrapped tool, with the same signature.
    """

    @functools.wraps(tool)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await tool(*args, **kwargs)
        except AtlassianError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper
