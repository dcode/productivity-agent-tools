# productivity-agent-tools

An MCP server that gives an AI agent (Goose first, Claude Code later) tools for
daily work against a **self-hosted Atlassian Data Center** deployment. It covers
Jira (read and write) and, when configured, Bitbucket (reads, plus pull-request
comments and reviews).

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

A product is switched on by having its table in the config. With no `[jira]`
table the Jira tools are not offered and no Jira token is needed; the same goes
for `[bitbucket]`. An empty table is enough to turn a product on with its
defaults, and at least one product must be configured.

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
   default_project = "PROJ"  # optional: the project key you usually work in
   ```

2. Create an HTTP access token in Bitbucket (your profile, Manage account, HTTP
   access tokens) with read permission, and store it:

   ```bash
   uv run productivity-agent-tools set-token bitbucket
   ```

3. Run `doctor` again; it now checks Bitbucket too. Restart your agent to load
   the new tools.

## Run a tool without an agent

`call` runs one tool directly and prints its result as JSON. It uses the same
config, tokens and dry-run setting as the server, and spends no model credits,
so it is the quickest way to check a tool against your real servers:

```bash
uv run productivity-agent-tools call
```

```bash
uv run productivity-agent-tools call jira_search "jql=assignee = currentUser()" max_results=5
```

```bash
uv run productivity-agent-tools call bitbucket_get_pull_request \
  repository=my-service pull_request_id=42 --log-level debug
```

`--field NAME` prints one field of the result on its own. Text comes out as
written instead of as JSON, so a diff reads as a diff, and in a terminal it is
coloured (piped output stays plain):

```bash
uv run productivity-agent-tools call bitbucket_get_pull_request_diff \
  repository=my-service pull_request_id=42 --field diff
```

With no tool name it lists every tool and its parameters; names in square
brackets are optional. Arguments are written `name=value`; quote any that
contain spaces. Write tools still obey `dry_run`, so add `--no-dry-run` to send
a change for real.

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
| `jira_list_worklogs` | read | Time logged on an issue, with the total |
| `jira_log_work` | write | Log time (`1h 30m`, `2d`) with an optional comment and start time |
| `jira_assign_issue` | write | Assign to `me`, `none`, or a person found by name or username |
| `jira_get_edit_fields` | read | Fields an issue's edit screen allows, with their allowed values |
| `jira_edit_issue` | write | Summary, description, type, priority, reporter, due date, estimates; add or remove labels, components, versions |
| `jira_get_create_fields` | read | Issue types a project offers, and one type's fields |
| `jira_create_issue` | write | Create an issue or sub-task with the standard fields |
| `jira_list_link_types` | read | Kinds of issue links, with both wordings |
| `jira_link_issues` | write | Link two issues, read as a sentence: "ABC-1 blocks ABC-2" |
| `jira_get_issue_pull_requests` | read | Pull requests linked to an issue (needs Jira and Bitbucket both configured) |
| `bitbucket_status` | read | Bitbucket version, whether the token is accepted, the default project |
| `bitbucket_search_code` | read | Search file contents across repositories, optionally within a project or repository |
| `bitbucket_list_projects` | read | Projects you can see, with their keys |
| `bitbucket_set_default_project` | session | Choose the project used when a call names none |
| `bitbucket_list_repositories` | read | Repositories in a project |
| `bitbucket_list_pull_requests` | read | Pull requests where you are reviewer, author or participant |
| `bitbucket_list_repository_pull_requests` | read | Every pull request in one repository, by state and text |
| `bitbucket_get_pull_request` | read | One pull request with description, reviewers, newest comments and build results |
| `bitbucket_get_pull_request_commits` | read | Commits in a pull request |
| `bitbucket_get_pull_request_changes` | read | Files a pull request changes |
| `bitbucket_get_pull_request_diff` | read | Unified diff, cut at a size limit |
| `bitbucket_get_pull_request_merge_status` | read | Whether a pull request can merge, and each check blocking it |
| `bitbucket_get_pull_request_activity` | read | History: pushes, comments, approvals, needs-work reviews, merges |
| `bitbucket_list_pull_request_tasks` | read | Open or resolved tasks on a pull request |
| `bitbucket_get_pull_request_issues` | read | Jira issues linked to a pull request, with summary, status and assignee when Jira is configured |
| `bitbucket_get_commit_builds` | read | CI build results for any commit, with totals and test counts |
| `bitbucket_list_required_builds` | read | Builds that must pass before a repository's pull requests can merge |
| `bitbucket_get_deployment` | read | One deployment's state, by key, environment and run number |
| `bitbucket_add_pull_request_comment` | write | Comment on a pull request, reply to a comment, or add a task (Markdown) |
| `bitbucket_review_pull_request` | write | Approve, mark as needing work, or withdraw your review |

The `bitbucket_` tools appear only when Bitbucket is configured. Only the two
write tools change anything in Bitbucket, and `dry_run` blocks them just as it
blocks Jira's writes.

A review is recorded as the token's owner, so the server needs your Bitbucket
user slug. It asks Bitbucket once per run (`bitbucket_status` shows the
answer). If `bitbucket_status` shows `user_slug: null`, set `user_slug` under
`[bitbucket]` to the name in your Bitbucket profile URL.

`bitbucket_search_code` uses the endpoint behind Bitbucket's own search page
(`rest/search/1.0/search`). Atlassian does not document it, so it may behave
differently between versions; if a server does not have it, the tool says so.
It accepts Bitbucket's
[search syntax](https://confluence.atlassian.com/bitbucketserver/search-for-code-in-bitbucket-server-814204781.html),
searches every project unless you name one, and only covers default branches.

With both Jira and Bitbucket configured, `jira_get_issue_pull_requests` lists an
issue's pull requests from two sources and merges them: Jira's Development
panel (which also knows pull requests linked only by branch name or title) and
the pull requests containing commits that mention the key. If one source is
missing, for example when the products are not linked, the result says so and
still shows the other.

Bitbucket has no way to list deployments. `bitbucket_get_deployment` needs
the deployment key, environment key and sequence number that your CI or CD
server reported, so the agent asks you for them rather than guessing.

Most other Bitbucket tools need a project key. Set `default_project` under
`[bitbucket]` to the project you usually work in and the agent can leave it out.
You can also tell the agent which project to use; it calls
`bitbucket_set_default_project`, which lasts until the server restarts.

`jira_edit_issue` and `jira_create_issue` check every field against the issue's
edit screen or the project's create screen first, and match values (priorities,
components, versions) by name, so a field the screen lacks or a value it does
not allow is refused with the choices listed. Creating an issue whose screen
requires a custom field is refused; create it in the browser instead. Status
and resolution change only through `jira_transition_issue`, which takes a
`resolution` when the transition offers one. `jira_get_issue` shows all the
standard fields, including links, components, versions, due date, resolution
and time tracking.

`jira_assign_issue` only assigns someone Jira allows on that issue. A name that
matches more than one person is refused with the candidates listed, and the
agent has to ask you which one; it does this even in dry-run.

An identical write (the same comment text on the same issue or pull request,
the same transition, worklog, assignee, edit or link of an issue, the same new
issue, or the same review of a
pull request)
repeated within 30 seconds is not sent again; the tool returns the
first result with `repeat_suppressed` set. Agents sometimes issue one call twice,
for example when a generated script runs its body two times.

Presets ship as `mine-open`, `updated-since-yesterday` and `current-sprint`;
edit them under `[jira.presets]` in your config.

## Design and roadmap

[docs/DESIGN.md](docs/DESIGN.md) records the design: the problem, the decisions made and
the reasons for them. [docs/ROADMAP.md](docs/ROADMAP.md) tracks what is done, what still
needs checking against real servers, and what comes next.

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
