# ServiceNow

A ServiceNow helpdesk behind single sign-on. The agent can list and read your
incidents (helpdesk tickets) and open a new one for you. Software requests
through the service catalog are next on the roadmap.

## Setup

```toml
[servicenow]
base_url = "https://helpdesk.example.com"
```

If you have top-level `[cookies]` meant for another host, add an empty
`[servicenow.cookies]` table so they are not sent here (or keep those cookies in
a [group](../configuration.md#groups) instead).

Then sign in:

```bash
uv run productivity-agent-tools login servicenow
```

A Chrome (or Edge) window opens on a new, temporary profile. Sign in there as
you normally do, smart card included. When the helpdesk has loaded, the window
closes by itself and the session is stored in the system keychain. Run `doctor`
to check it, and restart your agent to load the tools.

| Key | Default | Meaning |
|---|---|---|
| `browser` | Chrome, then Edge | Browser `login servicenow` starts: a path or a command name |
| `login_timeout_seconds` | `300` | How long `login servicenow` waits for you to finish |

## Tools

| Tool | Kind | What it does |
|---|---|---|
| `servicenow_status` | read | Who is signed in, when they signed in, dry-run |
| `servicenow_list_incidents` | read | Your incidents (you are the caller or opened them), open ones by default, newest first |
| `servicenow_get_incident` | read | One incident by number: state, who has it, comments, resolution |
| `servicenow_list_incident_categories` | read | The categories the incident form offers |
| `servicenow_create_incident` | write | Open an incident with you as the caller |

### Opening an incident

`servicenow_create_incident` takes a one-line `short_description`, a
`category`, and optionally a `description`, `urgency` and `impact` (`high`,
`medium` or `low`, the instance's standard 1-3 values; priority follows from the
two). The caller is always you: the tool reads your own user record first, even
in dry-run, to fill it.

The category is required because the helpdesk's form requires it. The API does
not enforce that, since the form's mandatory fields are checked in the browser,
so the tool does instead. It reads the form's categories
(`servicenow_list_incident_categories`), accepts a label or value in any case,
sends the value, and refuses one that is not listed, naming those that are. If
your account cannot read the list, the category is sent as written.

`servicenow_get_incident` can show urgency and impact as empty even when they
were set: your account may not be allowed to read those fields. The priority
still reflects them.

If your instance requires a field with no parameter (a site-specific `u_`
field, say), pass it in `fields`, by column name:

```bash
uv run productivity-agent-tools call servicenow_create_incident \
  "short_description=VPN client fails to connect" category=Network urgency=medium \
  'fields={"u_location": "Building 2"}' --dry-run
```

`fields` may not set `sys_` fields, the caller, or anything that has its own
parameter. Like every write, nothing is sent while `dry_run` is on (the result
shows the request that would be sent), and an identical create within 30
seconds is not sent twice. The agent is told to check your open incidents for
the same problem and confirm the wording with you first.

The result has the new number and a link to its form
(`<base_url>/incident.do?sys_id=...`); if your instance sends you to a portal
instead, the number is what to search for.

## How the sign-in works

`login servicenow` reads the instance's cookies from the temporary browser
profile over a private pipe, fetches the page you landed on to find its
cross-site request token, checks the pair by reading your own user record, and
stores both. Their values are never printed or logged. See
[Authentication](../authentication.md#browser-sign-in) for what `login` does and
does not do.

The session ends when ServiceNow times it out (on the instance this was built
against, after about an hour idle). A `servicenow_` tool then says the session
has expired; run `login servicenow` again. A server that is already running
picks up the new session without a restart.
