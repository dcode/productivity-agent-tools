#!/usr/bin/env python3
"""Goose dynamic-tools probe (throwaway spike, not product code).

Answers the questions the toolset design depends on (see "Context budget" in
docs/ROADMAP.md), before any product code changes:

1. Which protocol version does Goose negotiate with a stdio server, and does it
   declare anything about list-changed notifications?
2. When the server adds a tool mid-session and sends
   ``notifications/tools/list_changed``, does Goose fetch ``tools/list`` again,
   and can the model call the new tool **in the same session**, without being
   told to reload anything?
3. Same question for a tool being removed.
4. Does Goose find and load a skill served as a ``skill://`` resource, and does
   that skill steer the model through the enable-then-call flow on its own?

First run (2026-11-14, Goose 1.57.0): Goose negotiated 2026-07-28, never
opened ``subscriptions/listen``, so the notification never arrived and
``tools/list`` was fetched once per session. Goose's own source handles
``notifications/tools/list_changed`` on the 2025-11-25 handshake (it bumps a
version that invalidates its cached tool list), and rmcp falls back to that
handshake when ``server/discover`` is answered with an ordinary JSON-RPC
error. So this version adds ``--protocol handshake``, which serves the SDK's
handshake-era loop only (the SDK's own entry point locks a connection to
2026-07-28 when the first request is ``server/discover``, and then refuses the
fallback ``initialize``: the first try of this flag failed that way). It is the
default; ``--protocol auto`` restores the 2026-07-28 behaviour of run 1.

It touches no product and no network. It starts with three tools:
``probe_status``, ``probe_enable_more`` and ``probe_disable_more``.
``probe_enable_more`` adds ``probe_secret_word``, whose answer is random per run
and is written nowhere the model could read it, and notifies the client;
``probe_disable_more`` removes it again. On the 2026-07-28 protocol the
notification goes out over ``subscriptions/listen``; on older ones it is sent
on the session itself, and ``tools.listChanged`` is advertised (the SDK leaves
it off on older protocols unless asked).

Every request that matters is written as one JSON line to a log file
(default ``~/.cache/productivity-agent-tools/list_changed_probe.log``, or
``--log``), and to stderr, which Goose keeps in its own logs. The log holds
protocol versions, client name and version, capability names, tool names and
timestamps, and each call to ``probe_secret_word`` (not the word): nothing
from your work.

Setup: add this to ``~/.config/goose/config.yaml`` under ``extensions:``,
with the path to your checkout, then restart Goose::

    list-changed-probe:
      name: list-changed-probe
      type: stdio
      cmd: uv
      args: [run, --project, /path/to/productivity-agent-tools, python,
             /path/to/productivity-agent-tools/scripts/list_changed_probe.py,
             --protocol, handshake]
      enabled: true
      timeout: 300

Each test runs in a **new** Goose session, and each prompt is typed as is. No
test mentions reloading:

A. ``Call probe_status and show me its result.``
   Passes if ``protocol_version`` is ``2025-11-25``.
B. ``Call probe_enable_more, then call probe_secret_word and tell me the word.``
   Passes only if the log shows a ``probe_secret_word called`` event; the word
   itself proves nothing. If the tool is unknown, send a second message: ``Try
   probe_secret_word again.`` Passing then means Goose refreshes between
   turns, not within one.
C. Continue B's session: ``Call probe_disable_more, then list the probe tools
   you can call.`` Passes if ``probe_secret_word`` is no longer offered.
D. ``Read the skill resource the list-changed-probe extension offers, then
   follow it.`` Goose discovers skills only from folders (``~/.agents/skills``,
   ``<project>/.agents/skills``), so this checks the generic resource tools
   instead. Passes if it reads ``skill://list-changed-probe/SKILL.md`` and the
   log shows ``probe_secret_word called``.

Optional comparison: change the config's last two arguments to ``--protocol,
auto``, restart Goose, and run A and B again; B should fail as in run 1.

Then send the log file (``--log`` path) and a pass or fail for each test. Run
``uv run python scripts/list_changed_probe.py --show-log`` to print it.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.resources import TextResource
from mcp.server.runner import _serve_legacy_stream  # private; a throwaway probe
from mcp.server.stdio import stdio_server
from mcp_types import Tool as MCPTool
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

DEFAULT_LOG = Path.home() / ".cache" / "productivity-agent-tools" / "list_changed_probe.log"
# Random per run and never logged, so the model can only learn it by calling the tool.
SECRET_WORD = (
    f"{secrets.choice(['amber', 'cobalt', 'fennel', 'juniper'])}-{secrets.randbelow(900) + 100}"
)
SECRET_TOOL = "probe_secret_word"
SKILL_URI = "skill://list-changed-probe/SKILL.md"
SKILL_MD = f"""\
---
name: list-changed-probe
description: Test skill for the list-changed probe. Use it when asked to follow the
  list-changed-probe skill, or to find the probe's secret word.
---

# Finding the probe's secret word

The secret word comes from a tool that is not loaded until you ask for it.

1. Call `probe_enable_more`. It adds the tool `{SECRET_TOOL}`.
2. Call `{SECRET_TOOL}` and report the word it returns.
3. Call `probe_status` and report how many times the tool list was fetched.
"""


class ProbeLog:
    """Appends one JSON object per event to a file and to stderr."""

    def __init__(self, path: Path) -> None:
        """Open the log at ``path``, creating its folder."""
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, **fields: object) -> None:
        """Append one event with its fields."""
        record = {"at": datetime.now(UTC).isoformat(timespec="milliseconds"), "event": event}
        line = json.dumps(record | fields, default=str)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        print(line, file=sys.stderr, flush=True)


class ProbeServer(MCPServer):
    """An MCPServer that logs every tools/list and advertises tools.listChanged."""

    def __init__(self, log: ProbeLog, *, handshake_only: bool) -> None:
        """Create the server, logging to ``log``.

        With ``handshake_only``, the connection is served in the 2025 handshake
        era only: a 2026-07-28 ``server/discover`` probe is refused (-32600), so
        a client such as rmcp falls back to the initialize handshake on the same
        connection.
        """
        super().__init__(
            "list-changed-probe",
            instructions="A test server. Use its tools only when the user asks about the probe.",
        )
        self.handshake_only = handshake_only
        self.log = log
        self.list_calls = 0
        self.notifications = 0

    async def list_tools(self) -> list[MCPTool]:
        """List the tools, logging the call and the names."""
        tools = await super().list_tools()
        self.list_calls += 1
        self.log.write("tools/list", call=self.list_calls, tools=sorted(t.name for t in tools))
        return tools

    async def run_stdio_async(self) -> None:
        """Serve over stdio, advertising tools.listChanged on every protocol version."""
        options = self._lowlevel_server.create_initialization_options(
            NotificationOptions(tools_changed=True)
        )
        self.log.write(
            "start", tools_list_changed_advertised=True, handshake_only=self.handshake_only
        )
        lowlevel = self._lowlevel_server
        async with stdio_server() as (read_stream, write_stream):
            if not self.handshake_only:
                await lowlevel.run(read_stream, write_stream, options)
                return
            # The SDK's own entry point picks the era from the first request and locks
            # it, so an enveloped server/discover would refuse the initialize fallback.
            async with lowlevel.lifespan(lowlevel) as lifespan_state:
                await _serve_legacy_stream(
                    lowlevel,
                    read_stream,
                    write_stream,
                    lifespan_state=lifespan_state,
                    session_id=None,
                    init_options=options,
                    raise_exceptions=False,
                )

    def has_tool(self, name: str) -> bool:
        """Whether a tool named ``name`` is registered."""
        return self._tool_manager.get_tool(name) is not None

    async def announce(self, ctx: Context, change: str) -> str:
        """Tell the client its tool list changed, by the route its protocol uses."""
        session = ctx.request_context.session
        modern = session.protocol_version in MODERN_PROTOCOL_VERSIONS
        if modern:
            await ctx.notify_tools_changed()
        else:
            await session.send_tool_list_changed()
        self.notifications += 1
        route = "subscriptions/listen bus" if modern else "session notification"
        self.log.write(
            "list_changed sent",
            change=change,
            route=route,
            protocol=session.protocol_version,
            count=self.notifications,
        )
        return route


def build(log: ProbeLog, *, handshake_only: bool) -> ProbeServer:
    """Create the probe server with its starting tools and its skill resource."""
    server = ProbeServer(log, handshake_only=handshake_only)

    def secret_word() -> str:
        """Return the probe's secret word. Only exists after probe_enable_more."""
        log.write("probe_secret_word called")
        return SECRET_WORD

    @server.tool()
    async def probe_status(ctx: Context) -> dict[str, Any]:
        """Report what the probe knows about this client and session."""
        session = ctx.request_context.session
        params = session.client_params
        caps = session.client_capabilities
        status = {
            "protocol_version": session.protocol_version,
            "client": None if params is None else params.client_info.model_dump(exclude_none=True),
            "client_capabilities": None
            if caps is None
            else sorted(caps.model_dump(exclude_none=True)),
            "tools_list_calls": server.list_calls,
            "list_changed_sent": server.notifications,
            "secret_tool_registered": server.has_tool(SECRET_TOOL),
        }
        log.write("probe_status", **status)
        return status

    @server.tool()
    async def probe_enable_more(ctx: Context) -> str:
        """Add the probe_secret_word tool to this server and notify the client."""
        if server.has_tool(SECRET_TOOL):
            return f"{SECRET_TOOL} is already available. Call it now."
        server.add_tool(secret_word, name=SECRET_TOOL)
        route = await server.announce(ctx, "added")
        return f"Added {SECRET_TOOL} and notified the client ({route}). Call {SECRET_TOOL} now."

    @server.tool()
    async def probe_disable_more(ctx: Context) -> str:
        """Remove the probe_secret_word tool from this server and notify the client."""
        if not server.has_tool(SECRET_TOOL):
            return f"{SECRET_TOOL} is not registered."
        server.remove_tool(SECRET_TOOL)
        route = await server.announce(ctx, "removed")
        return f"Removed {SECRET_TOOL} and notified the client ({route})."

    server.add_resource(
        TextResource(uri=SKILL_URI, name="SKILL.md", mime_type="text/markdown", text=SKILL_MD)
    )
    return server


def main() -> None:
    """Parse the arguments, then serve or print the log."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG, help="JSON-lines log file")
    parser.add_argument("--show-log", action="store_true", help="print the log and exit")
    parser.add_argument(
        "--protocol",
        choices=("auto", "handshake"),
        default="handshake",
        help="handshake: refuse server/discover so the client uses 2025-11-25",
    )
    args = parser.parse_args()
    log_path: Path = args.log.expanduser()
    if args.show_log:
        print(
            log_path.read_text(encoding="utf-8") if log_path.exists() else f"No log at {log_path}"
        )
        return
    log = ProbeLog(log_path)
    build(log, handshake_only=args.protocol == "handshake").run("stdio")


if __name__ == "__main__":
    main()
