Confirmed on goose 1.57.0 (macOS, stdio) against a Python SDK 2.3.0 server:

- Goose negotiates 2026-07-28.
- `tools/list` is fetched once per session.
- A tool the server adds mid-session stays "Unknown tool" across turns.

When the server refuses the 2026 era and Goose falls back to 2025-11-25, the same flow works within one turn: the new tool is listed, approved and called, and a removal is picked up on the next listing. So the cache invalidation from #10925 is fine; only the 2026-07-28 subscription is missing.

rmcp already provides `Peer::listen()` (modelcontextprotocol/rust-sdk#1402 points the `on_*_list_changed` callbacks to it). Opening one stream with `toolsListChanged: true` after a 2026-07-28 connect, when the server advertises `tools.listChanged`, should close this.

On the server side, Python SDK servers have a matching gap on the 2025-11-25 path: modelcontextprotocol/python-sdk#3862.

