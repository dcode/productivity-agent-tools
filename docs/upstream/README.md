# Upstream reports

Issue text and reproductions for bugs found in other projects while building this one.
Track where each stands under "Upstream" in [ROADMAP.md](../ROADMAP.md).

## Tool-list changes not reaching Goose (2026-11-14)

Found with `scripts/list_changed_probe.py`. **All three posted** (checked 2026-11-24): the
new issue is modelcontextprotocol/python-sdk#3862, and both comments are up. The steps
below are kept as the record of how they were posted.

- `python-sdk-handshake-list-changed.md`: **new issue** for modelcontextprotocol/python-sdk.
  Its reproduction is `test_notify_tools_changed_handshake.py`: both tests fail on mcp 2.3.0,
  and pass when `ctx.notify_tools_changed()` is swapped for
  `ctx.request_context.session.send_tool_list_changed()` (the second test; the first
  needs `NotificationOptions`).
- `python-sdk-3851-comment.md`: comment on modelcontextprotocol/python-sdk#3851.
- `goose-13055-comment.md`: comment on aaif-goose/goose#13055.

Post the issue first, then put its number into both comments, then post them, from the
repository root:

```bash
title="MCPServer: ctx.notify_tools_changed() is a silent no-op on handshake-era"
title+=" (2025-11-25) connections, and tools.listChanged is advertised as false"
url=$(gh issue create --repo modelcontextprotocol/python-sdk --title "$title" \
  --body-file docs/upstream/python-sdk-handshake-list-changed.md) && echo "$url"
```

```bash
perl -pi -e "s/#NEW/#${url##*/}/g" docs/upstream/python-sdk-3851-comment.md docs/upstream/goose-13055-comment.md
```

```bash
gh issue comment 3851 --repo modelcontextprotocol/python-sdk --body-file docs/upstream/python-sdk-3851-comment.md
```

```bash
gh issue comment 13055 --repo aaif-goose/goose --body-file docs/upstream/goose-13055-comment.md
```

Then record the new issue's number in ROADMAP.md.
