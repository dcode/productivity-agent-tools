# ServiceNow

A ServiceNow helpdesk behind single sign-on. For now the product offers its
status tool, which proves the sign-in works; reads of requests and the service
catalog are next on the roadmap.

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
