# Configuration

The config is a TOML file at `~/.config/productivity-agent-tools/config.toml`
(`--config` on any command points elsewhere). Start from
[`config.example.toml`](https://github.com/dcode/productivity-agent-tools/blob/main/config.example.toml),
which lists every option with comments. Command-line flags, where a command has
them, win over the file.

## Turning products on

A product is switched on by having its table in the config: `[jira]`,
`[bitbucket]`, `[confluence]`, `[servicenow]`, `[outlook_mail]` or
`[outlook_calendar]`. Without its table a product's tools are not offered and
it needs no credential. An empty table turns a product on with its defaults, and
at least one product must be configured. A table naming no installed product is
an error, so a typo is caught.

## One host or several

The top-level `base_url`, `ca_bundle`, `timeout_seconds` and `[cookies]` are
defaults for every product, and each product's table can set any of them again
for itself. Both common layouts work:

```toml
# Everything behind one host, each product under its own path.
base_url = "https://atlassian.example.com"

[jira]
path = "/jira"
```

```toml
# Each product on a host of its own; no top-level base_url needed.
[jira]
base_url = "https://jira.example.com"
path = ""
```

`base_url` is the scheme and host, plus a path only when every product sits
behind a shared prefix. Each Atlassian product's own sub-path goes in its
`path` (`""` for the root of the host). Every `base_url` must start with
`https://`.

## Groups

When several products share a host but others do not (the Atlassian products
on one host, Outlook on another), put the shared values in a group and have the
products join it, so the top level stays neutral:

```toml
[group.atlassian]
base_url = "https://atlassian.example.com"
[group.atlassian.cookies]
consent_accepted = "1"

[jira]
group = "atlassian"
path = "/jira"

[bitbucket]
group = "atlassian"
path = "/bitbucket"
```

Each value resolves from the product's own table, then its group, then the top
level. A group holds the same values as the top level: `base_url`, `ca_bundle`,
`timeout_seconds`, cookies, `token_cmd` and `token_env_file`. A product that
names a group with no `[group.<name>]` table is an error.

## Extra cookies

Some organizations put a proxy in front of their applications that wants a
particular cookie on every request. Supply such cookies under `[cookies]` (or a
group's or product's own cookie table), or per run with `--cookie name=value`
(repeatable; it adds to the file's cookies and overrides any with the same
name). None are sent by default.

A common case is a **consent or terms-of-use banner**: the proxy answers every
request, API calls included, with a banner page until a cookie records that you
clicked through. If API calls come back as a web page, that is the likely
cause. Which cookie it is depends on the site: accept the banner in a browser,
then look in the developer tools for the cookie it set. Adding that cookie here
means you are accepting the banner through this tool.

A cookie table at any level **replaces** the inherited one rather than adding
to it, so a cookie meant for one host is never sent to another: the product's
table wins, then its group's, then the top level's. An empty table sends none.

Cookie values are never logged or shown to the agent; only their names are.

## Writes and `dry_run`

| Setting | Default | Effect |
|---|---|---|
| `dry_run` | `true` | Write tools return the request they *would* send and send nothing |

Set it in the config (`true`/`false` or `1`/`0`) or per run with `--dry-run` /
`--no-dry-run`. The Outlook and ServiceNow tools only read, so it does not
affect them.

A product's own table can set `dry_run` too, which wins over the top level for
that product only. Keep writes blocked everywhere but one product:

```toml
dry_run = true

[confluence]
dry_run = false
```

`--dry-run` and `--no-dry-run` on the command line override every table for that
run. `doctor` prints a product's setting when it differs from the top level.

## TLS

TLS is always verified. By default it uses the operating system's trust store,
so internal CAs your browser already trusts work without extra setup.
`ca_bundle` pins trust to a specific PEM file instead; on macOS,
`productivity-agent-tools export-ca` writes one from the Keychain.

## Paths and variables

Paths in the config (`ca_bundle`, `token_env_file`) may use `~`, `$HOME` or
`${HOME}`. A variable that is not set is an error naming the key, rather than a
confusing "file not found" later. See [Authentication](authentication.md) for
how `token_cmd` expands.

## Logging

Logs are structured ([structlog](https://www.structlog.org/)) and always go to
stderr, because the MCP protocol owns stdout. They are readable lines in a
terminal and one JSON object per line otherwise, such as in an agent host's log
file.

Set the level with `log_level` in the config or `--log-level` on `serve`,
`doctor`, `call` and `login`: `debug`, `info` (default), `warning` or `error`.

At `debug` every HTTP exchange is logged as an `http_request` and an
`http_response` event: method, path, status, content type, size, duration, the
redirect target if any, the names of cookies sent and set, and a few headers
that say who answered (`server`, `via`, `www-authenticate`,
`x-seraph-loginreason`). Tokens, cookie values, query-string values and request
and response bodies are never logged.

## Reference

Top level:

| Key | Default | Meaning |
|---|---|---|
| `base_url` | none | Default scheme and host for every product |
| `ca_bundle` | system trust store | PEM file of trusted roots |
| `dry_run` | `true` | Block writes |
| `timeout_seconds` | `30` | Per-request timeout |
| `log_level` | `info` | Least severe level logged |
| `token_cmd` | none | Command that prints a token; see [Authentication](authentication.md) |
| `token_env_file` | none | Env file of `<PRODUCT>_TOKEN=` lines; see [Authentication](authentication.md) |
| `[cookies]` | empty | Extra cookies for every request |
| `[group.<name>]` | none | Shared values for the products that join the group |

Every product table also accepts `base_url`, `ca_bundle`, `timeout_seconds`,
`token_cmd`, `token_env_file`, `group` and its own cookie table (such as
`[jira.cookies]`), and most accept `default_max_results` (25; at most 100 rows
are returned per call). Each product's own keys are on its page under
[Products](products/index.md).
