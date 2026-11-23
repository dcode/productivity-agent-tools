# ServiceNow

A ServiceNow helpdesk behind single sign-on. The agent can list and read your
incidents (helpdesk tickets) and open a new one, and search the service catalog,
request an item (such as software) and follow your requests.

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
| `portal` | none | The service portal the form is read from: its address suffix, such as `sp` in `/sp?id=...` |
| `unlisted_item` | none | The item for requesting what the catalog does not list (name or `sys_id`); searches offer it |
| `portal_id` | read from the portal's page | The portal's `sys_id`, to skip reading it |
| `[servicenow.order_defaults]` | none | Answers used when ordering any catalog item whose form has the question |
| `[servicenow.incident_defaults]` | none | Answers to that form's questions used when the agent gives none |

## Tools

| Tool | Kind | What it does |
|---|---|---|
| `servicenow_status` | read | Who is signed in, when they signed in, dry-run |
| `servicenow_list_incidents` | read | Your incidents (you are the caller or opened them), open ones by default, newest first |
| `servicenow_get_incident` | read | One incident by number: state, who has it, comments, resolution; `extra_fields` reads any other field |
| `servicenow_get_incident_form` | read | The incident form's questions: which are required, their choices, defaults |
| `servicenow_create_incident` | write | Open an incident by submitting that form |
| `servicenow_search_catalog` | read | Find catalog items, such as software, by words |
| `servicenow_get_catalog_form` | read | An item's form: questions, choices, rules, defaults |
| `servicenow_order_item` | write | Request an item, as the portal's "Order now" does |
| `servicenow_list_requests` | read | Your requested items (RITM): stage, approval, delivery |
| `servicenow_get_request_item` | read | One requested item: who has it, comments |

### Opening an incident

An incident is opened by submitting the helpdesk's own form, the portal's
"Create an incident" page, exactly as the portal does. That form is a catalog
item (a record producer): its questions are not incident fields, and its script
builds the incident from the answers, with you as the caller. Writing the
incident table directly would skip every question the helpdesk requires, since
the form checks them in the browser, not the API.

The agent reads the form first with `servicenow_get_incident_form`: each
question's name and label, whether it is required, its choices and help text,
and the default it will get. The form is read the way the portal reads it, from
the portal's page for the item (`/api/now/sp/page`), because the Service Catalog
API can leave questions out: on the instance this was built against it returned
one of eleven. The request is the portal's own (its `portal_id`, an `x-portal`
header and the page address), so set `portal` to the suffix in the portal's
address. If the page has no form, the catalog API is used, and the result's
`source` says so and why. Configured defaults that do not fit the form are listed
under `problems`; the form is still shown, but a create refuses until they are
fixed. Then `servicenow_create_incident` takes the answers.
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

#### Rules that change the form

A form can show, hide and require questions as answers change: on the
instance this was built against, Subcategory appears (and is required) only when
Category is Hardware, and Hostname and Network only for a Hardware desktop or
laptop. These rules (the form's UI policies) come with the form, and the tool
applies them to the answers the way the portal does:

- a question the answers bring up must be answered if the rule requires it;
- an answer to a question the answers hide is refused, naming the rule;
- a configured default for a hidden question is left out, so a default such as
  `u_network` costs nothing on a ticket that does not ask for it.

`servicenow_get_incident_form` lists the rules in words under `rules`, and shows
each question's `shown` and `required` for the configured defaults alone. A rule
using a condition the tool does not understand is listed as not checked, and not
applied.

A question answered by picking a record, such as a site, shows the table it is
picked from as `lookup_table`. Its answer is the picked record's `sys_id`. A name
given instead is looked up in that table the way the portal's picker searches it
(its search column, such as `u_name`, within its fixed filter) and used if exactly
one record has it; otherwise the tool refuses rather than send the name. If your
account cannot read the table, give the `sys_id` itself: the portal's picker
request (`angular.do?sysparm_type=sp_ref_list_data` in the browser's network tab)
lists each record's `sys_id` beside its name.

`servicenow_get_incident` also takes `extra_fields`, which returns any other
incident field's stored `value` beside its `display` text. Its comments come as a
list, newest first, each with `at`, `author`, `kind` (such as "Additional
comments") and `body`.

Answers that rarely change go in the config, so the agent does not ask each
time. A question takes the agent's answer, else this default, else the form's
own:

```toml
[servicenow.incident_defaults]
u_site = "N/A"
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
portal = "sp"
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

### Requesting from the catalog

The agent finds an item with `servicenow_search_catalog` (each result has its
`sys_id`, and `ordered` is false for a form that creates another kind of record).
Many helpdesks have one form for anything not listed, such as a "Non-Standard
Software Request"; name it as `unlisted_item` under `[servicenow]` and every search
offers it, so the agent knows where to turn when a program is not in the catalog.
It then
reads its form with `servicenow_get_catalog_form`, and requests it with
`servicenow_order_item`. The form is read and checked exactly as the incident
form is, rules and lookups included, and the order is sent as the portal's
"Order now" sends it. The result has the request's number (REQ...) and its items
(RITM...); `servicenow_list_requests` and `servicenow_get_request_item` follow
them through approval and delivery.

```bash
uv run productivity-agent-tools call servicenow_search_catalog "query=visual studio"
uv run productivity-agent-tools call servicenow_order_item item=<sys_id> \
  'answers={"justification": "Daily editing"}' --dry-run
```

Answers that do not change from one request to the next go in
`[servicenow.order_defaults]`. Unlike the incident defaults, these are shared by
every item, so a key is used only for an item whose form has that question.
Keying them by the label as an identifier lets one entry serve items whose
question names differ:

```toml
[servicenow.order_defaults]
preferred_contact_phone_number = "555-0100"  # preferred_contact_phone_number_vs on software items
classified_workstation = "No"
platform = "Mac"                              # platform_os on one item, platform_oms on another
```

#### Scripts the tool does not run

Besides its rules, an item's form can run scripts in the browser: on the
instance this was built against, a script asks the server whether the item needs
a license and then requires a license key, another replaces the platform choices
with the item's own (often just one), and another refuses a classified Mac. The
server does not check these on submission, and the tool cannot run them, so
`servicenow_get_catalog_form` lists every script that requires, hides, sets or
limits answers under `scripts`, as code, for the agent to read and follow.

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
