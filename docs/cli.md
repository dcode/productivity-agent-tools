# Command line

Every command takes `--config PATH` (default
`~/.config/productivity-agent-tools/config.toml`) and `--help`. Run them with
`uv run productivity-agent-tools <command>` from the checkout.

| Command | What it does |
|---|---|
| [`serve`](#serve) | Run the MCP server over stdio; what your agent starts |
| [`doctor`](#doctor) | Read-only checks of the config and every configured product |
| [`call`](#call) | Run one tool directly, without an agent |
| [`set-token`](#set-token) | Store a token in the system keychain |
| [`login`](#login) | Sign in to ServiceNow or Microsoft through the browser |
| [`configure-agent`](#configure-agent) | Register the server with your agent |
| [`export-ca`](#export-ca) | Export the macOS Keychain's trusted roots to a PEM file |

## Options shared by `serve`, `doctor` and `call`

| Option | Effect |
|---|---|
| `--dry-run` / `--no-dry-run` | Block writes, or allow them, for this run; overrides `dry_run` |
| `--cookie name=value` | Extra cookie for every request; repeatable; adds to and overrides the config's |
| `--token-env-file PATH` | Env file of token lines such as `JIRA_TOKEN=...`; overrides `token_env_file` at every level |
| `--log-level LEVEL` | `debug`, `info`, `warning` or `error`; overrides `log_level` |

## `serve`

Runs the MCP server over stdio. Your agent starts this; you rarely run it by
hand. Its logs go to stderr, which the agent usually saves to a log file.

## `doctor`

```bash
uv run productivity-agent-tools doctor
```

Checks the config, then each configured product in turn: where its credential
came from, that the server answers, and one read (your own account, a search).
Each line is `[ok]`, `[info]` or `[fail]`, and the command exits non-zero if
any check failed. It never writes. Add `--log-level debug` to see each request.

## `call`

```bash
uv run productivity-agent-tools call
uv run productivity-agent-tools call jira_search "jql=assignee = currentUser()" max_results=5
```

Runs one tool directly and prints its result as JSON, with the same config,
credentials and dry-run setting as the server and no model credits spent. With
no tool name it lists every tool and its parameters; names in square brackets
are optional.

Arguments are written `name=value`; quote any that contain spaces. A value for a
text parameter is used as written; anything else is read as JSON, so
`max_results=5` is a number and `unread_only=true` a boolean. Write tools still
obey `dry_run`, so add `--no-dry-run` to send a change for real.

`--field NAME` prints one field of the result on its own. Text comes out as
written instead of as JSON, so a diff reads as a diff, and in a terminal it is
coloured (piped output stays plain):

```bash
uv run productivity-agent-tools call bitbucket_get_pull_request_diff \
  repository=my-service pull_request_id=42 --field diff
```

## `set-token`

```bash
uv run productivity-agent-tools set-token            # choose from a list
uv run productivity-agent-tools set-token bitbucket
```

Stores a token, typed at a hidden prompt, in the system keychain. With no name
it lists the tokens it can store and asks which one (by number or name);
without a terminal it prints the list and stops. See
[Authentication](authentication.md#set-token).

`set-token microsoft` stores an Outlook refresh token captured by hand, for when
`login microsoft` cannot run. It takes `--from-response FILE` (the token response
saved from the browser; its `refresh_token` is used) and `--no-verify` (store
without redeeming it once first). The older `login-microsoft` does the same and
is deprecated.

ServiceNow has no token to store: use `login servicenow`.

## `login`

```bash
uv run productivity-agent-tools login servicenow
uv run productivity-agent-tools login microsoft
```

Signs in through the browser and stores the result in the system keychain.
`login microsoft` (also accepted as `login outlook_mail` or
`login outlook_calendar`) signs in once for both Outlook products. See
[Authentication](authentication.md#browser-sign-in).

## `configure-agent`

Registers the server with your agent; see [Agents](agents.md).

## `budget`

```bash
uv run productivity-agent-tools budget --tools
```

Shows what the products in your config cost the model: per product, the bytes
of tool names, descriptions and input schemas plus the server's instructions
that are sent on every turn, an estimate in tokens (bytes divided by four), and
the bytes of output schemas, which reach the model only if the agent forwards
them (Goose does not, except through code mode's `get_function_details`).
`--tools` adds each product's [toolsets](configuration.md#toolsets) with their
tools, largest first; `--all-toolsets` measures the toolsets your config leaves
off too. It builds the server without connecting to anything, so it needs no
token and no network. Takes `--config`.

## `export-ca`

```bash
uv run productivity-agent-tools export-ca ~/ca-bundle.pem
```

macOS only. Writes the Keychain's trusted root certificates to a PEM file
(`--output`, or the path as the only argument; default `~/ca-bundle.pem`), for
`ca_bundle` in the config. You rarely need it: TLS uses the system trust store by
default.
