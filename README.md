# productivity-agent-tools

An MCP server that gives an AI agent (Goose first, Claude Code later) tools for
daily work against a **self-hosted Atlassian Data Center** deployment. Version
one covers Jira.

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

Edit `base_url` to your host (scheme and host only), and set `[jira] path` to
the sub-path Jira is served under, or `""` if it is at the root of the host.

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

Add a stdio extension, either with `goose configure` (Add Extension, Command-line
Extension) or in `~/.config/goose/config.yaml`:

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

To allow writes for a session, add `--no-dry-run` after `serve`. Keep Goose's
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
