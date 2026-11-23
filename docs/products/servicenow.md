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
| `incident_form` | `"Create an incident"` | The catalog form that opens an incident: its exact name or `sys_id` (see below) |
| `[servicenow.incident_defaults]` | none | Answers to that form's questions used when the agent gives none |

## Tools

| Tool | Kind | What it does |
|---|---|---|
| `servicenow_status` | read | Who is signed in, when they signed in, dry-run |
| `servicenow_list_incidents` | read | Your incidents (you are the caller or opened them), open ones by default, newest first |
| `servicenow_get_incident` | read | One incident by number: state, who has it, comments, resolution; `extra_fields` reads any other field |
| `servicenow_get_incident_form` | read | The incident form's questions: which are required, their choices, defaults |
| `servicenow_create_incident` | write | Open an incident by submitting that form |

### Opening an incident

An incident is opened by submitting the helpdesk's own form, the portal's
"Create an incident" page, exactly as the portal does. That form is a catalog
item (a record producer): its questions are not incident fields, and its script
builds the incident from the answers, with you as the caller. Writing the
incident table directly would skip every question the helpdesk requires, since
the form checks them in the browser, not the API.

The agent reads the form first with `servicenow_get_incident_form`: each
question's name and label, whether it is required, its choices and help text,
and the default it will get. Then `servicenow_create_incident` takes the answers.
A key can be the question's name (`u_preferred_contact_phone_number`), its label
as shown ("Network Connection", any case), or that label as an identifier in
snake_case or camelCase (`network_connection`, `networkConnection`):

```bash
uv run productivity-agent-tools call servicenow_create_incident \
  'answers={"category": "Network", "please_describe_your_issue_below": "VPN drops hourly"}' \
  --dry-run
```

Before anything is sent, the answers are checked against the form: a choice is
matched by its label or value in any case and sent as its value; an unknown
question or choice is refused with the list; and a required question left
unanswered is refused, naming all that are. An empty answer clears a default.

A question answered by picking a record, such as a site, shows the table it is
picked from as `lookup_table`. Its answer is the picked record's `sys_id`, not its
name, and the tool refuses a name rather than send it. To find the `sys_id`, read
one of your incidents that has the field set:

```bash
uv run productivity-agent-tools call servicenow_get_incident \
  number=INC0012345 'extra_fields=["u_site"]'
```

`extra_fields` returns each field's stored `value` (the `sys_id`) beside its
`display` text.

Answers that rarely change go in the config, so the agent does not ask each
time. A question takes the agent's answer, else this default, else the form's
own:

```toml
[servicenow.incident_defaults]
u_site = "<sys_id from extra_fields>"
u_preferred_contact_phone_number = "555-0100"
u_network_connection = "VPN"
```

#### Which form

`incident_form` finds the form by its exact name. Instances often keep
ServiceNow's stock "Create an Incident" item beside their own form of the same
name; when the name matches more than one, the tool refuses and lists their
`sys_id`s. Use the one in the portal's address when the form is open
(`...?id=sc_cat_item&sys_id=<this>`):

```toml
[servicenow]
incident_form = "10f198405863e700eb7b1e2c93eae7ca"
```

Like every write, nothing is sent while `dry_run` is on (the result shows the
request that would be sent), and an identical submission within 30 seconds is
not sent twice. The agent is told to check your open incidents for the same
problem, ask you for required answers it does not know, and confirm the answers
with you first.

The result has the new number and a link to its form
(`<base_url>/incident.do?sys_id=...`); if your instance sends you to a portal
instead, the number is what to search for.

`servicenow_get_incident` can show urgency and impact as empty even when they
were set: your account may not be allowed to read those fields. The priority
still reflects them.

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
