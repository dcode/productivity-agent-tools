"""Regression tests: MCPServer's tools/list_changed on handshake-era (2025-11-25) connections.

Both fail on mcp 2.3.0.
"""

import anyio
import mcp_types as types
import pytest
from mcp.client.client import Client
from mcp.server.mcpserver import Context, MCPServer

pytestmark = pytest.mark.anyio


def _server() -> MCPServer:
    server = MCPServer("listchanged-handshake")

    def late_tool() -> str:
        """Registered at runtime."""
        return "late"

    @server.tool()
    async def enable(ctx: Context) -> str:
        """Register late_tool and announce it with the public Context API."""
        server.add_tool(late_tool)
        await ctx.notify_tools_changed()
        return "enabled"

    return server


async def test_handshake_client_is_told_tools_list_changed_is_supported() -> None:
    """A server that can change its tools says so to a 2025-11-25 client."""
    async with Client(_server(), mode="legacy") as client:
        assert client.protocol_version == "2025-11-25"
        tools = client.server_capabilities.tools
        assert tools is not None and tools.list_changed is True  # fails: False


async def test_notify_tools_changed_reaches_handshake_client() -> None:
    """ctx.notify_tools_changed() sends tools/list_changed to a 2025-11-25 client."""
    received = anyio.Event()

    async def on_message(message: object) -> None:
        if isinstance(message, types.ToolListChangedNotification):
            received.set()

    async with Client(_server(), mode="legacy", message_handler=on_message) as client:
        await client.call_tool("enable", {})
        with anyio.move_on_after(1):
            await received.wait()
        assert received.is_set(), "notify_tools_changed() sent nothing"  # fails
        assert "late_tool" in {tool.name for tool in (await client.list_tools()).tools}
