# productivity-agent-tools

An MCP server that gives an AI agent (Goose first, Claude Code later) tools for
daily work. Each product is switched on by its own table in the config:

| Product | Tools | Sign-in |
|---|---|---|
| Jira (Data Center) | reads, plus comments, transitions, worklogs, edits, new issues, links | personal access token |
| Bitbucket (Data Center) | reads, plus branches, pull-request comments and reviews | personal access token |
| Confluence (Data Center) | reads and search, plus creating and editing pages | personal access token |
| ServiceNow | status (more to come) | browser sign-in (`login servicenow`) |
| Outlook mail and calendar | reads only | browser sign-in (`login microsoft`) |

Writes are blocked until you turn `dry_run` off.

## Quickstart

You need Python 3.14+, [uv](https://docs.astral.sh/uv/), a way to sign in to each
product you turn on (see the table above), and your VPN connected if your servers
need it. These steps set up Jira; the other products are under [Setup](#setup).

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

3. Store your Jira token (hidden prompt) and check the connection:

   ```bash
   uv run productivity-agent-tools set-token jira
   uv run productivity-agent-tools doctor
   ```

4. Add the server to Goose, then restart Goose:

   ```bash
   uv run productivity-agent-tools configure-agent
   ```

Writes are blocked until you turn `dry_run` off. The sections below explain each
step and what to do when one fails.

## How it authenticates

- **Jira, Bitbucket and Confluence** send your **personal access token** as
  `Authorization: Bearer ...`; each product has its own (see
  [Token sources](#token-sources)).
- **ServiceNow** uses the browser session from `login servicenow`.
- **Outlook** uses a refresh token from `login microsoft`, renewing short-lived
  access tokens from it as needed.

None of them asks for or stores a password. Whatever they keep is in the system
keychain (the macOS Keychain, or the Secret Service on Linux).

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

A product is switched on by having its table in the config: `[jira]`,
`[bitbucket]`, `[confluence]`, `[servicenow]`, `[outlook_mail]` or
`[outlook_calendar]`. Without its table a product's tools are not offered and it
needs no credential. An empty table is enough to turn a product on with its
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

When several products share a host but others do not (Atlassian on one host,
Outlook on another), put the shared values in a group and have the products
join it, so the top level stays neutral:

```toml
[group.atlassian]
base_url = "https://atlassian.example.com"
[group.atlassian.cookies]
consent_accepted = "1"

[jira]
group = "atlassian"
path = "/jira"
```

Each value resolves from the product's own table, then its group, then the top
level. Paths in the config (`ca_bundle`, `token_env_file`) may use `~`, `$HOME`
or `${HOME}`; a variable that is not set is reported as an error.

Store your token. With no product named, `set-token` lists the tokens it can
store and asks which one:

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

Jira, Bitbucket and Confluence each have their own token, named after the
product: `JIRA_TOKEN`, `BITBUCKET_TOKEN`, `CONFLUENCE_TOKEN`. For each, these are
checked in order and the first one found wins:

1. The environment variable, such as `JIRA_TOKEN`.
2. `token_cmd`: a command that prints the token, such as a `sops`, `pass` or
   `op` call. Written as one string, it is read the way a shell reads a command
   line: `~`, `$HOME` and `${HOME}` expand, except inside single quotes, so
   `awk -F= '{ print $2 }' ~/.secrets/t.env` works. Written as a list
   (`["jq", "-r", "$x"]`), it is used exactly as written. Either way it runs
   without a shell (so no pipes; use `sh -c '...'` for one), with a 30-second
   limit, and its output is never logged. `{product}` in it is replaced by the
   product name, so one command can serve every product.
3. `token_env_file`: an env file with lines such as `JIRA_TOKEN=...`
   (`--token-env-file` on the command line overrides it). `{product}` works here
   too, for one file per product: `~/.secrets/{product}_token.env`. With
   `{product}` in the path, a product with no file of its own is skipped; a fixed
   path that does not exist is an error, since it is probably a typo.
4. The system keychain, where `set-token` stores it.

`token_cmd` and `token_env_file` can be set at the top of the config, in a
`[group.<name>]` table, or in a product's own table; the most specific wins:

```toml
# One encrypted file for every product's token.
token_cmd = "sops -d --extract '[\"{product}\"]' ~/.secrets/tokens.yaml"

[jira]
# Jira's token from somewhere else.
token_env_file = "${HOME}/.secrets/jira_token.env"
```

Or one env file per product, with no command at all:

```toml
token_env_file = "~/.secrets/{product}_token.env"
```

If no source has a token, the error lists where it looked.

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

### Confluence

Confluence is off until the config has a `[confluence]` table. To turn it on:

1. Add the table, with the sub-path Confluence is served under (or its own
   `base_url` if it is on a different host):

   ```toml
   [confluence]
   path = "/confluence"
   default_space = "~jdoe"  # optional: your personal space, or a team space key
   ```

2. Create a personal access token in Confluence (your profile, Settings,
   Personal Access Tokens) and store it:

   ```bash
   uv run productivity-agent-tools set-token confluence
   ```

3. Run `doctor` again; it now checks Confluence too. Restart your agent to load
   the new tools.

### ServiceNow

ServiceNow (a helpdesk behind single sign-on) is off until the config has a
`[servicenow]` table. It has no token: you sign in through the browser and the
tool keeps the resulting session. To turn it on:

1. Add the table, with the instance's own address:

   ```toml
   [servicenow]
   base_url = "https://helpdesk.example.com"

   # An empty table, so cookies meant for other hosts are not sent here.
   [servicenow.cookies]
   ```

2. Sign in:

   ```bash
   uv run productivity-agent-tools login servicenow
   ```

   A Chrome (or Edge) window opens on a new, temporary profile. Sign in there as
   you normally do, smart card included. When the helpdesk has loaded, the
   window closes by itself and the session is stored in the system keychain.

3. Run `doctor` again; it now checks ServiceNow too. Restart your agent to load
   the new tools.

The session ends when ServiceNow times it out (after about an hour idle on the
instance this was built against). A `servicenow_` tool then says the session has
expired; run `login servicenow` again. A server that is already running picks
up the new session without a restart.

What `login` does and does not do:

- It never sees your smart card, PIN or password. Those stay between you and
  the browser.
- It reads the instance's cookies from that one temporary browser profile, over
  a private pipe (no listening debug port), and deletes the profile afterwards.
  Your everyday browser profile is not touched.
- It checks the session by reading your own user record, then stores the
  cookies and the page's request token. Their values are never printed or
  logged.

`login` needs macOS or Linux, and a browser whose policy allows remote
debugging. Set `browser` under `[servicenow]` if Chrome or Edge is somewhere
unusual.

### Outlook calendar and mail

The read-only `outlook_calendar_` and `outlook_mail_` tools read Microsoft 365
over the Outlook REST API. They are off until the config has their tables, and
they share one Microsoft sign-in. To turn them on:

1. Copy the tables from `contrib/microsoft-dod.toml` (DoD webmail) into your
   config, or fill in `[microsoft]` from `config.example.toml` for another
   tenant.

2. Sign in:

   ```bash
   uv run productivity-agent-tools login microsoft
   ```

   As with ServiceNow, a Chrome (or Edge) window opens on a new, temporary
   profile, this time on the Microsoft sign-in page. Sign in as you normally
   do, smart card included. When the sign-in page hands back to webmail, the
   window closes by itself and the refresh token is stored in the system
   keychain. `login outlook_mail` and `login outlook_calendar` do the same.

3. Run `doctor` again; it now checks Outlook too. Restart your agent to load
   the new tools.

The tools renew their access token silently for as long as the tenant's
sign-in window allows (about 18 hours on the DoD tenant). After that an
`outlook_` tool says the sign-in has expired; run `login microsoft` again,
typically once each morning. A running server picks up the new sign-in without
a restart, at its next hourly access-token renewal.

To see when you signed in and when to sign in again, ask your agent (it calls
`outlook_mail_status`), or run `call outlook_mail_status`. The estimate needs
`sign_in_hours` under `[microsoft]` (18 in the DoD block); the status is read
from the keychain each time, so it reflects a sign-in done while the server runs.

How it differs from ServiceNow: it collects no cookies. The window opens on the
sign-in page with a one-time PKCE challenge whose secret half never leaves this
process. When you are in, the sign-in page sends the tab to webmail's redirect
address with a one-time code; `login` reads that address from the browser's tab
list (still without attaching to the page), redeems the code itself, and keeps
the refresh token that comes back. Set `browser` and `login_timeout_seconds`
under `[microsoft]` as for ServiceNow.

Beyond listing, searching and reading, `outlook_mail_query_messages` and
`outlook_calendar_query_events` take OData query options (`filter`, `select`,
`orderby`, `expand`, `top`, `skip`, `count`) and pass them to the API as written,
read-only. For example, to find a message by its `Message-ID` header:

```bash
uv run productivity-agent-tools call outlook_mail_query_messages \
  "filter=InternetMessageId eq '<abc123@mail.example.com>'" \
  select=Subject,ReceivedDateTime,From,Id
```

Results use the API's PascalCase property names; options accept PascalCase or
Graph-style camelCase. Header contents (`Authentication-Results`,
`List-Unsubscribe`) cannot be filtered on, but `select=Subject,InternetMessageHeaders`
returns the headers for a whole page of messages to read through.

If the browser sign-in cannot run, `set-token microsoft` stores a refresh token
you captured by hand (pasted at a hidden prompt, or `--from-response token.json`).
The older `login-microsoft` does the same and is deprecated.

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

Set the level with `log_level` in the config or `--log-level` on `serve`,
`doctor`, `call` and `login`: `debug`, `info` (default), `warning` or `error`.

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
| `jira_edit_issue` | write | Summary, description, type, priority, reporter, due date, estimates, epic, epic name, story points; add or remove labels, components, versions |
| `jira_get_create_fields` | read | Issue types a project offers, and one type's fields |
| `jira_create_issue` | write | Create an issue, epic, story in an epic, or sub-task, with the standard fields and story points |
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
| `bitbucket_create_branch` | write | Create a feature, bugfix, hotfix, release or custom branch, named after a Jira issue |
| `bitbucket_add_pull_request_comment` | write | Comment on a pull request, reply to a comment, or add a task (Markdown) |
| `bitbucket_review_pull_request` | write | Approve, mark as needing work, or withdraw your review |
| `confluence_status` | read | Confluence version, who is signed in, your personal space, dry-run |
| `confluence_list_spaces` | read | Spaces you can see, global or personal |
| `confluence_set_default_space` | session | The space other tools use when none is named, until restart |
| `confluence_search` | read | Pages and blog posts matching a CQL query |
| `confluence_get_page` | read | A page by id or exact title: body (storage format), version, ancestors, labels |
| `confluence_list_child_pages` | read | Pages directly under a page |
| `confluence_create_page` | write | Create a page from Markdown or storage format, optionally under a parent |
| `confluence_update_page` | write | Replace, append to, prepend to or find-and-replace in a page's body, or retitle or move it |
| `servicenow_status` | read | Who is signed in to ServiceNow, when they signed in, dry-run |
| `outlook_mail_status` | read | Who is signed in, when, and until about when the sign-in lasts |
| `outlook_mail_list_messages` | read | Messages newest first, optionally only unread |
| `outlook_mail_search_messages` | read | Outlook's own relevance search across subject, sender and body |
| `outlook_mail_query_messages` | read | OData query (`filter`, `select`, `orderby`, ...), such as a lookup by Message-ID |
| `outlook_mail_get_message` | read | One message with recipients and its body as text |
| `outlook_mail_get_message_headers` | read | A message's internet headers (Received, Authentication-Results, List-Unsubscribe, ...) |
| `outlook_calendar_status` | read | Who is signed in, when, and until about when the sign-in lasts |
| `outlook_calendar_list_calendars` | read | Your calendars, with their ids |
| `outlook_calendar_list_events` | read | Events in a date range (default the next seven days), recurrences expanded, times in UTC |
| `outlook_calendar_query_events` | read | OData query over events (recurring meetings as one series) |
| `outlook_calendar_get_event` | read | One event with attendees, their responses and the body as text |

Each tool is offered only when its product is configured. The Outlook tools only
ever read; there are no Outlook writes.

The `bitbucket_` tools appear only when Bitbucket is configured. Only the three
write tools change anything in Bitbucket, and `dry_run` blocks them just as it
blocks Jira's writes.

`bitbucket_create_branch` names a branch the way Bitbucket's own "Create branch"
dialog does: the branch type's prefix, the issue key, and the issue's summary in
lower case with dashes, such as `feature/ABC-123-retry-failed-uploads`. The
prefixes come from the repository's branching model, so a repository that uses
`feat/` or has switched a type off is respected; `custom` has no prefix. The
summary is looked up when Jira is configured. The branch starts from the model's
development branch (its production branch for a hotfix) unless you name a start
point. Because the name carries the issue key, the branch shows up on the issue
in Jira.

Agile issue types work through `jira_create_issue`: an Epic takes `epic_name`,
any other issue joins an epic with `epic` (the epic's key), a sub-task takes
`parent`, and `story_points` sets the estimate. These are custom fields whose
ids differ on every server; they are found by their type, so nothing needs
configuring. `jira_get_issue` shows them too.

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

If no CI or CD server reports builds or deployments to your Bitbucket, set
`ci = false` under `[bitbucket]`. The three build and deployment tools are then
not offered, so the agent does not try them, and reading a pull request skips
the build lookup.

Bitbucket has no way to list deployments. `bitbucket_get_deployment` needs
the deployment key, environment key and sequence number that your CI or CD
server reported, so the agent asks you for them rather than guessing.

The `confluence_` tools appear only when Confluence is configured. Only
`confluence_create_page` and `confluence_update_page` change anything, and
`dry_run` blocks both. Page bodies are written in Markdown and converted to
Confluence's storage format here, because Confluence 10 no longer converts wiki
markup through its API. Code blocks become Confluence code macros and images
become Confluence images. For macros, layouts or mentions, the agent can send
storage format directly (`body_format="storage"`).

An edit must name the version the agent read. If anyone changed the page since,
nothing is saved and the agent is told to read it again, so it cannot overwrite
someone else's change. To add a section, the agent can append to the page
(`mode="append"`) without sending the whole page back. To fill in a template or
change a few places on a page with layouts and macros, it passes `replacements`:
exact pieces of the page to find and what to put there. Everything else stays
byte for byte as it was. Each piece must occur exactly once, and an edit that
would leave broken markup is refused. `confluence_status`
shows your personal space key (`~` and your username), which is where pages
about you, such as an "About me" page, usually go.

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

Whatever these tools return is sent to the model your agent is configured with:
issues, code, pages, helpdesk records, mail and calendar entries. Confirm that
model is approved for that data before using this on real work.

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

Tests run against fakes of each server (and a stand-in browser); nothing here
contacts a real server. `scripts/smoke-test.sh` is the opposite: it runs every
tool once against your real servers through `call`, read-only or in dry-run (copy
`.env.example` to `.env` first).

### How the code is laid out

Product modules never make HTTP calls themselves. They talk to an `ApiClient`
(a protocol in `api.py`: `get`, `write`, `aclose`, plus `base_url` and
`dry_run`), and each kind of service has one adapter that implements it.
`HttpAdapter` in `transport.py` holds what every adapter shares: TLS trust,
extra cookies, dry-run, logging and connectivity errors. `AtlassianClient` in
`http.py`, `ServiceNowClient` in `servicenow/http.py` and `OutlookClient` in
`microsoft/outlook.py` add how their servers report results and failures. An
adapter is never handed a secret: it asks a credential provider
(`credentials.py`) for the authentication headers before every request: a fixed
bearer token for the Atlassian products, the stored browser session for
ServiceNow, and a renewed OAuth access token for Outlook. A new service with
different sign-in is a new adapter and a new product module; the existing
products do not change. Every adapter must honor `dry_run` in `write`.

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
2. Once it is on `main`, tag that commit with the same version, prefixed with
   `v`, and push the tag. A pull request merged by rebase gets new commit ids on
   `main`, so tag the commit there, not the one on the branch:

   ```bash
   git tag v0.2.0 <commit-on-main> && git push origin v0.2.0
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

`pytest` measures branch coverage and fails below 90%; the suite sits just
under 100%. Tool settings live in `pyproject.toml`, except taplo's, which it
only reads from `.taplo.toml`.

Layout: `transport.py`, `http.py`, `config.py`, `tokens.py`, `credentials.py`,
`browser.py` (the DevTools driver both browser sign-ins use) and `products.py`
are the shared core. Each product lives in a sub-package (`jira/`, `bitbucket/`,
`confluence/`, `servicenow/`, and `microsoft/` for the two Outlook products)
whose product module exposes a `Product` object, listed under the
`productivity_agent_tools.products` entry-point group in `pyproject.toml`.
Nothing in the shared core names a product: `server.py`, `config.py` and the CLI
find them through the entry points. Tools that need two products at once are an `Integration` (`links.py`),
listed under `productivity_agent_tools.integrations`. To add a product, write
the sub-package, add its entry point, and run `uv sync` so the entry point is
installed.
