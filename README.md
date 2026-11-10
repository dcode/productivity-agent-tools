# productivity-agent-tools

An MCP server that gives an AI agent (Goose first, Claude Code later) tools for
daily work against a **self-hosted Atlassian Data Center** deployment. It covers
Jira (read and write) and, when configured, Bitbucket pull requests (read-only).

## Quickstart

You need Python 3.14+, [uv](https://docs.astral.sh/uv/), a Jira personal access
token, and your VPN connected if Jira needs it.

1. Install:

   ```bash
   git clone git@github.com:dcode/productivity-agent-tools.git && cd productivity-agent-tools
   uv sync
   ```

2. Create the config, then edit `base_url` (scheme and host only) and
   `[jira] path` in it:

   ```bash
   mkdir -p ~/.config/productivity-agent-tools
   cp config.example.toml ~/.config/productivity-agent-tools/config.toml
   ```

3. Store your token (hidden prompt) and check the connection:

   ```bash
   uv run productivity-agent-tools set-token
   uv run productivity-agent-tools doctor
   ```

4. Add the server to Goose, then restart Goose:

   ```bash
   uv run productivity-agent-tools configure-agent
   ```

Writes are blocked until you turn `dry_run` off. The sections below explain each
step and what to do when one fails.

## How it authenticates

Every request carries your Jira **personal access token** as
`Authorization: Bearer ...`. Nothing else is needed for a Jira you can reach
directly.

TLS is always verified. By default it uses the operating system's trust store, so
internal CAs your browser already trusts work without extra setup; `ca_bundle`
in the config pins trust to a specific PEM file instead.

### Extra cookies

Some organizations put a proxy in front of their applications that wants a
particular cookie on every request. You can supply any such cookies under
`[cookies]` in the config, or per run with `--cookie name=value` (repeatable;
command-line values add to the file's and override by name). None are sent by
default.

A common case is a **consent or terms-of-use banner**: the proxy answers every
request, API calls included, with a banner page until a cookie records that you
clicked through. If API calls come back as a web page, that is the likely cause.
Which cookie it is depends on the site, so finding it is up to you: accept the
banner in a browser, then look in the developer tools for the cookie it set.
Adding that cookie here means you are accepting the banner through this tool.

Cookie values are never logged or shown to the agent; only their names are.

## Safety defaults

| Setting | Default | Effect |
|---|---|---|
| `dry_run` | on | Write tools return the request they *would* send and send nothing |
| `[cookies]` | empty | No extra cookies are sent |

`dry_run` can be set in the config file (`true`/`false` or `1`/`0`) and
overridden per run with `--dry-run` / `--no-dry-run`. Command-line flags win.

## Setup

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/). If your Jira is only
reachable over a VPN, connect it first.

```bash
git clone git@github.com:dcode/productivity-agent-tools.git && cd productivity-agent-tools
uv sync
```

Create the config (it names your internal host, so it lives outside the repo):

```bash
mkdir -p ~/.config/productivity-agent-tools
cp config.example.toml ~/.config/productivity-agent-tools/config.toml
```

Edit `base_url` to your host (scheme and host, plus a path only if every product
shares a prefix such as `/atlassian`), and set `[jira] path` to
the sub-path Jira is served under, or `""` if it is at the root of the host.

### One host or several

The top-level `base_url`, `ca_bundle`, `timeout_seconds` and `[cookies]` are
defaults for every product. Each product's table can set any of them again for
itself, so both layouts work:

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

A product's own `[jira.cookies]` table replaces the top-level `[cookies]` for
that product instead of adding to it, so a cookie meant for one host is not
sent to another. `--cookie` on the command line adds to both.

Store your token:

```bash
uv run productivity-agent-tools set-token
```

Check everything end to end (read-only):

```bash
uv run productivity-agent-tools doctor
```

If a check fails, run it again with `--log-level debug` to see each request and
what the server answered (see [Logging](#logging)).

### Token sources

Checked in this order; the first one found wins:

1. Environment variable `JIRA_TOKEN`
2. `JIRA_TOKEN=...` inside the env file named by `token_env_file` or `--token-env-file`
   (for example `.env` or `~/.secrets/jira_token.env`)
3. macOS Keychain (written by `set-token`)

Bitbucket uses its own token, found the same way under the name
`BITBUCKET_TOKEN`.

### Bitbucket

Bitbucket is off until the config has a `[bitbucket]` table. To turn it on:

1. Add the table, with the sub-path Bitbucket is served under (or its own
   `base_url` if it is on a different host):

   ```toml
   [bitbucket]
   path = "/bitbucket"
   ```

2. Create an HTTP access token in Bitbucket (your profile, Manage account, HTTP
   access tokens) with read permission, and store it:

   ```bash
   uv run productivity-agent-tools set-token bitbucket
   ```

3. Run `doctor` again; it now checks Bitbucket too. Restart your agent to load
   the new tools.

## Logging

Logs are structured ([structlog](https://www.structlog.org/)) and always go to
stderr, because the MCP protocol owns stdout. They are readable lines in a
terminal and one JSON object per line otherwise, such as in an agent host's log
file.

Set the level with `log_level` in the config or `--log-level` on `serve` and
`doctor`: `debug`, `info` (default), `warning` or `error`.

At `debug` every HTTP exchange is logged as an `http_request` and an
`http_response` event: method, path, status, content type, size, duration, the
redirect target if any, the names of cookies sent and set, and a few headers
that say who answered (`server`, `via`, `www-authenticate`,
`x-seraph-loginreason`). The token, cookie values, query-string values and
request and response bodies are never logged.

```bash
uv run productivity-agent-tools doctor --log-level debug
```

## Add to Goose

Let the tool register itself:

```bash
uv run productivity-agent-tools configure-agent
```

This adds a `productivity` extension to `~/.config/goose/config.yaml` that starts
the server by the absolute path of the command in this checkout's environment,
so it does not depend on Goose's working directory or `PATH`. The rest of the
file, comments included, is kept, and the previous version is saved beside it as
`config.yaml.bak`. Running it again updates the entry in place.

| Option | Effect |
|---|---|
| `--show` | Print the entry without changing any file |
| `--agent` | Agent to configure; `goose` is the default and the only one so far |
| `--name` | Extension name, `productivity` by default |
| `--config` | Start the server with a config file other than the default |
| `--agent-config` | Goose config file to edit, if it is not in the default place |

To do it by hand instead, add a stdio extension with `goose configure` (Add
Extension, Command-line Extension) or in `~/.config/goose/config.yaml`. Keep the
arguments in this order; `uv run` treats the first word after its own options as
the program to start:

```yaml
extensions:
  productivity:
    name: productivity
    type: stdio
    enabled: true
    cmd: uv
    args:
      - run
      - --directory
      - /absolute/path/to/productivity-agent-tools
      - productivity-agent-tools
      - serve
    timeout: 300
```

To allow writes for a session, add `--no-dry-run` after `serve` in the entry's
`args`. Keep Goose's
tool approval on so you see each comment or transition before it is sent.

## Tools

| Tool | Kind | What it does |
|---|---|---|
| `jira_status` | read | Who is logged in, Jira version, whether writes are blocked |
| `jira_list_presets` | read | Named JQL queries from your config |
| `jira_run_preset` | read | Run a preset, return compact rows |
| `jira_search` | read | Ad-hoc JQL, return compact rows |
| `jira_get_issue` | read | One issue with description and newest comments |
| `jira_list_transitions` | read | Workflow transitions available on an issue |
| `jira_add_comment` | write | Add a comment (Jira wiki markup) |
| `jira_transition_issue` | write | Move an issue through a transition |
| `bitbucket_status` | read | Bitbucket version and whether the token is accepted |
| `bitbucket_list_pull_requests` | read | Pull requests where you are reviewer, author or participant |
| `bitbucket_get_pull_request` | read | One pull request with description, reviewers, newest comments and build results |

The `bitbucket_` tools appear only when Bitbucket is configured. They do not
return diffs.

An identical write (same issue and same comment text, or same issue and same
transition) repeated within 30 seconds is not sent again; the tool returns the
first result with `repeat_suppressed` set. Agents sometimes issue one call twice,
for example when a generated script runs its body two times.

Presets ship as `mine-open`, `updated-since-yesterday` and `current-sprint`;
edit them under `[jira.presets]` in your config.

## Data handling

Whatever these tools return is sent to the model your agent is configured with.
Confirm that model is approved for the data in your Jira before using this on
real tickets.

## Development

Install the git hooks once after cloning:

```bash
uv run pre-commit install
```

The hooks lint and format Python (ruff), Markdown (rumdl) and TOML (taplo), and
type-check with ty. To run everything by hand:

```bash
uv run pre-commit run --all-files
```

```bash
uv run pytest
```

Tests run against a fake Jira and proxy; nothing here contacts a real server.

### How the code is laid out

Product modules never make HTTP calls themselves. They talk to an `ApiClient`
(a protocol in `api.py`: `get`, `write`, `aclose`, plus `base_url` and
`dry_run`), and each kind of service has one adapter that implements it.
`AtlassianClient` in `http.py` is the adapter for Atlassian Data Center: bearer
token, TLS trust, extra cookies, logging and error messages. A new service with
different sign-in, such as Microsoft Graph, is a new adapter and a new product
module; the Jira code does not change. Every adapter must honor `dry_run` in
`write`.

### Continuous integration

GitHub Actions runs on every pull request and on every push to `main`:

| Job | What it does |
|---|---|
| Lint, format and type-check | All pre-commit hooks: ruff, rumdl, taplo and ty |
| Test | The test suite with the coverage threshold, on Linux and macOS |
| Build | Builds the sdist and wheel, installs the wheel into a clean environment, and uploads both as an artifact |

Dependabot opens weekly pull requests for GitHub Actions and Python dependency updates.

### Releasing

1. Bump the version: `hatch version minor` (or `patch`, `major`), then commit.
2. Tag the commit with the same version, prefixed with `v`, and push the tag:

   ```bash
   git tag v0.2.0 && git push origin v0.2.0
   ```

The release workflow checks that the tag matches `__version__`, runs the full
CI suite, and publishes a GitHub release with the sdist and wheel attached and
generated release notes. It does not publish to PyPI.

### With hatch

The same checks are available as [hatch](https://hatch.pypa.io/) commands, which
manage their own environments:

| Command | What it runs |
|---|---|
| `hatch test` | The test suite with branch coverage and the 90% threshold |
| `hatch test -k name --no-cov` | A subset of tests, without the coverage threshold |
| `hatch fmt` | ruff lint (with fixes) and format, using this project's settings |
| `hatch fmt --check` | The same, reporting only |
| `hatch run types` | ty type check |
| `hatch run hooks` | Every pre-commit hook on all files |
| `hatch run check` | Hooks, then tests |
| `hatch run doctor` | The read-only connection check |
| `hatch build` | Build the sdist and wheel into `dist/` |

`pytest` measures branch coverage and fails below 90%; the suite currently
sits at 100%. Tool settings live in `pyproject.toml`, except taplo's, which it
only reads from `.taplo.toml`.

Layout: `http.py`, `config.py` and `tokens.py` are the shared core; each product
is a sub-package (`jira/`) that registers its own tools in `server.py`.
