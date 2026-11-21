# productivity-agent-tools — MCP server for daily work: Atlassian, ServiceNow and Outlook

## Problem Statement

On a regulated enterprise network, the daily tools (Jira, Bitbucket and Confluence, a
ServiceNow helpdesk, and Microsoft 365 calendar and mail) sit behind a VPN, single sign-on
or both. People
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
for Jira, Bitbucket and Confluence (reads, plus a narrow set of writes in each), ServiceNow,
and Outlook mail and calendar (read-only), and has a CLI for setup, sign-in, diagnosis and
running a tool directly. Each product is a module switched on by its own config table.

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

9. As a user, I want to store a token once with `set-token jira` (or, with no name, pick
   from a list) at a hidden prompt, so that it is kept in the system keychain and never in a
   file or in the agent's config.
10. As a user, I want each product's token looked up from its environment variable
    (`JIRA_TOKEN`), then a command that prints it (`token_cmd`, such as a `sops` call), then
    a line in an env file (`token_env_file`, `${HOME}` and `~` expanded), then the keychain,
    with the command and file settable at the top level, per group or per product, so that
    I can keep tokens wherever my secrets already live.
11. As a user, I want TLS always verified against the operating system's trust store, so
    that internal CAs my browser already trusts work without setup, even when a
    non-system `curl` or OpenSSL earlier on my `PATH` ignores the Keychain.
12. As a user, I want `ca_bundle` to pin trust to a specific PEM file, and on macOS
    `export-ca` to write one from the Keychain, so that I can still pin trust when I need to.
13. As a user behind a consent-banner proxy, I want to supply any cookies under `[cookies]`
    or per run with `--cookie name=value` (repeatable, overriding the file's values by name),
    so that API calls get past the banner. Adding the cookie is my own deliberate acceptance
    of the banner.
14. As a user, I want no cookies sent by default, and cookie values never logged or shown to
    the agent (names only), so that nothing is accepted or leaked on my behalf.

### Safety

15. As a user, I want write tools blocked by `dry_run`, which defaults to on, so that a new
    setup cannot change anything until I choose.
16. As a user, I want `dry_run` settable in the config (`true`/`false` or `1`/`0`), at
    the top level and in a product's own table (which wins for that product), and
    overridable per run with `--dry-run` / `--no-dry-run`, with the command line winning
    over every table, so that I can allow writes to one product (my own notes, say) while
    the others stay blocked, and run read-only on days I want no risk.
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
57. As a developer, I want tests to run against an in-process fake Jira, Bitbucket,
    Confluence and intercepting proxy, so that nothing in the suite contacts a real server.
58. As a developer, I want one set of checks (ruff, rumdl, taplo, ty, pytest with coverage)
    run by pre-commit, hatch and CI alike, so that a passing local run means a passing CI run.

### Confluence

59. As a user, I want to search Confluence with CQL and read a page by id or exact title, so
    that the agent can find and quote team documentation.
60. As a user, I want the agent to create a page in my personal space or a team space from
    Markdown, so that I can ask for a page such as "About me" in plain words and review it
    before it is published.
61. As a user, I want the agent to edit a page, including adding a section without
    rewriting the rest, without ever overwriting a change someone else made after it read
    the page, so that a shared page is never silently clobbered.
62. As a user, I want my personal space key shown by the status tool, so that the agent puts
    pages about me in the right place without asking for my username.

### Agile issues and branches

63. As a user, I want to create epics, put stories and tasks into an epic, and set story
    points, so that the agent can lay out a piece of work the way the board expects.
64. As a user, I want to create a branch for a Jira issue with a standard branch type
    (feature, bugfix, hotfix, release or custom), named the way Bitbucket names it, so that
    starting work is one request and the branch shows up on the issue.

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
- **Two kinds of product (2026-11-19).** `RemoteProduct` is the original shape: an
  adapter, a credential resolver, `token_name` and an optional `login`. `LocalProduct`
  is for data on this machine (the Obsidian vault is the first): instead of an adapter it
  has `open(settings, dry_run)`, an async context manager that yields its client, and its
  settings extend `LocalProductSettings` (class attribute `remote = False`), so config
  loading does not demand a `base_url`. Both share a base with the name, title,
  instructions, settings parser, tools and `doctor`; `Product` is a type alias for
  either. `server.py` takes `Connected(api)` for a remote product and `Opened(client)` for
  a local one; the CLI branches on the kind when connecting and in `doctor`, and
  `remote_products()` is what `set-token` and `login` list. Chosen over a fake
  `base_url` plus a no-op credential because a local product then never pretends to have
  either, and over giving it an `ApiClient` because file access has nothing HTTP-shaped
  to share. A transport other than HTTP later would be a third kind, not a stretch of
  `RemoteProduct`.
- `RemoteProduct.token_name` lets products on one host share a token (two GitLab products
  would both use `gitlab`); it defaults to the product's name.
- Products never import each other. Two ways to combine them:
  - **Integrations** (`Integration` under `productivity_agent_tools.integrations`) are
    tools that need several named products, registered only when all are on. `links.py`
    is the Jira and Bitbucket one.
  - **Capabilities** (`capabilities.py`) are runtime-checkable protocols that a client may
    implement. A product's tools get a `ToolContext` with the other products' clients as
    `peers` and ask for one with `context.peer(IssueTracker)`, not caring which product
    answers. This is for data one product can use but does not need.
  - **`TaskSource` and `MeetingSource` (2026-11-19)** are the capabilities the Obsidian
    sync reads: Jira (`tasks_jql`, 100 at most) and Bitbucket (open pull requests whose
    review by the user is still `UNAPPROVED`) offer `my_open_tasks` and `tasks_by_key`;
    the Outlook calendar offers `my_meetings`. They hand over plain records
    (`SourcedTask`, `SourcedMeeting`) already in the vault's terms (TaskNotes status and
    priority, aware times). `ToolContext.peers_of(kind)` returns every peer offering one,
    where `peer` returns the first. Chosen over an `Integration` per pair
    (`jira_obsidian`, `calendar_obsidian`, ...), which would grow with every source; a
    new source (ServiceNow requests, GitLab) joins by implementing the protocol. This also
    settles the open question about code hosts in favour of capabilities.
- A config table that names no installed product is an error, so a typo does not
  silently switch a product off.
- Development happens on a separate machine. Code reaches the work laptop through GitHub, and
  facts about the live servers come back as pasted probe or smoke-test output.

### Connection layer

- `api.py` defines `ApiClient`, a `Protocol` with `get`, `get_text` (plain-text resources
  such as raw diffs), `query` (reads that must be POSTed), `write` and `aclose`, plus
  `base_url` and `dry_run`. Product modules never make HTTP calls themselves.
- `HttpAdapter` in `transport.py` is what every adapter shares: TLS trust, extra cookies,
  dry-run, logging and plain-language connectivity errors. It never follows a redirect and
  keeps no cookie a server sets. A subclass implements `_interpret` to say how its kind of
  server reports results and failures: `AtlassianClient` in `http.py` for Atlassian Data
  Center, `ServiceNowClient` in `servicenow/http.py` for ServiceNow. One adapter is built
  per product.
- **A product that signs in interactively offers `Product.login`**, which the generic
  `login <product>` verb calls. ServiceNow's opens the installed browser on a temporary
  profile, waits for the user to sign in, reads the instance's cookies over a DevTools
  pipe, and stores them with the page's request token in the keychain (see ROADMAP.md,
  ServiceNow, for what was probed and what was rejected). Its credential provider reads
  the keychain before every request, so a running server picks up a new sign-in.
- **The browser driver is shared** (`browser.py`): a throwaway profile, the DevTools
  protocol on a private pipe, and browser-level commands only (never attaching to a
  page). ServiceNow reads cookies with it; Microsoft reads tab addresses with it
  (`Target.getTargets`, plus `Target.setDiscoverTargets` events so an address a tab only
  passes through is still seen).
- **`login microsoft` is the same model for an OAuth client** (`microsoft/signin.py`).
  The Outlook products share one sign-in, so `login microsoft` (also accepted as
  `login outlook_mail`/`outlook_calendar`) reads the `[microsoft]` table rather than a
  product's `Product.login`. It opens the browser on the authorize page of the configured
  single-page client with a PKCE S256 challenge and a random `state`, watches the tab
  list for the client's `redirect_uri` carrying that `state`, takes the `code` from the
  fragment, and redeems it in Python with the verifier and the SPA `Origin` header (the
  same cross-origin shape the refresh needs). The refresh token that comes back is stored
  exactly as a seeded one. The verifier never leaves the process, so the code in the tab
  is useless to the redirect page or anyone else; a redirect with another `state` is
  ignored, and one carrying `error` fails the sign-in with the first line of its
  description. Chosen over reading MSAL's cache from the page (needs page attach, refused
  on the work laptop, and the cache may be encrypted) and over intercepting the token
  response (needs `Fetch`/`Network` on the page). `login-microsoft` (paste a captured
  token) remains as the fallback.
- **Status tools describe the credential as it is now.** `Credential.source` is a string
  fixed when the server starts, which goes stale once a new sign-in can replace the
  credential underneath a running server. Such a credential also offers
  `Credential.describe`, an async callable that `ToolContext.current_source()` uses, so
  `outlook_*_status` and `servicenow_status` read the keychain when called. The Microsoft
  session keeps `signed_in_at` (the interactive sign-in, unchanged by rotation) apart
  from `obtained_at` (last rotation), and the optional `sign_in_hours` from `[microsoft]`,
  so the status can say "usable until about …" and when the access token in use expires.
- **A renewal never overwrites a newer sign-in.** After redeeming, the provider re-reads
  the keychain and writes the rotated token back only if the stored one is still the one
  it redeemed; otherwise `login microsoft` stored a new sign-in meanwhile, and that wins.
- **An adapter is never handed a secret.** `credentials.py` defines `HeaderProvider`, an
  async callable returning the authentication *headers*, and the adapter asks it before
  every request. A credential that changes while the server runs (a refreshed OAuth token,
  a new browser session) is therefore picked up without reconnecting, and a provider that
  can no longer renew raises a plain-language error before anything is sent. It returns
  headers rather than a token string because not every service uses one bearer header: a
  ServiceNow browser session is a cookie plus a CSRF header.
- Each `Product` names a `resolve_credential` (a `CredentialResolver`), which finds the
  credential under the product's token name when it is connected and reports where it came
  from. The default, `personal_access_token`, wraps the token from the environment, env
  file or keychain in `bearer_token`, so the Atlassian products behave exactly as before.
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
  override. The top-level `base_url` is optional when every product (or its group) names its
  own.
- **Groups (`[group.<name>]`) sit between the top level and a product.** A group holds the
  same connection fields (`base_url`, `ca_bundle`, `timeout_seconds`, `[group.<name>.cookies]`)
  and a product joins one with `group = "<name>"`. Each value resolves product → group →
  top level, so products on one host — the Atlassian products on the internal host, the two
  Outlook products on the Outlook REST host — share a base URL and banner cookies in one
  place without the top level having to belong to any one of them. This matters because a
  user may run only the Microsoft (or a future) tools and never touch Atlassian: the top
  level stays neutral, and the DoD banner cookie lives in `[group.atlassian]` where it is
  only sent to that host. A product's `group` must name a defined group, or it is a config
  error (so a typo is caught). Cookies replace rather than merge at every level (a product's
  own `[x.cookies]` wins, then its group's, then the top level's), so a cookie meant for one
  host is never sent to another. `--cookie` overrides are added wherever cookies are defined
  (top level, each group, each product).
- `base_url` is scheme and host, plus a path only when a shared prefix applies. Each
  product's sub-path goes in its own `path` (`""` for the host root).
- `dry_run` (default `true`), `log_level` (default `info`) and per-product
  `default_max_results` (25) are config keys. Results are capped at 100 per call.
- `token_cmd` and `token_env_file` resolve like the connection values (product, group, top
  level), but each separately, so a product can take its command from the top and its env
  file from its group. `--token-env-file` overrides the env file at every level.
- Paths (`ca_bundle`, `token_env_file`) and a `token_cmd` string expand `~` and environment
  variables (`$HOME`, `${HOME}`); a `token_cmd` list does not (see Authentication). An
  unset variable is a config error naming the key, so a typo is caught at load rather than
  surfacing as a missing file.
- `{product}` in `token_cmd` or `token_env_file` becomes the token name at lookup. It is a
  literal 9-character match, not a template language, so braces a program needs (awk's
  `{ print $2 }`, jq filters) are left alone. A templated `token_env_file` that does not
  exist for one product is skipped (that product's token is elsewhere); a fixed path that
  does not exist is an error, since it is most likely a typo. A file that exists but
  cannot be read is an error either way.
- `[microsoft]` is a sign-in table, not a product: it holds the OAuth client for
  `login microsoft`. The Outlook products take their host from their own tables or group.
- Jira presets live under `[jira.presets]`. Bitbucket's `default_project` lives under
  `[bitbucket]`.
- CLI parsing uses cyclopts, which also brings in `rich` (now listed directly). Command-line
  flags override the config.

### Authentication

- Personal access tokens or HTTP access tokens, sent as `Authorization: Bearer`. Jira,
  Bitbucket and Confluence each have their own token. ServiceNow and Outlook sign in
  through the browser instead (see Connection layer).
- Token sources, first match wins: the environment variable (`JIRA_TOKEN`), then
  `token_cmd`, then an env file, then the system keychain (through `keyring`, written by
  `set-token`). Explicit before implicit: a value set for this run beats configuration,
  and configuration beats what was stored earlier.
- `token_cmd` runs without a shell, with no input and a 30-second limit. A string is read
  the way a shell reads a command line: `~` and `$VAR` expand outside quotes, `$VAR` also
  inside double quotes, nothing inside single quotes, and a backslash escapes; expanded
  values are re-quoted so a value with spaces stays one word; then it is `shlex`-split.
  This came from a live config: `awk -F= '{ print $2 }' ${HOME}/...` failed when every word
  was expanded after splitting, because the quotes that protect `$2` were already gone. A
  TOML list is taken exactly as written, the escape hatch when a `$` must reach the
  program. Its output must be one line and is never logged; a failure reports the
  exit status and the first line of its error output. `{product}` in any word becomes the
  token name, so one top-level command (say, `sops --extract '["{product}"]'`) serves every
  product. Chosen over a shell string, which would make quoting and injection the user's
  problem for no gain: `sh -c` is there for the rare pipe.
- When no source has a token, the error lists where it looked and every way to supply one.
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
- **Dry-run is per product (2026-11-19):** a product's table may set `dry_run`, which wins
  over the top level (`Settings.dry_run_for`); `--dry-run` / `--no-dry-run` clear every
  product's own value for that run. Groups do not carry it: a group is shared connection
  details, and whether writes go out is a decision about each product. Asked for so the
  Obsidian vault can take writes while Jira and the rest stay blocked.
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
- **Jira Software's fields** (Epic Name, Epic Link, Story Points) are custom fields whose
  ids differ on every server. They are recognised by the custom type key in the screen
  metadata (`...:gh-epic-label`, `...:gh-epic-link`); Story Points is a plain number
  field, so it is recognised by name ("Story Points" or "Story Point Estimate") on a custom
  field. The tools take `epic_name`, `epic` (an issue key; "none" clears it) and
  `story_points` (a negative number clears it) and check each against the screen like any
  other field. A create that lacks only these says which parameter to pass. For reading,
  the ids come from `rest/api/2/field`, asked once per server run; a server without them
  reads issues as before. Sprints are not set here: that needs the board API to turn a
  sprint name into an id.
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
- `bitbucket_create_branch` (`bitbucket/branches.py`) builds the name Bitbucket's own
  dialog proposes: prefix, issue key, and the summary lower-cased with dashes (apostrophes
  dropped, cut at a word within 40 characters). Prefixes and the development and
  production branches come from `rest/branch-utils/latest/.../branchmodel`, which is not
  in the published OpenAPI spec but answers on 10.2.8; if it does not answer, Bitbucket's
  default prefixes are used and the result says so. A type the model has switched off is
  refused. `custom` has no prefix and uses the given name as written. The summary comes from an `IssueTracker`
  peer; with Jira on, an issue Jira does not return refuses the branch (a likely typo),
  while a Jira error only drops the summary. The start point defaults to the model's
  development branch (production for a hotfix), then the repository's default branch.
  The name is validated against git's ref rules and checked not to exist, in dry run too.
  The branch is created with `POST .../branches` (`name`, `startPoint`).
- `ci = false` under `[bitbucket]` is for a server no CI or CD system reports to. It
  leaves `bitbucket_get_commit_builds`, `bitbucket_list_required_builds` and
  `bitbucket_get_deployment` unregistered, so an agent does not spend calls on them, and
  `bitbucket_get_pull_request` skips its build request (`builds_available` is false).
- The user's slug: `applinks/whoami` answers nothing for a bearer token on 10.2.8, but
  every authenticated response carries `X-AUSERNAME`. The adapter keeps it as
  `ApiClient.username`; the Bitbucket client uses it first and asks the servlet only when
  no response has been seen yet. Confirmed on the real server.

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

### Confluence specifics

- REST API `rest/api` under the Confluence sub-path (default `/confluence`), checked
  against the Confluence 10.2.20 OpenAPI spec. Token name `confluence`
  (`CONFLUENCE_TOKEN`).
- **Bodies are Markdown, converted here.** The 10.2 API converts only from storage format
  (and from editor format to storage); wiki markup is no longer accepted. Markdown is what
  agents write most reliably, so `confluence/markdown.py` renders CommonMark plus tables
  and strikethrough with `markdown-it-py` as XHTML, which is valid storage format. Fenced
  and indented code become the `code` macro (common language names mapped to the macro's,
  others shown without highlighting; the code sits in CDATA, with `]]>` split). Images
  become `ac:image` with `ri:url`. Raw HTML is escaped, not passed through, so a page
  cannot be broken by stray markup; `body_format="storage"` sends storage format as
  written, for macros, layouts and mentions.
- **Pages are read in storage format**, so a page can be edited and sent back without a
  lossy round trip. The body is cut at `max_chars` (default 20,000, at most 500,000) with
  `truncated` and `body_chars`, and the result tells the agent never to send a truncated
  body back in replace mode.
- **Edits are optimistic.** `confluence_update_page` takes the version the agent read. The
  page is fetched again before saving; if its version differs, nothing is sent and the
  agent is told to read it again. The PUT carries version + 1, which Confluence also
  checks. `mode` is `replace`, `append` or `prepend`; the last two splice storage format
  onto the current body here, so adding a section never sends the whole page through the
  model. A body left out keeps the current one, so a title change or move alone works.
  A page over 500,000 characters is refused for editing.
- **Find and replace** (`replacements` on `confluence_update_page`, instead of `body`) is
  for pages whose layout and macros must survive, such as a template to fill in. Each
  pair is an exact piece of the storage-format body and its replacement, spliced here so
  the page never goes through the model whole. Every `find` must occur exactly once in
  the current body (all misses and duplicates are reported together), pieces may not
  overlap, and nothing may be a no-op. If the body was well-formed XML before (checked
  with the `ac:` and `ri:` prefixes bound and HTML entities made numeric), it must be
  after, which catches a bare `&` or half an element. Replacement text is storage format,
  not Markdown, because Markdown would wrap inline text in `<p>`.
- A page read asks for `operations` and reports `can_edit` (true when `update` is among
  them, None when the server sends none). An edit of a page with `can_edit` false is
  refused before anything is sent, in dry run too, so a dry run cannot look like it would
  have worked. Confirmed on 10.2.19 against a page with no Edit link.
- `confluence_create_page` checks the title is free in the space first (also in dry run),
  because Confluence refuses a second page with the same title, and the error then names
  the existing page's id.
- `confluence_status` reports the personal space: `space/~<username>`, or none on 404.
  `default_space` (config) and `confluence_set_default_space` (session, checked against
  the server) work like Bitbucket's default project.
- Both writes go through the shared `RepeatGuard`. Labels, attachments, comments, blog
  posts and deleting pages are not offered yet.

### Outlook REST specifics (the Microsoft stopgap)

- Two read-only products, `outlook_calendar` and `outlook_mail`, over the Outlook REST
  API v2.0 (`outlook-dod.office365.us/api/v2.0`). They are the stopgap described under
  Roadmap and ROADMAP.md: the real target is Microsoft Graph behind MSAL once admin
  consent lands, so these are kept thin and swappable. **GET only**, by discipline: the
  harvested token carries `Mail.ReadWrite`/`Calendars.ReadWrite` (no read-only scope
  exists to request), so the adapter stays read-only the way writes stay behind `dry_run`.
- **Two products, one token.** Both set `token_name = "microsoft"` and
  `resolve_credential = microsoft_token`, so they share one keychain refresh-token
  session. Each is switched on by its own table (`[outlook_calendar]`, `[outlook_mail]`),
  so a user can enable just one; they share one host through a `[group.microsoft]` table
  (see Configuration) so the Outlook REST base URL is written once; and the OAuth parameters
  stay in the separate `[microsoft]` table that `login microsoft` reads (never a served
  product). This mirrors the planned GitLab split (`gitlab_wiki`/`gitlab_scm` sharing a
  `gitlab` token). Chosen over one
  `microsoft` product with many tools for independent enablement and cleaner tool
  grouping.
- **Products sharing a token share one resolved credential.** `cli._open_server` now
  resolves each distinct token name once and hands the same `HeaderProvider` to every
  product that shares it. Without this, two `MicrosoftCredential` instances would each
  hold their own access-token cache and lock and could race to redeem — and so rotate —
  the one refresh token on a cold cache. One shared provider means one lock, one cache,
  one orderly rotation. (Two GitLab products would benefit the same way.)
- **`OutlookClient`** is the adapter (`transport.py`'s `HttpAdapter` plus an Outlook
  `_interpret`). It is a public-cloud host, so there is no consent banner, no cookie
  session and no sign-in redirect to read: a 401 means the minted token was refused,
  which for the stopgap means the refresh token can no longer be redeemed, so the message
  says to run `login microsoft` again. Errors are read from Outlook's
  `{"error": {"code", "message"}}` shape;
  429 is called out as rate limiting with "do not loop".
- **Two `Prefer` headers on every request:** `outlook.body-content-type="text"` so event
  and message bodies arrive as plain text (smaller, no markup for the model), and
  `outlook.timezone="UTC"` so event times are normalized rather than mailbox-local.
- **Calendar.** `outlook_calendar_list_events` uses `calendarview` (a start/end range,
  recurrences expanded) rather than `/me/events` (series masters), because "what is on my
  calendar" wants the actual occurrences. The range defaults to now through seven days
  out; a bare date is widened to the start or end of that day. Bodies are truncated like
  Confluence's (default 20,000, ceiling 500,000, with `body_chars`/`truncated`).
- **Mail.** `outlook_mail_list_messages` is newest-first with an optional unread filter;
  `outlook_mail_search_messages` uses Outlook's own `$search` (which cannot be combined
  with `$orderby`). Bodies truncate the same way.
- **Internet headers are a separate tool.** `outlook_mail_get_message_headers` fetches
  `InternetMessageHeaders` with `$select` (Outlook returns them only when selected, and
  selecting them alone would strip the other fields a normal read needs), and returns the
  headers in order with duplicates kept. For many messages at once,
  `outlook_mail_query_messages` with `select=Subject,InternetMessageHeaders` returns them
  for a whole page (confirmed live). It is for triage and automation: Authentication-Results
  (SPF/DKIM/DMARC), the Received chain, Return-Path/Reply-To, and List-Id/List-Unsubscribe
  for mailing lists.
- **OData query tools are the escape hatch.** `outlook_mail_query_messages` and
  `outlook_calendar_query_events` (`microsoft/odata.py`) pass `$filter`, `$select`,
  `$orderby`, `$expand`, `$top`, `$skip` and `$count` to the collection as written (GET
  only), on `/me/messages` or one mail folder, and on `/me/events` or one calendar. They
  exist for what the fixed tools cannot ask, chiefly finding a message by
  `InternetMessageId` and filtering on any filterable property. Decisions:
  - Options are not parsed or rewritten here; the API validates them and its error
    message (an `InefficientFilter`, an unknown property) reaches the agent unchanged.
    The tool docstrings carry the rules the agent most often trips on: PascalCase names
    and single-quoted strings, plus what to do on an `InefficientFilter`. (Graph documents
    that `$orderby` properties must lead the `$filter`; on the DoD Outlook REST a filter
    and a sort on different properties were accepted, so that is advice, not a rule.)
  - Without `$select` a compact default is sent (no body, no headers), so a broad query
    cannot pull every body by accident; any string longer than `max_chars` is cut wherever
    it is in an item, and `values_truncated` says how many were.
  - Items keep the API's own property names (unlike the fixed tools' snake_case models),
    so a name seen in a result can go straight into the next filter or select.
    `@odata.*` annotations are dropped.
  - Paging is `$skip`: the result gives `next_skip` when `@odata.nextLink` was present,
    rather than handing the agent an opaque next link to follow.
  - Events are read from `/events`, so recurring meetings are series masters;
    "what is on my calendar" stays with `calendarview` in `outlook_calendar_list_events`.
  - Header *contents* cannot be filtered on: `InternetMessageHeaders` is not a filterable
    property. Finding mailing-list or failed-DMARC mail means paging with the headers
    selected and reading them, which works in one request per page.
  - Confirmed on the DoD tenant (2026-11-18): an `InternetMessageId` filter finds the
    message; option property names are case-insensitive (Graph's camelCase works), while
    results always come back PascalCase; a collection `$select` of
    `InternetMessageHeaders` returns them for every item, and the whole header set, not
    only the well-known names; `IsRead` filter with `ReceivedDateTime` sort is accepted
    (2026-11-19).
- **Outlook REST v2.0 answers in PascalCase** (`Subject`, `ReceivedDateTime`,
  `EmailAddress`); the fixed tools normalize that to the snake_case models so the agent
  sees one vocabulary. OData collections are `{"value": [...]}` with `@odata.nextLink` for more.
- **The meeting key is `iCalUId`, in camelCase**, the one event property the REST API does
  not name in PascalCase (seen live 2026-11-19; `$select` accepts either spelling, but
  results carry `iCalUId`). Reading `ICalUId` found nothing, so the first live sync made no
  meeting notes; the test data had been written with the wrong spelling too.

### Obsidian specifics

- `obsidian/` holds `settings.py`, `frontmatter.py` (splitting and parsing a note's
  properties), `vault.py` (the `Vault` client), `models.py`, `tools.py` and `product.py`,
  the first `LocalProduct`.
- **Every call looks at the files again**, so a note changed in Obsidian is seen at once,
  but a note's properties are parsed once and kept until its modification time or size
  changes, and the task tools do not parse a note whose properties cannot contain the task
  tag as a word. Parsing YAML is the cost (about 1 ms a note with ruamel's pure-Python
  loader; its C extension is not available on Python 3.14 here), not reading files.
  Measured 2026-11-19 on a 5,000-note vault with 1,000 tasks: the first task listing
  0.84 s, later ones 0.11 s; the first `obsidian_status` 3.0 s (it parses every note to
  report broken properties), later ones 0.19 s. Before the cache and the tag check, every
  call took about 5 s.
- **A task note is a note whose `tags` hold `task_tag`**, which is how TaskNotes
  identifies tasks by default, found anywhere in the vault rather than only in
  `tasks_folder` (the user may have moved some). TaskNotes can instead identify tasks by
  a property; that setting is not read yet. Finished means a status in `done_statuses`.
- **Properties are read with ruamel's safe loader** and turned into JSON values; dates
  become ISO 8601 text. Broken YAML, or properties that are not a mapping, give an empty
  set of properties and a reason (listed by `obsidian_status`), never a failed call. Step
  3 will write with ruamel's round-trip mode so the user's ordering and comments survive.
- **Dot folders are never read** (`.obsidian`, `.trash`, `.git`), links are not followed
  while walking, and `obsidian_get_note` refuses any path that resolves outside the vault
  (`..`, an absolute path elsewhere, a symbolic link out). A bare name, as a `[[link]]`
  gives it, is looked up by file name across the vault and refused when ambiguous.
- **`open` does not check the vault.** A missing vault is reported by each tool and by
  `doctor`, so the other products keep working while the path is fixed; the server would
  otherwise fail to start over one wrong line.
- **Dated notes (2026-11-19).** The user keeps daily notes at `Journal/YYYY/MM/YYYY-MM-DD`
  and wants meetings at `Meetings/YYYY/MM/YYYY-MM-DD <title>`. Both are a folder plus a
  date format in moment.js syntax, the syntax Obsidian's Daily notes plugin uses, so a
  format can be copied between the two; the folder is kept apart because a folder
  name's letters (the `M` of `Meetings`) would read as tokens. A subset of tokens is
  supported (`paths.py`); a time or locale token is refused at config load. Meeting titles
  lose the characters Obsidian refuses in file names. **Daily notes follow
  `.obsidian/daily-notes.json`** unless the config overrides it, and `doctor` fails when
  the two differ: a daily note written elsewhere is a second note for the same day,
  which is the duplication this work exists to prevent. Meeting notes are found by key,
  not by path, so a meeting that moves or is renamed keeps its note; the path only
  decides where a new note goes.
- **Time zone (2026-11-19).** "Today" is computed from an aware current time in
  `[obsidian] time_zone`, else this computer's zone, found by `timezones.py` on every use
  (`TZ`, then the `/etc/localtime` link's `zoneinfo/<name>`, then the current fixed
  offset). Asked for by the user after spotting that `datetime.now(None)` is naive. Looked
  up per call so a long-running server follows a laptop between zones and across DST. It
  lives in the shared core because the calendar sync (step 5) needs the same answer to
  put a UTC meeting on the right local day. `tzlocal` would also do this; three lines of
  lookup did not justify a dependency.
- **Writes (step 3, 2026-11-19)** are `obsidian_create_task`, `obsidian_update_task` and
  `obsidian_write_section`, in `edits.py` (the rules) over `notes.py` (the text and the
  file). A new task with an existing key is refused and returns that task; one whose
  title scores 0.8 or more against an open task is held back until the agent passes
  `allow_similar` after asking the user, a higher bar than `obsidian_find_task`'s 0.6
  because it blocks. On a task with a source key other than `uid`, `title`, `status`,
  `priority` and `due` belong to the source and are refused; the user changes them there
  and the sync brings them back. Properties are edited with ruamel's round-trip mode
  (order, comments and quoting kept; an ISO day is stored as a date so it is written
  unquoted, as TaskNotes writes it). A section is the text between two HTML comments
  (`<!-- productivity-agent-tools:<name>:start -->` / `:end -->`), invisible in reading
  view; damaged markers are refused rather than guessed at. Files are written to a
  dot-named temporary file and renamed; the modification time and size read are checked
  just before, and a note that changed is left alone (`ChangedOnDiskError`). One lock in
  the tools serializes writes, so two simultaneous identical calls cannot both create a
  task. A new daily note starts from the template named in `.obsidian/daily-notes.json`,
  with the core Templates variables filled; Templater syntax is left for Templater.
- **Sync (step 5, 2026-11-19)** is `obsidian_sync` (`sync.py`): fetch from every
  `TaskSource` and `MeetingSource` peer (outside the write lock), then make or update
  notes under it. Notes are indexed by key (`Vault.notes_by_key`, any note, task or not).
  Ownership is per field: a task's `title`, `status`, `priority` (only when the source has
  priorities, so Bitbucket does not erase the user's), `due`, `completedDate`,
  `source_status` and `url`; a meeting's `title`, `date`, `start`, `end`, `organizer`,
  `location`, `join`, `url`, `response`, `cancelled` and `series_id`. A value of None
  removes the property, so a due date dropped in Jira goes. Vault tasks that are open
  but no longer listed are looked up with `tasks_by_key` and closed when finished; ones
  the source cannot find are left alone. No new note for a finished task, or for a
  meeting that is cancelled, all-day, declined or shown as free (counted as `ignored`).
  A meeting that moves keeps its note and path. Two notes with one key are reported and
  not touched. **Meetings that leave the calendar (2026-11-19, from the live run):** a
  cancelled, declined or deleted meeting drops out of `calendarview` instead of coming
  back with `IsCancelled`, so its note was never revisited. Now each meeting note dated in
  the window whose key did not come back is looked for in a second `calendarview` a week
  either side of its day (`LOOKAROUND_DAYS`): found, it moved; not found, `cancelled: true`.
  Chosen over a lookup by `iCalUId` because an occurrence of a series is not in
  `/me/events` and its key differs from the series', while `calendarview` was already
  proven live to return occurrences with their keys. Nothing is marked when any calendar
  failed to answer. `my_meetings` now follows pages (up to 1,000 events).
  A new synced task whose title scores 0.8+ against a hand-made task is made
  anyway (the key is authoritative) and the report names the likely twin. One source
  failing is reported and the rest sync. Running it twice changes nothing the second
  time; a test holds it to that.
- **Recipes (step 6, 2026-11-19)** live in `contrib/goose/` as plain instructions over
  the tools, not code: the rules that matter (no duplicates, source-owned fields,
  sections) are enforced by the tools, so a recipe only orders the steps and says when to
  ask the user. They omit an `extensions` block because an explicit one would have to
  name this checkout's absolute path; whether Goose then offers the user's enabled
  extensions is not stated in its docs and is a laptop check. A note can be named by key
  (`field:value` for any of `KEY_FIELDS`) so a recipe can reach a meeting's note from its
  calendar event without rebuilding the file name.
- **Template folders are skipped** (2026-11-19, from the first live run): the folders
  named in `.obsidian/templates.json` (`folder`) and Templater's
  `plugins/templater-obsidian/data.json` (`templates_folder`). A Templater template's
  `<% %>` frontmatter was being reported as broken YAML, and a template tagged `#task`
  would have been listed as a task.
- **Synced task file names (2026-11-19, the user's choice):** `<key> <slug>.md`, the slug
  lower-cased and hyphen-joined, cut to `task_title_length` (25) at a word, so names stay
  short and sort by key; a key's `/` and `#` become hyphens (`PROJ-web-42`). Tasks with
  only a `uid` keep title names. Names never identify a note, so changing the setting
  only affects notes made afterwards.
- **Templates and buttons (2026-11-19).** `[obsidian] meeting_template` names a note that
  new synced meeting notes start from (core variables filled by `templates.fill`, the
  sync's properties merged into its frontmatter); the built-in body now has the `prep`
  markers too. `contrib/obsidian/` holds a daily, a synced-meeting and an ad hoc meeting
  template whose marker pairs match the recipes (a test holds them to it). For running
  things, the user wanted whatever works rather than a given plugin, so nothing depends
  on one: `contrib/macos/sync.sh` (sync plus a notification) and `run-recipe.sh` (a
  recipe in a new iTerm2 or Terminal window) can be called by any button, and a launchd
  agent runs the sync every 30 minutes. Chosen over the Shell Commands plugin (the
  user found it unmaintained) and over cron (launchd is macOS's own and runs at login).
- **Time blocks (2026-11-19, the user's rule):** a task's `block_minutes` is computed in
  code, not by the agent: its estimate up to `max_timeblock_minutes` (50), else
  `timeblock_minutes` (25, a pomodoro); a due date does not change the length (the user
  corrected a first version that gave due-only tasks the longest block). The estimate is
  TaskNotes' `timeEstimate`, else `source_estimate`, which the
  sync now owns from Jira's `timeestimate` (remaining) or `timeoriginalestimate`, rounded
  up to minutes; a source without estimates leaves it alone, as with priority. Names
  carry their unit like the rest of the config (`timeout_seconds`, `sync_days_ahead`).
  Each task also gets `break_minutes` to leave after its block: `long_break_minutes` (10)
  after a block longer than `timeblock_minutes`, else `break_minutes` (5); agreed with
  the user, including that a 30- or 40-minute block counts as long.
  Plan-day now ranks every open task (carried over, due soon, in progress, high
  priority, current sprint from the `current-sprint` preset, reviews) instead of leaning
  on due dates, after the first real plan came out thin.
- **A status changed in Obsidian is kept (2026-11-20, from the live run):** the user marked
  a Jira task done in TaskNotes, and the next sync put Jira's "in progress" back. Now, if
  the note's `source_status` (the source's own status name, written only by the sync)
  equals the source's current one, the source has not moved since the last sync, so a
  `status` that differs was changed here: it and `completedDate` are left alone and the
  report flags the task; other owned fields still update. When the source moves, it wins.
  The user suggested `source_status` over a new `synced_status` property: no extra field,
  and notes made earlier are covered at once. `dateModified` was considered and rejected
  as the signal: it changes on any edit (scheduling, the sync's own writes), not only a
  status change. The one ambiguous case, both changing between two syncs, goes to the
  source.
- **Title similarity** is `difflib.SequenceMatcher` over lower-cased words, 0.6 by
  default. It only proposes; the agent is told a key match is the same task and a title
  match is for the user to judge.

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
- `set-token [name]`: hidden prompt, stored in the system keychain. With no name it lists
  the tokens (one entry per token name, so the Outlook pair shows once) and asks; without a
  terminal it fails with the list instead. `set-token microsoft` stores an Outlook refresh
  token captured by hand (`--from-response`, `--verify`); a product that only signs in
  through the browser points at `login`.
- `login <product>`: browser sign-in. `login microsoft` (or either Outlook product) uses the
  `[microsoft]` table; other products use their own `Product.login`.
- `login-microsoft`: deprecated, hidden alias of `set-token microsoft`; prints a note.
- `export-ca` (macOS): exports the Keychain's trusted roots to a PEM file (default
  `~/ca-bundle.pem`).
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
- **More Confluence:** labels, attachments (for an "About me" photo, say), page comments,
  blog posts, and `@` mentions or user macros written from Markdown.
- **Other custom Jira fields:** reading and setting `customfield_*` values beyond the agile
  ones already handled (epic name, epic link, story points), whose shapes vary by field
  type.
- **More Bitbucket writes:** comments on a file or line of the diff, resolving tasks, and
  editing or deleting the user's own comments.
- **Microsoft Graph (Microsoft 365 calendar and mail):** a new adapter implementing
  `ApiClient` with its own OAuth sign-in, plus one or more product modules. This is the
  reason per-product hosts and the adapter protocol exist. The design was settled in a
  short interview on 2026-11-13/14, after a read-only sign-in probe
  (`scripts/ms_graph_probe.py`) confirmed against the real tenant that a public client can
  authenticate and is not Conditional-Access blocked; the tenant requires admin consent for
  the delegated scopes, so the build waits on that (see ROADMAP.md for status and ordering).
  Decisions:
  - **Sign-in is OAuth, not a static token, so the adapter protocol gains a token
    *provider*.** The adapter is handed an async provider of fresh authentication headers,
    rather than a `token: str` fixed before connecting; the Atlassian adapter wraps its
    personal access token in a trivial provider. **Built (2026-11-14)** as the
    `HeaderProvider` seam described under "Overall shape", returning headers rather than a
    token string so ServiceNow can share it. This is the only change MS Graph forces on
    shared code. Everything else is reused: per-product `base_url`, the `dry_run` rule in
    `write`, and the plain-language error reader.
  - **MSAL (`msal`) runs the flow**, the silent refresh and the token cache. The cache is
    persisted through `keyring` under the shared service, token name `microsoft`, so
    interactive sign-in happens once and access tokens refresh silently after.
  - **Authorization code + PKCE on a loopback redirect is the primary interactive flow.** It
    opens the system browser, so smart card, MFA and managed-device Conditional Access work
    exactly as they do for a browser login. **Device code** is an opt-in fallback for
    headless use only; Microsoft blocks it by default in many tenants (rolling out from
    February 2025), so it cannot be the sole flow.
  - **`client_id` and `tenant` are configurable**, defaulting to Microsoft's first-party
    public client "Microsoft Graph Command Line Tools"
    (`14d82eec-204b-4c2f-b7e8-296a70dab67e`), which needs no app registration of one's own.
    A tenant that registers its own app changes one config line.
  - **Its own host, no VPN, no banner:** `base_url = "https://graph.microsoft.com/v1.0"`,
    system trust store, no cookies. Unlike the Atlassian products this is a public cloud
    host reached off the internal network, so the consent-cookie machinery does not apply.
  - **Products share one `microsoft` token** (the `token_name` mechanism, as planned for the
    two GitLab products), and the scope set requested is the union of the configured
    products. Calendar first, then mail, then others.
  - **Read-only first, writes later behind `dry_run`.** Initial scopes are delegated and
    read-only (`Calendars.Read`, `Mail.Read`, `User.Read`, plus `offline_access` for
    refresh); write scopes (`Calendars.ReadWrite`, `Mail.Send`) are a later, larger consent.
  - **Data handling:** the content read (calendar entries, mail) is passed to the agent's
    model, so access is kept delegated (the user's own data only), read-only to start, and
    the admin-consent request names the AI use plainly rather than hiding it.
- **OpenTelemetry tracing** of httpx requests and responses, alongside the structlog
  logging.
- **Obsidian vault (`obsidian`), planned 2026-11-19.** A daily plan, task notes for what
  the user is responsible for, and meeting notes, kept in the user's own Obsidian vault
  without the same task or meeting appearing twice from one day to the next. Decisions:
  - **The vault is a `LocalProduct`** that works on the files directly, so it needs
    neither Obsidian running nor a plugin's HTTP API, a morning sync can run from cron,
    and the tests use a vault in `tmp_path`. (The Local REST API community plugin would
    have fit `RemoteProduct` unchanged, but only while Obsidian is open.)
  - **Deduplication is code, not prompting.** Every synced note carries a stable key in its
    frontmatter (`jira: ABC-123`, `bitbucket: PROJ/repo#42`, `ical_uid`, `series_id`;
    `uid` for a task made in the vault), and notes are found by key, never by file name or
    title, so a renamed issue or moved meeting updates its note. The agent gets typed tools
    (`obsidian_find_task`, `obsidian_create_task` that refuses an existing key and returns
    near matches, `obsidian_schedule_task`, `obsidian_write_daily_section` that replaces
    the text between markers), not raw file writes.
  - **Each frontmatter field has one owner.** Sync owns what the source owns (`title`,
    `status`, `priority`, `due`, the source's status); the user and agent own the rest
    (`scheduled`, `contexts`, `timeEstimate`, the body). Sync never writes a field it does
    not own and never deletes a note: a resolved issue becomes `status: done`.
  - **TaskNotes' standard field names are kept**; the keys above are extra frontmatter that
    TaskNotes ignores and keeps.
  - **Daily notes link, never copy.** Planning a day sets `scheduled` on task notes and the
    daily note queries them, so unfinished work carries over by staying open.
  - **Sync uses capabilities, not integrations.** An `Integration` names fixed products, so
    syncing would need one per source (`jira_obsidian`, `calendar_obsidian`, ...). Instead
    `capabilities.py` gains `TaskSource` (open tasks with a stable key) and `MeetingSource`
    (meeting occurrences with `ical_uid` and series id); Jira, Bitbucket (pull requests
    awaiting review) and the Outlook calendar implement them, and `obsidian_sync` collects
    every peer offering each, through a new `ToolContext.peers_of(kind)`. A later source
    (ServiceNow requests, GitLab) joins by implementing the protocol. This also answers the
    open question about code hosts in favour of capabilities.
  - **No new CLI command:** `call obsidian_sync` runs the sync with no agent and no model
    credits; the agent's recipes (plan the day, prepare a meeting, wrap up) use the same
    tools.
  - **Safe beside a running Obsidian:** writes go to a temporary file then a rename, and a
    note whose modification time changed after it was read is skipped and reported, not
    overwritten.
  - **Data handling:** the vault's contents reach the agent's model like any other tool
    result, so the same model-approval question applies.

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
  Custom fields other than Jira Software's own are deferred (see Roadmap).
- **Bitbucket merges, declines, and deleting or moving branches.** These change code or
  end a review, so they are left to the user in the browser. Creating a branch is offered,
  because it changes nothing that exists. Comment anchors and task resolution are
  deferred (see Roadmap).
- **Deleting or moving Confluence pages between spaces.** Left to the browser.
- **Microsoft 365.** Deferred (see Roadmap).
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
