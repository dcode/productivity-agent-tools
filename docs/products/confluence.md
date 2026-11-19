# Confluence

Confluence Data Center: reads and search, plus creating and editing pages.
Writes are blocked while `dry_run` is on.

## Setup

```toml
[confluence]
path = "/confluence"       # or its own base_url if it is on a different host
default_space = "~jdoe"    # optional: your personal space, or a team space key
```

Create a personal access token in Confluence (your profile, Settings, Personal
Access Tokens) and store it with `set-token confluence`.

| Key | Default | Meaning |
|---|---|---|
| `path` | `/confluence` | Sub-path Confluence is served under |
| `default_max_results` | `25` | Rows returned when a call names no limit |
| `default_space` | none | Space key used when a call names none |

## Tools

| Tool | Kind | What it does |
|---|---|---|
| `confluence_status` | read | Confluence version, the account, where the token came from, your personal space, dry-run |
| `confluence_list_spaces` | read | Spaces you can see, global or personal |
| `confluence_set_default_space` | session | The space other tools use when none is named, until restart |
| `confluence_search` | read | Pages and blog posts matching a CQL query |
| `confluence_get_page` | read | A page by id or exact title: body (storage format), version, ancestors, labels |
| `confluence_list_child_pages` | read | Pages directly under a page |
| `confluence_create_page` | write | Create a page from Markdown or storage format, optionally under a parent |
| `confluence_update_page` | write | Replace, append to, prepend to or find-and-replace in a page's body, or retitle or move it |

## Writing pages

Page bodies are written in Markdown and converted to Confluence's storage
format here, because Confluence 10 no longer converts wiki markup through its
API. Code blocks become Confluence code macros and images become Confluence
images. For macros, layouts or mentions, the agent can send storage format
directly (`body_format="storage"`).

An edit must name the version the agent read. If anyone changed the page since,
nothing is saved and the agent is told to read it again, so it cannot overwrite
someone else's change. To add a section, the agent can append to the page
(`mode="append"`) without sending the whole page back. To fill in a template or
change a few places on a page with layouts and macros, it passes
`replacements`: exact pieces of the page to find and what to put there.
Everything else stays byte for byte as it was. Each piece must occur exactly
once, and an edit that would leave broken markup is refused.

`confluence_status` shows your personal space key (`~` and your username), which
is where pages about you, such as an "About me" page, usually go.
