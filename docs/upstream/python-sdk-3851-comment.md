A real client that hits this: Goose 1.57.0. Its rmcp `ClientLifecycleMode::Auto` sends an enveloped `server/discover` first. On any JSON-RPC error that isn't a modern rejection, it falls back to `initialize` on the same stdio process.

Our case is a server that deliberately stays in the handshake era, because Goose doesn't open `subscriptions/listen` (aaif-goose/goose#13055). We removed the `server/discover` handler. The probe got -32601, and the fallback `initialize` got -32022. Goose then dropped the extension at startup with "process quit before initialization".

Our workaround calls the private `mcp.server.runner._serve_legacy_stream` directly. That refuses the probe with -32600 and accepts the `initialize`, and Goose connects on 2025-11-25.

Beyond the fix proposed here, a public way to limit which eras a stdio server serves would make this supportable. Related: #3862 (handshake-era `notify_tools_changed()` is a no-op).

