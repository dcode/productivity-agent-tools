# Outlook mail and calendar

Read-only Microsoft 365 mail and calendar over the Outlook REST API. There are
no Outlook writes. The two products share one Microsoft sign-in.

This uses the Outlook REST API v2.0 and the refresh token of the organization's
webmail app, as a stopgap until a proper Microsoft Graph client is approved for
the tenant. See the
[roadmap](https://github.com/dcode/productivity-agent-tools/blob/main/docs/ROADMAP.md)
for the background.

## Setup

1. Add the tables. For the DoD webmail tenant, copy them from
   [`contrib/microsoft-dod.toml`](https://github.com/dcode/productivity-agent-tools/blob/main/contrib/microsoft-dod.toml).
   For another tenant, fill in `[microsoft]` from
   [`config.example.toml`](https://github.com/dcode/productivity-agent-tools/blob/main/config.example.toml):

    ```toml
    [microsoft]   # how `login microsoft` signs in; not a served product
    client_id = "<the sign-in app's client id>"
    tenant = "<your tenant id, or organizations>"
    authority = "login.microsoftonline.com"
    origin = "<the webmail origin>"
    redirect_uri = "<a registered redirect URI under that origin>"
    scope = "<resource>/.default"
    sign_in_hours = 18   # optional: your tenant's sign-in window

    [group.microsoft]
    base_url = "https://outlook.office365.com/api/v2.0"

    [outlook_mail]
    group = "microsoft"

    [outlook_calendar]
    group = "microsoft"
    ```

    The Outlook host has no consent banner, so keep cookies meant for other
    hosts out of its group. Remove either product's table to turn it off.

2. Sign in:

    ```bash
    uv run productivity-agent-tools login microsoft
    ```

    A Chrome (or Edge) window opens on a new, temporary profile, on the
    Microsoft sign-in page. Sign in as you normally do, smart card included.
    When the sign-in page hands back to webmail, the window closes by itself and
    the refresh token is stored in the system keychain. `login outlook_mail` and
    `login outlook_calendar` do the same.

3. Run `doctor`, then restart your agent to load the tools.

`[microsoft]` keys:

| Key | Default | Meaning |
|---|---|---|
| `client_id`, `tenant`, `authority`, `origin`, `redirect_uri`, `scope` | required | The OAuth client the browser signs in as; not secret |
| `sign_in_hours` | none | How long the tenant lets one sign-in be renewed, so the status tools can say when to sign in again |
| `browser` | Chrome, then Edge | Browser `login microsoft` starts |
| `login_timeout_seconds` | `300` | How long `login microsoft` waits |

## How long a sign-in lasts

The tools renew their access token silently for as long as the tenant's sign-in
window allows (about 18 hours on the DoD tenant). After that an `outlook_` tool
says the sign-in has expired; run `login microsoft` again, typically once each
morning. A running server picks up the new sign-in at its next hourly
access-token renewal, without a restart.

To see when you signed in and when to sign in again, ask your agent (it calls
`outlook_mail_status`), or run `call outlook_mail_status`. The estimate needs
`sign_in_hours`. The status is read from the keychain each time, so it reflects a
sign-in done while the server runs.

If the browser sign-in cannot run, `set-token microsoft` stores a refresh token
you captured by hand: pasted at a hidden prompt, or `--from-response token.json`
for the token response saved from the browser's developer tools.

## Tools

| Tool | Kind | What it does |
|---|---|---|
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

## OData queries

`outlook_mail_query_messages` and `outlook_calendar_query_events` take OData
query options (`filter`, `select`, `orderby`, `expand`, `top`, `skip`, `count`)
and pass them to the API as written, read-only. To find a message by its
`Message-ID` header:

```bash
uv run productivity-agent-tools call outlook_mail_query_messages \
  "filter=InternetMessageId eq '<abc123@mail.example.com>'" \
  select=Subject,ReceivedDateTime,From,Id
```

- Results use the API's PascalCase property names, so a name seen in a result
  can go straight into the next filter; options accept PascalCase or
  Graph-style camelCase.
- Without `select`, a compact set of fields comes back (no bodies, no headers).
  Long text values are cut at `max_chars`, and the result says how many were.
- Page with `skip`: each result gives `next_skip` when there is more. `count=true`
  adds the total.
- Header contents (`Authentication-Results`, `List-Unsubscribe`) cannot be
  filtered on, but `select=Subject,InternetMessageHeaders` returns the full
  header set for a whole page of messages to read through.
- The event query reads events themselves, so a recurring meeting is one series;
  `outlook_calendar_list_events` is the one for "what is on my calendar".

## How the sign-in works

It collects no cookies. The window opens on the sign-in page with a one-time
PKCE challenge whose secret half never leaves this process. When you are in, the
sign-in page sends the tab to webmail's redirect address with a one-time code;
`login` reads that address from the browser's tab list (without attaching to
the page), redeems the code itself, and keeps the refresh token that comes back.
The code is useless to anyone without that secret half.
