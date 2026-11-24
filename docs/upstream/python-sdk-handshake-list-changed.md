### Summary

On a connection opened with the `initialize` handshake (2025-11-25 and earlier), `MCPServer` has two problems:

1. It advertises `tools: {listChanged: false}`, even though `add_tool()` / `remove_tool()` change the list at runtime. `MCPServer.run()` gives no public way to pass `NotificationOptions(tools_changed=True)`.
2. `Context.notify_tools_changed()` publishes only to the `subscriptions/listen` bus. A handshake-era client gets nothing, and there is no error or warning. `notify_prompts_changed()` and `notify_resources_changed()` probably behave the same way (not tested).

The 2026-07-28 path works. So a server written against the documented API updates its tool list for modern clients and silently never does for handshake clients.

### Why it matters now

Goose 1.57.0 negotiates 2026-07-28 but never opens `subscriptions/listen` (aaif-goose/goose#13055). The only way to get a tool-list change to Goose today is the 2025-11-25 era, and this bug drops it there. The Goose side works: with the workaround below, Goose lists and calls a tool added mid-turn.

Workaround we use: `await ctx.request_context.session.send_tool_list_changed()`, plus serving with `create_initialization_options(NotificationOptions(tools_changed=True))`.

### Reproduction (mcp 2.3.0, Python 3.14)

Both tests fail. Replacing `ctx.notify_tools_changed()` with `ctx.request_context.session.send_tool_list_changed()` makes the second pass, which shows the client side is fine.

```python
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
```

### Expected

On handshake-era connections:

- Advertise `listChanged: true` for tools, or at least make it configurable from `MCPServer`.
- Have `notify_*_changed()` send `notifications/*/list_changed` on the session. That era has no subscription filter, and a server that declared `listChanged` may send these notifications.

### Related

- #1429: send the notification automatically on `add_tool()` / `remove_tool()`. Older than the era split.
- #1486: `NotificationOptions` for FastMCP (stale).
- #3851: the era is latched by the first request.
- aaif-goose/goose#13055
