# productivity-agent-tools — MCP server for daily work against self-hosted Atlassian

## Problem Statement

On a regulated enterprise network, the daily tools (Jira, Bitbucket and Confluence, and
later Microsoft 365 calendar and mail) are self-hosted and reachable only over a VPN. People
sign in to them in a browser with a smart card. An AI agent on the same laptop, such as Goose
or Claude Code, has no way to read today's tickets, check a pull request, or post a status
update for its user. Earlier attempts to call the Jira API with a personal access token
failed, so it seemed the API might require smart-card authentication, which would put it out
of reach of a script.

A further constraint is that the work laptop is not the development machine. Code is built
elsewhere, pushed to GitHub and pulled onto the work laptop. Facts about the live servers
reach development only as script output that the user pastes back.

## Solution

`productivity-agent-tools` is one Python MCP server over stdio. It gives an agent typed tools
for Jira and Bitbucket (reads of issues, code, pull requests, builds and deployments, plus a
narrow set of writes in each), and has a CLI for setup,
diagnosis and running a tool directly.

Probing the real deployment showed that smart-card authentication is not required. The
token failures were caused by a reverse proxy that answers every request on the host, API
calls included, with a consent-banner HTML page until the request carries the cookie the
banner sets. When that cookie is added, a personal access token sent as
`Authorization: Bearer` authenticates normally. The server therefore uses bearer tokens plus
optional user-supplied cookies, verifies TLS against the operating system's trust store, and
keeps writes behind a dry-run switch that is on by default.

## User Stories

### Setup and configuration

1. As a user, I want to install with `git clone` and `uv sync`, so that the work laptop needs
   nothing beyond Python 3.14, uv and PyPI access.
2. As a user, I want my config at `~/.config/productivity-agent-tools/config.toml`, outside
   the repository, so that my internal hostname is never committed.
3. As a user, I want to start from `config.example.toml`, which uses a placeholder host, so
   that I can see every option with comments.
4. As a user whose products all sit on one host under sub-paths, I want a top-level
   `base_url` plus a per-product `path` (such as `[jira] path = "/jira"`), so that one host
   serves every product.
5. As a user whose products are on separate hosts, I want each product's table to set its own
   `base_url`, `ca_bundle`, `timeout_seconds` and cookies, so that the more common
   `jira.corp.example` and `confluence.corp.example` layout works too.
6. As a user, I want a product's own cookie table (such as `[jira.cookies]`) to replace the
   top-level `[cookies]` instead of adding to it, so that a cookie meant for one host is never
   sent to another.
7. As a user, I want a product to be switched on only when its table exists (an empty table
   is enough), so that I am never asked for a token for a product I do not use.
8. As a user, I want `serve`, `call` and `doctor` to stop with a clear message when no
   product is configured, so that a broken config cannot pass for one that works.

### Authentication and trust

9. As a user, I want to store a token once with `set-token` (or `set-token bitbucket`) at a
   hidden prompt, so that it is kept in the macOS Keychain and never in a file or in the
   agent's config.
10. As a user, I want the token to be looked up from the `JIRA_TOKEN` environment variable,
    then a `JIRA_TOKEN=` line in an env file (`token_env_file` or `--token-env-file`, for
    example `.env` or `~/.secrets/jira_token.env`), then the Keychain, so that I can choose
    how to provide it. Bitbucket does the same with `BITBUCKET_TOKEN`.
11. As a user, I want TLS always verified against the operating system's trust store, so
    that internal CAs my browser already trusts work without setup, even when a
    non-system `curl` or OpenSSL earlier on my `PATH` ignores the Keychain.
12. As a user, I want `ca_bundle` to pin trust to a specific PEM file, and `export-ca` to
    write one from the macOS Keychain, so that I can still pin trust when I need to.
13. As a user behind a consent-banner proxy, I want to supply any cookies under `[cookies]`
    or per run with `--cookie name=value` (repeatable, overriding the file's values by name),
    so that API calls get past the banner. Adding the cookie is my own deliberate acceptance
    of the banner.
14. As a user, I want no cookies sent by default, and cookie values never logged or shown to
    the agent (names only), so that nothing is accepted or leaked on my behalf.

### Safety

15. As a user, I want write tools blocked by `dry_run`, which defaults to on, so that a new
    setup cannot change anything until I choose.
16. As a user, I want `dry_run` settable in the config (`true`/`false` or `1`/`0`) and
    overridable per run with `--dry-run` / `--no-dry-run`, with the command line winning, so
    that I can run read-only on days I want no risk.
17. As a user, I want a blocked write to return the exact request it would have sent, so
    that I can check what the agent meant to do.
18. As a user, I want an identical write (the same comment on the same issue or pull request,
    the same transition, or the same review) repeated within 30 seconds to be sent once,
    with the first result returned and `repeat_suppressed` set, so that an agent that runs
    one call twice (for example from a generated script) does not post twice. Failed and
    dry-run writes are not remembered.
19. As a user, I want tools annotated as read or write in MCP, so that the agent's approval
    mode can run reads freely and show me each write before it is sent.

### Jira

20. As a user, I want named JQL presets (`mine-open`, `updated-since-yesterday`,
    `current-sprint` shipped as defaults, editable under `[jira.presets]`), so that the agent
    can pull my daily view by name.
21. As a user, I want the agent to run ad-hoc JQL and get compact rows back, so that it can
    answer questions the presets do not cover.
22. As a user, I want one issue in full, with its description and newest comments, so that
    the agent can summarize it.
23. As a user, I want the agent to list an issue's available transitions and move it through
    one, so that it can update status for me.
24. As a user, I want the agent to add a comment in Jira wiki markup, so that it can post
    updates.
25. As a user, I want `jira_status` to say who is signed in, the Jira version and whether
    writes are blocked, so that the agent can check its own connection.
26. As a user, I want the agent to log time on an issue in Jira's own duration syntax, with
    an optional comment and start time, and to list what is already logged, so that it can
    keep my timesheet current.
27. As a user, I want `jira_get_issue` to show every standard field (project, type, status,
    priority, people, dates, resolution, time tracking, labels, components, versions and
    links), so that the agent sees what I see in the browser.
28. As a user, I want the agent to edit an issue's standard fields and create issues, checked
    against the screens Jira configures for the project, so that a field or value Jira would
    reject is caught with the allowed choices before anything is sent.
29. As a user, I want the agent to link issues as a sentence ("ABC-1 blocks ABC-2") and to
    set a resolution through a transition, so that status, resolution and links change the
    way Jira expects.
30. As a user, I want the agent to assign an issue to me, to nobody, or to a person I name,
    and to ask me when a name matches more than one person, so that it never assigns work to
    the wrong person.

### Bitbucket

31. As a user, I want to list pull requests where I am reviewer, author or participant, and
    every pull request in a repository by state and text, so that the agent can find what
    needs my attention.
32. As a user, I want one pull request with its description, reviewers, newest comments and
    build results, plus its commits, changed files and a unified diff cut at a size limit, so
    that the agent can review it.
33. As a user, I want to list projects and repositories, so that the agent can discover keys
    and slugs.
34. As a user, I want a `default_project` under `[bitbucket]` and a
    `bitbucket_set_default_project` tool that lasts until the server restarts, so that I do
    not have to name the project on every call.
35. As a user, I want code search through the endpoint behind Bitbucket's own search page, so
    that the agent can find where something is defined or used.
36. As a user, I want a search-index outage (a 5xx) reported as such, with an instruction not
    to rebuild the search by walking every repository, so that a failed search does not burn
    my model's daily credit quota.
37. As a user, I want to know whether a pull request can merge now and which checks block it
    (approvals, builds, tasks, conflicts), its open and resolved tasks, and its history of
    pushes, comments and reviews, so that the agent can tell me what is left to do on it.
38. As a user, I want the Jira issues a pull request is linked to, as keys the `jira_` tools
    accept, so that the agent can move from a pull request to its ticket.
39. As a user, I want the CI build results of any commit (with totals, test counts and
    descriptions), the builds a repository requires before merging, and the state of a
    deployment I name, so that the agent can explain a red build or a stuck release.
40. As a user, I want the agent to comment on a pull request, reply to a comment, or add a
    task, in Markdown, so that it can post review feedback for me.
41. As a user, I want the agent to approve a pull request, mark it as needing work, or
    withdraw either, so that it can record my review once I have agreed to it.
42. As a user, I want the server to find my Bitbucket user slug itself, and to let me set it
    under `[bitbucket]` when it cannot, so that reviews work without extra setup.

### Jira and Bitbucket together

43. As a user, I want the pull requests linked to a Jira issue, with their state and branches,
    including ones linked only by branch name or title, so that the agent can tell me where
    the work on a ticket stands.
44. As a user, I want the Jira issues linked to a pull request to come with their summary,
    status and assignee when Jira is configured, so that the agent does not need a second
    call per issue.

### Diagnostics and operation

45. As a user, I want `doctor` to check config, token and connection for each configured
    product and report each separately, so that I can find what is wrong without an agent.
46. As a user, I want structured logs (structlog) on stderr, readable in a terminal and JSON
    otherwise, with `log_level` in the config or `--log-level`, so that the MCP protocol keeps
    stdout and an agent host's log file is parseable.
47. As a user, I want `debug` to log each HTTP exchange (method, path, status, content type,
    size, duration, redirect target, cookie names, identifying headers) and never the token,
    cookie values, query values or bodies, so that I can paste debug logs safely.
48. As a user, I want a redirect error to name its destination and say what to check, so
    that I can tell a login page from a wrong path.
49. As a user, I want a failed request whose path repeats a segment (such as `/jira/jira`) or
    contains `//` to ask whether that was intended and show the full address, without
    rejecting such paths outright, so that a legitimate shared prefix still works.
50. As a user, I want an empty 2xx answered with an HTML content type treated as success, so
    that a Jira transition that went through is not reported as a banner page.
51. As a user, I want `call <tool> name=value ...` to run any tool without an agent and print
    JSON, typed from the tool's schema and obeying `dry_run`, so that I can test against real
    servers without spending model credits. With no tool name, it lists the tools.
52. As a user, I want `call --field NAME` to print one field as written, with a diff coloured
    only when stdout is a terminal, so that a diff reads as a diff and agents and pipes stay
    plain.
53. As a user, I want `scripts/smoke-test.sh` to run every tool once through `call`, skipping
    unconfigured products and unfilled `<placeholder>` values and forcing its one write into
    dry-run, so that I can confirm a release against my servers in one step.

### Agent registration

54. As a user, I want `configure-agent` to add a `productivity` stdio extension to Goose's
    config that starts the server by the absolute path of the command in this checkout's
    environment, so that it does not depend on Goose's working directory or `PATH`.
55. As a user, I want `configure-agent` to keep the rest of the file and its comments, save
    `config.yaml.bak`, update in place on re-run, and preview with `--show`, so that it is
    safe to run.

### Development

56. As a developer, I want product modules to depend only on an `ApiClient` protocol, so that
    a service with different sign-in (such as Microsoft Graph) is a new adapter and the Jira
    code does not change.
57. As a developer, I want tests to run against an in-process fake Jira, fake Bitbucket and
    intercepting proxy, so that nothing in the suite contacts a real server.
58. As a developer, I want one set of checks (ruff, rumdl, taplo, ty, pytest with coverage)
    run by pre-commit, hatch and CI alike, so that a passing local run means a passing CI run.

## Implementation Decisions

### Overall shape

- One Python package and one MCP server (`productivity-agent-tools serve`, stdio), using the
  official `mcp` SDK. MCP was chosen because both Goose and Claude Code speak it, so adding
  Claude Code later is registration work, not a rewrite.
- Goose is the only supported agent host. Claude Code is off the roadmap because it will
  not be available in the work environment for a long time (see Out of Scope).
- One server with one sub-package per product (`jira/`, `bitbucket/`), each with
  `client.py`, `models.py`, `tools.py`, `settings.py` and `product.py`. A shared core
  (`http.py`, `config.py`, `tokens.py`, `logs.py`, `payload.py`, `tooling.py`,
  `repeats.py`, `products.py`, `capabilities.py`) holds everything that is not specific to
  a product, and never names one.
- **Products are plugins, found through entry points.** Each `product.py` exposes a
  `Product` (a frozen, generic dataclass over the product's settings and client classes)
  holding its name, title, server-instructions paragraph, settings class and parser,
  client factory, tool registration and `doctor` checks. It is listed under the
  `productivity_agent_tools.products` entry-point group in `pyproject.toml`.
  `config.py`, `server.py` and the CLI loop over the installed products, so a new product
  (`gitlab_wiki` and `gitlab_scm` are expected) is a new sub-package plus one entry-point
  line. A product may also come from a separately installed package.
- Chosen over pluggy: discovery is all that is needed, `importlib.metadata` is in the
  standard library, and a dataclass of typed callables keeps the boundary visible to `ty`.
  pluggy's fan-out hook calls return untyped lists and would add a dependency.
- `Product.token_name` lets products on one host share a token (two GitLab products
  would both use `gitlab`); it defaults to the product's name.
- Products never import each other. Two ways to combine them:
  - **Integrations** (`Integration` under `productivity_agent_tools.integrations`) are
    tools that need several named products, registered only when all are on. `links.py`
    is the Jira and Bitbucket one.
  - **Capabilities** (`capabilities.py`) are runtime-checkable protocols that a client may
    implement. A product's tools get a `ToolContext` with the other products' clients as
    `peers` and ask for one with `context.peer(IssueTracker)`, not caring which product
    answers. This is for data one product can use but does not need.
- A config table that names no installed product is an error, so a typo does not
  silently switch a product off.
- Development happens on a separate machine. Code reaches the work laptop through GitHub, and
  facts about the live servers come back as pasted probe or smoke-test output.

### Connection layer

- `api.py` defines `ApiClient`, a `Protocol` with `get`, `get_text` (plain-text resources
  such as raw diffs), `query` (reads that must be POSTed), `write` and `aclose`, plus
  `base_url` and `dry_run`. Product modules never make HTTP calls themselves.
- `AtlassianClient` in `http.py` is the Atlassian Data Center adapter: bearer token, TLS
  trust, extra cookies, logging and plain-language errors. One adapter is built per product.
- Each `Product` names its adapter (`Product.adapter`, matching the `Adapter` protocol in
  `api.py`); Jira and Bitbucket use `AtlassianClient.from_connection`. A GitLab product
  would bring its own adapter, since its errors and authentication differ.
- Every adapter must honor `dry_run` in `write`. `get_text` and `query` are reads and are
  never blocked.
- HTTP is async `httpx`, with TLS trust from `truststore` (the operating system's store) unless
  `ca_bundle` is set.
- Error messages name the cause in plain language: 401, 403 and 404 each get their own
  explanation, a redirect names its target without the query string, an HTML answer to an
  API call is reported as an intercepting page, and a suspicious path (repeated segment or
  `//`) is questioned on failure. The error reader understands both Jira's and Bitbucket's
  error formats.

### Configuration

- TOML at `~/.config/productivity-agent-tools/config.toml`. The top-level `base_url`,
  `ca_bundle`, `timeout_seconds` (30) and `[cookies]` are defaults that a product table can
  override. The top-level `base_url` is optional when every product names its own.
- `base_url` is scheme and host, plus a path only when every product shares a prefix. Each
  product's sub-path goes in its own `path` (`""` for the host root).
- `dry_run` (default `true`), `token_env_file`, `log_level` (default `info`) and per-product
  `default_max_results` (25) are config keys. Results are capped at 100 per call.
- Jira presets live under `[jira.presets]`. Bitbucket's `default_project` lives under
  `[bitbucket]`.
- CLI parsing uses cyclopts, which also brings in `rich` (now listed directly). Command-line
  flags override the config.

### Authentication

- Personal access tokens or HTTP access tokens, sent as `Authorization: Bearer`. Jira and
  Bitbucket each have their own token.
- Token sources, first match wins: the environment variable (`JIRA_TOKEN` /
  `BITBUCKET_TOKEN`), then an env file, then the macOS Keychain (through `keyring`, written by
  `set-token`).
- Extra cookies are generic (`[cookies]` and `--cookie`) and not tied to any one banner. The
  consent-banner case is documented as a use case, and finding the right cookie is left to the
  user, because it differs from site to site.
- Smart-card (client-certificate) authentication is not implemented: the deployment does not
  request a client certificate for the API (see Further Notes).

### Write safety

- Jira writes in this version are add comment, transition issue (with an optional
  resolution), log work, assign, edit issue, create issue and link issues.
  Bitbucket writes
  are add a pull-request comment (new, reply or task) and set the user's review (approve,
  needs work, or neither).
- Dry-run is on by default and returns the would-be request. This was chosen over relying on
  the agent's approval prompt alone, or a two-step confirm tool that the agent could call
  itself.
- Tools carry MCP read and write annotations so the host's approval mode applies as well.
- Repeat suppression keys on (issue, comment text), (issue, transition), (issue, duration,
  start, comment), (issue, resolved username), (issue, requested edits), (project, issue type, fields),
  (issue, relation, other issue), (pull request,
  parent comment, task flag, comment text) or (pull request, review) with a 30-second window
  (`repeats.py`). Simultaneous repeats share one in-flight result. Each product has its own
  guard.
- `writes.py` holds what every product's writes share: `WriteRequest`, the `WriteReport`
  base model that each product's result extends (Jira adds the issue key, Bitbucket the
  pull request and new comment id), the dry-run message, and the repeat wrapper.

### Jira specifics

- Log work is `POST issue/{key}/worklog` with `timeSpent`, and `comment` and `started` only
  when given; Jira then uses now and lowers the remaining estimate by itself. The duration
  is checked against Jira's syntax (numbers with w, d, h or m) and tidied (`1 H 30 M` becomes
  `1h 30m`) before anything is sent. A start time without an offset is read as local time
  on the laptop, and a bare date as 09:00 local, then sent as `yyyy-MM-ddTHH:mm:ss.SSS+zzzz`.
- Assign is `PUT issue/{key}/assignee` with `{"name": username}`, or `null` to unassign.
  "me" resolves through `myself`, and anything else through `user/assignable/search` for that
  issue. An exact username or display-name match wins, a single partial match is used, and
  more than one is refused with up to ten candidates listed. The lookup happens before the
  dry-run check and before repeat suppression, so "me" and the user's own username count as
  the same change.
- Write results now carry `created_id`: the id of a new comment or worklog, or the key of a
  new issue.
- Field metadata lives in `jira/fields.py`. `editmeta` (one issue) and
  `createmeta/{project}/issuetypes[/{id}]` (Data Center 9 and later; pages or bare lists are
  both read) say which fields a screen has, which are required, and their allowed values.
  Every field the edit and create tools set is checked against that metadata, even in
  dry-run. Values are matched by name or id in any letter case and sent as `{"id": ...}`
  (or `{"name": ...}` when Jira gives no id).
- Of the standard fields: summary, description, issue type, priority, reporter, due date and
  `timetracking` (original and remaining estimate) are set through `fields`. Labels,
  components, fix versions and affects versions (`versions`) are added or removed through
  `update`, so the rest of each list is kept. On create they are set as lists. Status and
  resolution change only through transitions (the transition is fetched with
  `expand=transitions.fields`, which lists the resolutions its screen allows), the assignee
  through `jira_assign_issue`, time spent through worklogs, and links through `issueLink`.
  Project, created, updated, `timeoriginalestimate` and `timespent` are read-only here.
- Creating an issue fills project and issue type itself and leaves reporter to Jira's
  default (the user). A required field without a default that the tool cannot fill, such as
  a custom field, refuses the create with the field names listed. A sub-task type needs a
  parent and any other type must not have one.
- Links read as a sentence: (key, relation, other key). The relation may be a link type's
  name, its outward wording or its inward wording. An inward wording swaps the ends. The
  request puts the sentence's first issue in `inwardIssue`, because Jira treats
  `inwardIssue` as the link's source, the issue that shows the outward wording. This
  direction is to be confirmed on the live server (see ROADMAP.md).

### Bitbucket specifics

- Only `bitbucket_add_pull_request_comment` and `bitbucket_review_pull_request` change
  anything. Comments POST to `comments`, tasks to `blocker-comments`, both with an optional
  `parent` for replies. File and line comments are not offered yet.
- Reviews use `PUT .../participants/{userSlug}` with `APPROVED`, `NEEDS_WORK` or
  `UNAPPROVED`. The older `POST .../approve` was deprecated in 4.2 and cannot mark needs
  work. REST API 1.0 has no "who am I" resource, so the slug comes from
  `plugins/servlet/applinks/whoami` (the user name, as plain text) plus `users/{name}`,
  once per run. `user_slug` under `[bitbucket]` skips both. The lookup runs in dry-run too,
  so a missing slug shows up before writes are turned on. `bitbucket_status` reports the
  slug, or null if Bitbucket would not say, and does not fail over it.
- Code search uses the undocumented `rest/search/1.0/search` endpoint (POST, through `query`).
  It searches all projects unless one is named, does not apply the default project, and
  covers default branches only.
- The diff defaults to 20,000 characters, with a ceiling of 100,000, and reports
  `total_chars` and `truncated`. Search hits show at most 12 lines each.
- Build results come from `rest/build-status/1.0/commits/{hash}` and its `stats` sibling.
  Atlassian deprecated that resource in 7.14 in favor of a per-repository one, but the
  replacement can only fetch a build whose key is already known, so it cannot list. The
  older resource is still served in 10.x and needs no project or repository. Fields that
  only newer servers send (test counts, duration, build number, ref) are optional.
- Bitbucket has no endpoint that lists deployments, only one that fetches a deployment by
  key, environment key and sequence number. `bitbucket_get_deployment` takes all three and
  its description tells the agent not to guess them.
- Tasks are read from `blocker-comments` (Bitbucket 7.2 and later), merge readiness from
  `pull-requests/{id}/merge`, required builds from `rest/required-builds/latest`, and
  linked Jira issues from `rest/jira/latest`, which needs Bitbucket's application link to
  Jira. A 404 from an optional plugin endpoint names the version or link it needs.
- Endpoint shapes were taken from Atlassian's published OpenAPI specification for 10.0,
  because the reference pages are rendered in the browser.
- One deployment timestamp (`lastUpdated`) is documented with a value in seconds, while
  every other Bitbucket timestamp is in milliseconds. Values below 10^11 are read as
  seconds.

### Jira and Bitbucket together

- `links.py` holds what needs both products, as the `jira_bitbucket` integration. It is
  registered only when both are configured, as is its paragraph of the server
  instructions.
- `jira_get_issue_pull_requests` merges two sources, run concurrently. The first is Jira's
  Development panel: `rest/dev-status/1.0/issue/detail` with the issue's numeric id,
  `applicationType=stash` and `dataType=pullrequest`. It is undocumented and read
  defensively, with project, repository and number taken from each entry's URL. The second
  is Bitbucket's documented `jira/latest/issues/{key}/commits` (up to 50 commits), followed
  by `commits/{id}/pull-requests` for up to 20 distinct commits. Pull requests are merged by
  (project, repository, number), and `found_by` says where each came from.
- A source that fails with this package's own error is listed under `unavailable` with the
  reason, and the other still answers. A single commit lookup that fails, such as one in a
  repository the user cannot read, is counted rather than failing the source. Any other
  exception is raised, so a bug is not hidden. `more_commits` tells the agent the list may
  be incomplete, and not to check the rest one by one, because of the model credit budget.
- `bitbucket_get_pull_request_issues` adds details when a peer offers the `IssueTracker`
  capability, which `JiraClient.describe_issues` implements. It runs one JQL search,
  `key in (...)`, over keys validated as issue keys. If Jira refuses the search (one
  missing key fails the whole query), the keys are returned without details and
  `details_note` says why.

### Logging

- structlog on stderr, always. Output is readable lines in a terminal and JSON lines
  otherwise.
- At `debug`, `http_request` and `http_response` events are logged, never containing
  secrets or bodies.
- The `httpx`, `keyring` and `mcp` loggers are capped at warning.

### CLI

- `serve`: runs the MCP server (`--dry-run/--no-dry-run`, `--cookie`, `--log-level`,
  `--config`, `--token-env-file`).
- `doctor`: read-only check, one per configured product.
- `set-token [jira|bitbucket]`: hidden prompt, stored in the Keychain.
- `export-ca`: exports the macOS Keychain CAs to a PEM file (default `~/ca-bundle.pem`).
- `call [tool] [name=value ...] [--field NAME]`: runs a tool directly.
- `configure-agent [--agent goose] [--name productivity] [--config] [--agent-config]
  [--show]`: registers the server with Goose by absolute path, using `ruamel.yaml` to keep
  comments, with a startup timeout of 300 seconds.

### Tooling and code style

- Python 3.14+, uv for environments, hatchling build with the version read from `__version__`.
- Async for all I/O, Google-style docstrings, strict annotations throughout (`X | None`, never
  `Optional`; built-in generics such as `list[]`).
- ruff (lint and format, line length 100), ty (type check, warnings are errors), rumdl
  (Markdown), taplo (TOML), all through pre-commit. Settings live in `pyproject.toml`, except
  taplo's, which live in `.taplo.toml`.
- pytest with branch coverage and a 90% floor, aiming for 100%. hatch scripts: `hatch test`,
  `hatch fmt`, `hatch run types`, `hatch run hooks`, `hatch run check`, `hatch run doctor`,
  `hatch build`.
- American English everywhere. Conventional-commit messages.
- GitHub Actions: pre-commit job, test job on Linux and macOS, build job that installs the
  wheel into a clean environment. A `v*` tag publishes a GitHub release (not to PyPI) after
  checking that the tag matches `__version__`. Dependabot runs weekly for Actions and Python.

### Roadmap

Designed for but deferred. [ROADMAP.md](ROADMAP.md) has the ordered backlog and status.

- **Writing links between the products:** for example a remote link from an issue to a
  pull request. Bitbucket and Jira link them by key automatically, so this waits for a
  need.
- **Confluence:** a third Atlassian product module behind the same adapter. It sits on the
  same host and sub-path pattern as Jira and Bitbucket.
- **Custom Jira fields:** reading and setting `customfield_*` values (such as story points or
  epic links), whose shapes vary by field type.
- **More Bitbucket writes:** comments on a file or line of the diff, resolving tasks, and
  editing or deleting the user's own comments.
- **Microsoft Graph (Microsoft 365 calendar and mail):** a new adapter implementing
  `ApiClient` with its own sign-in, plus a new product module. This is the reason
  per-product hosts and the adapter protocol exist.
- **OpenTelemetry tracing** of httpx requests and responses, alongside the structlog
  logging.

## Testing Decisions

- Automated tests run against in-process fakes of Jira and Bitbucket and an intercepting
  proxy that serves a banner page until a cookie is present. Nothing in the suite contacts a
  real server.
- The coverage floor is 90% (branch coverage). The suite is currently at about 99.9% (271
  tests).
- The live servers cannot be reached from development or CI, so verification against them is
  manual. The user runs `doctor`, `call`, or `scripts/smoke-test.sh` on the work laptop and
  pastes the output back.
- Confirmed against a real Jira 11.3 and Bitbucket 10.2.8: every Jira read tool, a real
  transition and comment through Goose, a dry-run comment, Bitbucket status, projects,
  repositories, both pull-request lists, and a pull request's detail, commits, changed files
  and diff.
- Not yet confirmed against a real server (tracked in [ROADMAP.md](ROADMAP.md)): comment
  parsing on an issue or pull request that has comments, Bitbucket build results, a moved or
  renamed file in the changed-files list, code search (the server's index was returning
  503), every tool added for merge status, activity, tasks, linked issues, commit
  builds, required builds and deployments, and the Bitbucket writes (including the
  `applinks/whoami` lookup through a bearer token), the Jira worklog and assign tools, and
  the Jira field, create, transition-resolution and link tools, and the Jira and Bitbucket
  join (`rest/dev-status` in particular).
- Prior art: none before this project. These tests establish the repo's convention.

## Out of Scope

- **Smart-card client-certificate authentication.** It is unnecessary because
  the API accepts bearer tokens once past the banner (see Authentication and Further Notes).
- **Automatically accepting a consent banner, or shipping any site's cookie name.** The user
  supplies the cookie deliberately (see Authentication).
- **Moving an issue to another project, deleting issues or links, and editing or deleting
  worklogs.** Moving needs Jira's bulk-move wizard; deletes are left to the browser.
  Custom fields are deferred (see Roadmap).
- **Bitbucket merges, declines and branch changes.** These change code or end a review,
  so they are left to the user in the browser. Comment anchors and task resolution are
  deferred (see Roadmap).
- **Confluence and Microsoft 365.** Deferred (see Roadmap).
- **Claude Code as an agent host.** Not planned: it will not be available in the work
  environment for a long time. Because the server speaks MCP, adding it later means only a
  second `configure-agent` target (see Overall shape).
- **One MCP server per product.** Rejected in favor of one server with product modules (see
  Overall shape).
- **A stdlib-only, dependency-free build.** Unnecessary because PyPI is reachable from the
  work laptop (see Overall shape).
- **Publishing to PyPI.** Releases are GitHub releases only (see Tooling).
- **Activity digests and board-based sprint views as the daily default.** Named JQL presets
  were chosen instead (see Jira user stories).

## Further Notes

- **How the design was reached:** a design interview (one question at a time, each with a
  recommended option) on 2026-11-10. Before deciding authentication, it ran two rounds of
  read-only probe scripts on the work laptop, passed through a private gist. The decisions
  above are what the user confirmed at the end of that interview, plus the changes made over
  the rest of the day. The user's three changes to the first proposal were: dry-run as a CLI
  flag and config key, defaulting to on; a banner opt-in that was later generalized into
  arbitrary extra cookies; and cyclopts for argument and config parsing.
- **Confirmed by probing (not assumed):** the TLS handshake for API paths asks for no client
  certificate. Without the cookie, every path returns the same 200 HTML banner page. With the
  cookie and no token, Jira returns 401 JSON. With the cookie and a bearer token,
  `/rest/api/2/myself` returns 200 and searches work. The host root redirects to `/jira`, and
  Bitbucket and Confluence answer under `/bitbucket` and `/confluence`.
- **The `/jira/jira` incident:** a config whose `base_url` already ended in `/jira`, combined
  with `path = "/jira"`, redirected to the login page. That led to the suspicious-path hint.
  Such paths are questioned rather than rejected, because a real reverse proxy may mount
  products under a shared prefix.
- **The Goose launch failure:** Goose could not start `uv run ... serve` from its own working
  directory. That led to `configure-agent` launching the server by absolute path.
- **Model credit budget:** the agent's model runs on a daily credit quota. When code search
  failed, the agent tried to rebuild it by listing every repository in every project and ran
  out of quota. Tool error messages therefore tell the agent what not to do, as well as what
  went wrong.
- **Neutral wording:** the repository describes the environment in generic terms ("a
  regulated enterprise network", "a consent banner"). It contains no organization-specific
  references, hostnames, usernames, or project keys, because the repo is pulled
  from outside the network.
- **Glossary:**
  - *Product*: a service with its own config table and tool module.
  - *Adapter*: an `ApiClient` implementation for a kind of service.
  - *Preset*: a named JQL query.
  - *Repeat suppression*: the 30-second identical-write guard.
- This is the repo's first PRD and sets its format.
