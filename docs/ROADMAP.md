# Roadmap

Read this first when coming back to the project. The design and the reasons behind it are in
[DESIGN.md](DESIGN.md). Update this file in the same commit as the work it describes.

Last updated: 2026-11-18.

## Where things stand

- **Version:** 0.7.0, on `main` via
  [#15](https://github.com/dcode/productivity-agent-tools/pull/15) (2026-11-15). Tags:
  `v0.5.0` at 804f319, `v0.6.0` at 9be367b, `v0.7.0` at 0235736. Release tags go on the
  commit that changes `__version__`; `v0.1.0`-`v0.4.0` predate that rule and stay where
  they are. Cloud sessions cannot push tags, so tag from the laptop.
- **`login microsoft` (new, 2026-11-18, branch `claude/zealous-mendel-ny1eml`, not yet
  merged or run live):** the Outlook sign-in now works like `login servicenow`. It opens
  Chrome/Edge on a temporary profile at the Microsoft authorize page (PKCE + `state`), the
  user signs in as usual (smart card stays in the browser), and the tool reads the
  redirect's `code` from the tab list over the DevTools pipe (no page attach), redeems it
  itself, and stores the refresh token. Replaces "capture the token in DevTools and paste
  it" for the daily ~18 h re-sign-in; `login-microsoft` (paste) stays as the fallback.
  The browser driver moved to `productivity_agent_tools/browser.py`, shared by both.
  **Next action: run it on the work laptop** (checks under "Confirm against the real
  servers", Outlook REST). No version bump yet; do it with the merge (0.8.0).
- **Config groups (new, 2026-11-15):** a `[group.<name>]` table holds shared connection
  values (`base_url`, `ca_bundle`, `timeout_seconds`, cookies) and a product joins it with
  `group = "<name>"`; resolution is product → group → top level. This came out of the first
  live Outlook `doctor` run (below): it keeps the top level neutral so a Microsoft-only user
  never inherits the Atlassian host or its banner cookie. See DESIGN.md, Configuration.
- **Merged to `main`:**
  - [#1](https://github.com/dcode/productivity-agent-tools/pull/1): Jira server, CLI,
    CI, `configure-agent`
  - [#2](https://github.com/dcode/productivity-agent-tools/pull/2): read-only
    Bitbucket, `call`, opt-in products, smoke test
  - [#3](https://github.com/dcode/productivity-agent-tools/pull/3): Bitbucket builds,
    deployments and more pull-request reads
  - [#4](https://github.com/dcode/productivity-agent-tools/pull/4): Bitbucket pull-request
    writes, shared `writes.py`
  - [#5](https://github.com/dcode/productivity-agent-tools/pull/5): Jira log work, worklogs,
    assign
  - [#6](https://github.com/dcode/productivity-agent-tools/pull/6): Jira standard fields
  - [#7](https://github.com/dcode/productivity-agent-tools/pull/7): Jira and Bitbucket
    together
  - [#8](https://github.com/dcode/productivity-agent-tools/pull/8): products as
    entry-point plugins, `AgentToolsError`, a per-product adapter. Run `uv sync` after
    pulling so the entry points are installed. An unknown product table is now an error.
  - [#9](https://github.com/dcode/productivity-agent-tools/pull/9): Confluence (reads,
    search, create, edit, find-and-replace, `can_edit`), `ci = false`, the smoke-run fixes
    and version 0.2.0
  - [#10](https://github.com/dcode/productivity-agent-tools/pull/10): the smoke test reads
    its values from an untracked `.env` (copy `.env.example`)
  - [#11](https://github.com/dcode/productivity-agent-tools/pull/11): agile issue fields
    (`epic_name`, `epic`, `story_points`), `bitbucket_create_branch`, the `call` fix for
    named-choice parameters, and version 0.3.0
- **Also on `main`, in 0.4.0:** the credential seam, the ServiceNow adapter,
  `login servicenow` and `servicenow_status`, with the Microsoft Graph and ServiceNow
  spikes and version 0.4.0 (built on `claude/servicenow-credential-seam`).
- **Also on `main`, in 0.5.0:** the Outlook REST spikes and the Microsoft
  refresh-token lifecycle (`login-microsoft`).
- **Tests:** 543 passing, about 99.98% branch coverage. All hooks clean.
- **In use:** Goose on the work laptop, against Jira 11.3.12, Bitbucket 10.2.8 and Confluence
  10.2.19. No CI
  server (Bamboo or other) reports builds or deployments to this Bitbucket.
- **Last smoke run:** build `v0.2.0-3-g721e8b3`, 2026-11-12: all 41 calls answered, none
  failed. The dry-run branch create and the dry-run Epic create both work.
- **Microsoft Graph (new):** design settled and the sign-in probe passed against the real
  tenant (the public client authenticates and is not Conditional-Access blocked).
  **Waiting on admin consent** for the read-only delegated scopes before any build. The
  **Outlook REST stopgap** is being built in the meantime: the token lifecycle (0.5.0) and
  now the adapter plus the read-only `outlook_calendar`/`outlook_mail` products (0.6.0) are
  done and unit-tested, but **not yet run against the live tenant** (see "Confirm against
  the real servers"). See "Later" for the decisions and planned order.
- **ServiceNow (new, 2026-11-14):** the work helpdesk, for requesting software and
  following the requests. **The probe passed on the real instance:** the REST API accepts
  the browser's session from a plain client, and the account can read its own requests and
  the software catalog. Sign-in will be a `login servicenow` verb that drives the installed
  browser; **the sign-in probe also passed on the real instance** (smart card included).
  Both spikes are done, and steps 1 and 2 of the build are done and on `main` (built on
  `claude/servicenow-credential-seam`): the credential seam, the
  `ServiceNowClient` adapter, `login servicenow` and a `servicenow_status` tool.
  **Confirmed on the work laptop (2026-11-14):** `login servicenow`, the Keychain round
  trip, `doctor` and `servicenow_status` all work against the real instance.
  **Next action: step 3 under "Later": the read-only tools.** The slower session checks
  (idle expiry, API-only lifetime) are still open under "Confirm against the real servers".

## Done

- [x] Jira: status, presets, search, issue detail with every standard field, transitions
      (with resolutions), worklogs, edit and create screens, link types; add comment,
      transition, log work, assign, edit, create, link (writes)
- [x] Bitbucket (read-only): status, projects, repositories, pull-request lists, pull-request
      detail, commits, changes, diff, code search, default project
- [x] Bitbucket pull requests: merge status (blocking checks), activity history, tasks, linked
      Jira issues
- [x] Bitbucket builds and deployments: richer build results (key, description, tests, ref,
      duration), builds for any commit with totals, required builds, one deployment by its
      identifiers
- [x] Bitbucket writes: comment, reply or task on a pull request; approve, needs-work or
      withdraw a review; user slug from `applinks/whoami` or `user_slug` in the config
- [x] Jira and Bitbucket together: `jira_get_issue_pull_requests` (Development panel plus
      commits that mention the key), Jira details in `bitbucket_get_pull_request_issues`
- [x] Safety: `dry_run` on by default, MCP read/write annotations, 30-second repeat suppression
- [x] Connection: bearer tokens (environment variable, env file, Keychain), system trust store,
      generic extra cookies, per-product hosts and paths behind the `ApiClient` protocol
- [x] Diagnostics: structlog, debug HTTP logging without secrets, explained redirects and
      suspicious paths, `doctor` per product
- [x] CLI: `serve`, `doctor`, `set-token`, `export-ca`, `call` (with `--field`),
      `configure-agent` (Goose)
- [x] Project: hatch, uv, ruff, ty, rumdl, taplo, pre-commit, CI on Linux and macOS, tag
      releases, Dependabot, `scripts/smoke-test.sh`
- [x] Products as plugins: `Product` objects found through the
      `productivity_agent_tools.products` entry points, integrations between products,
      capability protocols for optional cross-product data (see DESIGN.md, Overall shape)
- [x] Agile issues: epics (`epic_name`), issues in an epic (`epic`), `story_points`, on
      `jira_create_issue`, `jira_edit_issue` and `jira_get_issue`
- [x] `bitbucket_create_branch`: feature, bugfix, hotfix, release or custom, named after a
      Jira issue, following the repository's branching model
- [x] Confluence: status (with personal space), spaces, default space, CQL search, page by
      id or title (storage format), child pages; create and edit pages from Markdown or
      storage format, with version checks and append/prepend (see DESIGN.md, Confluence
      specifics)

## Confirm against the real servers

These have automated tests but have never run against the live servers. Check each when the
chance comes up and tick it here.

- [ ] Jira comments: run `call jira_get_issue key=<ISSUE>` on an issue that has comments and
      compare the result with the browser
- [ ] Bitbucket pull-request comments: run `call bitbucket_get_pull_request` on a pull
      request that has comments
- [ ] ~~Bitbucket build results~~: cannot be checked here, no CI server reports builds
- [ ] A moved or renamed file in `bitbucket_get_pull_request_changes`
- [ ] `bitbucket_search_code`, once the server's search index stops returning 503
- [ ] `call --field` colouring, viewed in a real terminal
- [ ] The new Bitbucket reads, through `scripts/smoke-test.sh` (fill in the new placeholders):
  - [ ] `bitbucket_get_pull_request_merge_status` on an **open** pull request blocked by a
        check (a merged one now gives the `note`, confirmed)
  - [ ] `bitbucket_get_pull_request_activity`: answers (OPENED and MERGED seen); still
        check that REVIEWED is shown for needs-work
  - [ ] `bitbucket_list_pull_request_tasks`: answers, but only seen empty; try a pull
        request with tasks
  - [ ] `bitbucket_get_pull_request_issues`: answers, but only seen empty; try a pull
        request whose commits or title name an issue key
  - [x] `bitbucket_list_required_builds` answers on 10.2.8 (none configured)
  - [ ] ~~`bitbucket_get_commit_builds` and `bitbucket_get_deployment`~~: cannot be
        checked here, no CI server reports to this Bitbucket
- [ ] Jira log work and assign:
  - [ ] `jira_list_worklogs` on an issue with time logged, compared with the browser
  - [ ] One real `jira_log_work` with a comment and a `started` time: check the time zone
        in the browser, and that the remaining estimate went down
  - [ ] `jira_assign_issue` with `me`, with a colleague's display name, and with `none`
  - [ ] Whether `user/assignable/search` matches on display names on Jira 11.3 (it takes
        `username`, which Data Center matches against name, display name and email)
- [ ] Jira standard fields:
  - [ ] `jira_get_issue` on an issue with links, components, fix versions and time tracking,
        compared with the browser
  - [x] `jira_get_edit_fields` and `jira_get_create_fields` answer on 11.3.12 (createmeta
        works), and a wrong issue type is refused with the project's own list
  - [ ] One real `jira_edit_issue` (a label and the priority) and one real
        `jira_create_issue` in a test project
  - [ ] One real `jira_link_issues` with "blocks": **confirm the direction** in the browser
        (the first issue should say "blocks", the second "is blocked by")
  - [ ] `jira_transition_issue` with a resolution, on a workflow whose Done screen has one
- [ ] Jira and Bitbucket together:
  - [ ] `jira_get_issue_pull_requests`: both sources answer on the real servers
        (`rest/dev-status` works), but only seen with no pull requests. Try an issue that
        has one, ideally linked only by branch name, and check the panel finds it.
  - [ ] `bitbucket_get_pull_request_issues` shows summaries and statuses
- [ ] Bitbucket writes:
  - [x] `bitbucket_status` shows your `user_slug`, from the `X-AUSERNAME` header
        (`applinks/whoami` does not accept a bearer token on 10.2.8)
  - [x] The dry-run comment shows the request it would send
  - [x] The dry-run review does too
  - [ ] One real comment and one real approve and unapprove on a test pull request, with
        `--no-dry-run`, compared in the browser
- [ ] Confluence:
  - [x] Set up and `confluence_status`: 10.2.19, `rest/api/server-information` answers
  - [x] Spaces, CQL search in a space, a page by id, child pages and the dry-run create
        (Markdown converted) all answer
  - [x] `personal_space` null is right: there is no personal space
  - [x] `confluence_get_page` on the profile page shows `can_edit: false`, matching the
        browser (no Edit link). Edit access was granted on 2026-11-13; it should now
        read `true`.
  - [ ] `contributor = currentUser()` was accepted but found nothing; confirm once you
        have edited a page
  - [x] `confluence_get_page` with `max_chars` high enough for the whole body: the layout,
        macros and `ac:placeholder` hints come back intact
  - [x] One real `confluence_update_page` (2026-11-13): the profile page, filled in with
        `replacements` built from the live page's exact text. First real Confluence write
        and first real use of `replacements`.
  - [x] An edit with a stale `version` is refused on the real server, naming the current
        version and saying to read the page again
  - [x] **The profile page** is done, ahead of the Fri Nov 20 deadline.
- [ ] Agile issues and branches (the smoke test has dry-run checks for both):
  - [x] The dry-run Epic create finds the Epic Name field on 11.3.12
  - [ ] `jira_get_create_fields project=<KEY> issue_type=Epic` lists an Epic Name field,
        and for a story type an Epic Link and Story Points field (if not, paste the output:
        the fields are recognised by their custom type key)
  - [ ] `jira_get_issue` on an issue that is in an epic shows `epic`; on an epic shows
        `epic_name`
  - [x] One real epic created (2026-11-13)
  - [ ] One real story in that epic with story points, then `jira_get_issue` on it shows
        `epic` and `story_points`
  - [x] The dry-run branch shows the repository's prefix and start point with no `note`:
        `branch-utils/.../branchmodel` answers on 10.2.8
  - [ ] One real `bitbucket_create_branch` from an issue, seen in the browser and on the
        issue's Development panel
- [ ] ServiceNow, after `login servicenow` on the work laptop:
  - [x] `login servicenow` itself: the packaged flow signed in on the real instance
        (2026-11-14)
  - [x] The Keychain accepts the stored session (the cookie header is one to two
        kilobytes) and `doctor` reads it back
  - [x] `doctor` and `call servicenow_status` name you (still good 23 minutes after
        sign-in)
  - [ ] Leave it idle past an hour: the next call should say the session has expired,
        and `login servicenow` again should fix a running server without a restart.
        Seen 2026-11-14: idle from 16:09:59 to 17:37:52 UTC (88 minutes) and the next
        `servicenow_status` got a 401, so the idle limit is under 88 minutes. Still to
        check: the wording of the error, and the fix reaching a running server.
  - [ ] Whether a session used only through the API lasts as long as one in the browser:
        the adapter discards cookies the server sets, so any the instance rotates are
        not kept.
        Seen 2026-11-14/15: a `servicenow_status` loop on a fresh session kept
        succeeding overnight, last success **2026-11-15T05:26:43Z**; it stopped because
        the VPN dropped, not on a 401. So an API-only session, kept busy, survives well
        past the idle limit even though rotated cookies are dropped, with no absolute
        cap hit in that window. Still to record: the loop's start time (the "saved" time
        in `servicenow_status`) and interval, to state the lifetime as a number.
  - [ ] With a top-level `[cookies]` table, add an empty `[servicenow.cookies]` so those
        cookies are not sent to the helpdesk
- [ ] `login microsoft` (browser sign-in) on the work laptop, with
      `--log-level debug`. The unknowns, in the order they would bite:
  - [ ] Conditional Access accepts a sign-in from a **fresh temporary profile** (the
        ServiceNow SSO did, but AAD may want a compliant/registered device, which an
        empty profile cannot prove). Failure shows as an `AADSTS53000`/`53003` error in
        the redirect, reported as "The Microsoft sign-in failed: …".
  - [ ] The authorize page accepts our request for the OWA client (`response_mode=fragment`,
        PKCE S256, `openid profile offline_access` + the Outlook `.default`).
  - [ ] `Target.getTargets` / `Target.setDiscoverTargets` show the redirect **with its
        fragment** before `oauthRedirect.html` rewrites it. If the debug log shows only
        "no tab came back", this is the suspect.
  - [ ] The code redeems (Origin header, same as the refresh) and the message names you
        from the ID token; then `doctor` works without any paste.
  - [ ] A second `login microsoft` while `serve` runs is picked up without a restart.
- [ ] Outlook REST (`outlook_calendar`/`outlook_mail`), after `login microsoft` (or the
      `login-microsoft` paste fallback) on the work laptop. First live run 2026-11-15:
      `login-microsoft` seeded and the token is
      **accepted** (redemption works, no 401), but `GET /me` returned 404 because both
      Outlook tables were **empty**, so they inherited the top-level Atlassian `base_url`
      (stormwind) and the `dodacknowledged` cookie — the request went to stormwind, not
      Outlook. **Fixed by the group layer:** put the Outlook host in `[group.microsoft]`
      and `group = "microsoft"` on each product (contrib updated), so the top-level
      Atlassian settings no longer leak. Re-run pending with the corrected config.
  - [ ] `doctor`: both products authenticate `GET /me` and name you (one seeded
        `[microsoft]` token, shared). Confirm the shared-credential path mints the Outlook
        token once, not once per product. (Note: `doctor` redeems per product in sequence,
        so the two "seeded at" times differ by the rotation between them — that is expected
        and confirms the token is shared.)
  - [ ] `call outlook_calendar_status` and `outlook_mail_status`: `user_name`/`email` match
        the mailbox.
  - [ ] `outlook_calendar_list_calendars`, then `outlook_calendar_list_events` for the next
        week: times come back in UTC (the `Prefer` header), recurrences are expanded, and
        the count matches the browser. Then `outlook_calendar_get_event` on one, with
        attendees and a text body.
  - [ ] `outlook_mail_list_messages` (and `unread_only=true`), `outlook_mail_search_messages`
        on a known term, and `outlook_mail_get_message` on one (text body, truncation).
  - [ ] `outlook_mail_get_message_headers` on a real message: confirm
        `InternetMessageHeaders` actually comes back (it is `$select`-only and may be capped
        in size), and that Authentication-Results / Received / List-Unsubscribe are present
        and useful for triage. **Open question:** does the DoD Outlook REST v2.0 return the
        full header set, or a subset?
  - [ ] Confirm a 401 (let the 18 h sign-in window lapse) is reported as "run
        login microsoft", and that signing in again fixes a running server without a
        restart.

## Next up

Rough order. Each item is one pull request. API references: the Jira 11.0.2 and Bitbucket
10.5 OpenAPI specs (`https://dac-static.atlassian.com/server/jira/platform/jira_software_dc_11002_swagger.v3.json`,
`https://dac-static.atlassian.com/server/bitbucket/10.5.swagger.v3.json`).

1. One real story in the new epic and one real branch from an issue (see the checks above).

## Later

- **GitLab (self-managed)**: `gitlab_wiki` and `gitlab_scm` products sharing a `gitlab`
  token, a GitLab `ApiClient` adapter (error format differs from Atlassian's), and a
  Jira–GitLab integration if issues are tracked in Jira. Not started; the plugin structure
  was built for it. Open question: does `jira_get_issue_pull_requests` become an
  integration per code host, or a capability (`CodeHost`) that every code host offers?
- **Multiple accounts of one product (multi-tenancy)** — design notes, not started
  (captured 2026-11-15). Motivation: run the *same* product against two accounts at once —
  e.g. an Army M365 mailbox and a civilian-employer M365 mailbox, each its own credential
  and host, both through `outlook_mail`. Today one product = one config table = one tool
  prefix = one client = one `token_name`, so two mailboxes is impossible.

  The seams already lean this way and should be reused, not replaced: the adapter is handed
  a `HeaderProvider` (not a token), credentials are addressable by keychain name through
  `CredentialResolver`, and the new `[group.<name>]` layer carries per-host connection. So
  the likely model is three layers:
  1. **Named auth profiles** — e.g. `[[auth.microsoft]]` with `name = "A365"` /
     `"MyEmployer"`, each a credential of a *kind* (microsoft-oauth, atlassian-pat,
     servicenow-browser) stored in the keychain under its own name. `[microsoft]` today
     becomes the single default profile; `login-microsoft --account A365` seeds a named one.
     This generalizes `token_name` from a single string to a named profile.
  2. **Groups** gain an `auth = "<profile>"` reference (connection + which credential), as
     in `[group.a365]` / `[group.my365]`.
  3. **Product instances** — one product bound to several groups, each instance getting its
     own tool namespace.

  **The hard part is instancing the tool names, not auth.** `register_tools` hardcodes tool
  names (`outlook_mail_list_messages`), and `build_server`/config assume one client per
  product name. Multi-account needs distinct prefixes the agent can address unambiguously
  (`outlook_mail_a365_*` vs `outlook_mail_my365_*` — favoured over an `account=` arg on
  every tool, for agent clarity and write safety), which means `register_tools` must take a
  prefix and config must gain an instance dimension. That touches every product. The entry
  point stays one singular `Product`; instancing is a runtime/config concern layered on top.

  Open questions to settle (worth a short design interview, like Jira/ServiceNow — the
  tool-naming choice is costly to change later):
  - Config syntax: `groups = ["a365", "my365"]` on a single `[outlook_mail]` table (one
    instance per group, backward-compatible: omit or `group = "x"` = single instance)
    vs. array-of-tables `[[outlook_mail]]`. Favour the list — avoids array-of-tables and
    keeps the single-instance case a plain `[product]` table.
  - Tool-prefix derivation: from the group/instance name? Must be prefix-safe.
  - Per-instance `login` and keychain naming; `doctor` and `status` per instance.
  - Agent-facing instructions so the model knows which prefix is which mailbox.
  - Single-instance must stay dead simple; multi-instance strictly opt-in.
  - Writes (later) make cross-account mistakes costly: distinct prefixes + per-instance
    `dry_run`.
- **More Confluence:** labels (`content/{id}/label`), attachments (a photo for "About
  me", then `ac:image` with `ri:attachment`), page comments, blog posts, and `@` mentions
  from Markdown. The 10.2.20 OpenAPI spec is at
  `https://dac-static.atlassian.com/server/confluence/10.2.20.swagger.v3.json`.
- **OpenTelemetry** traces of httpx requests and responses, alongside structlog.
- **Microsoft Graph (Microsoft 365 calendar, then mail):** an adapter plus product modules.
  Design interview done (2026-11-13/14); the decisions are in DESIGN.md (Roadmap section).
  **Status: blocked on admin consent.** The sign-in probe (`scripts/ms_graph_probe.py`,
  authorization-code + PKCE with the public "Microsoft Graph Command Line Tools" client)
  reaches the tenant and is not Conditional-Access blocked, but the tenant requires an admin
  to approve the delegated scopes; the request was submitted with a read-only justification.
  Nothing to build until that lands (or IT registers a tenant app, which is a one-line
  `client_id`/`tenant` swap). Device-code flow not separately tested yet.

  **Stopgap being probed (token replay):** while consent is pending, reuse a bearer
  token the browser/desktop-client session already holds (the ServiceNow pattern —
  harvest the credential the SSO sign-in produced — but a JWT, not a cookie pair). This
  is a gov-cloud (DoD) tenant `fae6d70f-…` on `login.microsoftonline.us`; Graph is
  `dod-graph.microsoft.us` and Outlook REST is `outlook-dod.office365.us`.
  `scripts/ms_graph_token_probe.py` decodes a pasted token locally, routes it by `aud`
  (Graph tokens to Graph `/v1.0`, Outlook tokens to Outlook REST `/api/v2.0` — same
  OData shape for `/me/events|messages|calendars`), and makes read-only probes.

  **First real tokens decoded (2026-11-14), both from the Teams/Office first-party
  client `9199bf20-a13f-4107-85dc-02114787ef48`:**
  - Graph token: `aud=dod-graph.microsoft.us` (right audience), but its `scp` has
    **no `Calendars.*`/`Mail.*`** — only User/People/Directory/Files/Teams. So Graph
    authenticates `/me` but will **403** on mail/calendar. That client does not request
    mail/calendar on the Graph resource.
  - Outlook token: `aud=outlook-dod.office365.us` with **`Calendars.ReadWrite` and
    `Mail.ReadWrite`** — Graph rejects this audience, but **Outlook REST v2.0 accepts
    it**, so it is the token that actually reads calendar and mail today.

  **Confirmed end-to-end (2026-11-14), read-only via Outlook REST:** a refresh-minted
  Outlook token (`.../outlook-dod.office365.us/.default`) returned **200** on `/me`,
  `/me/calendars` (2), `/me/events?$top=1` (1) and `/me/messages?$top=1` (1). That is the
  stopgap, and the only one that works.

  **Graph for mail/calendar via this harvested client is a hard dead end.** Requesting
  `dod-graph.microsoft.us/Calendars.Read Mail.Read` (from either the Outlook or the Graph
  refresh token) fails with **AADSTS65002** — not 65001 (consentable) but *preauthorization
  required*: the OWA/webmail SPA is a Microsoft first-party app and is not preauthorized by
  the API owner (Microsoft) to request Graph. No tenant admin can change that. So this
  detour cannot reach Graph mail/calendar at all; the real Graph path remains the pending
  admin consent on the proper Graph CLI client, unchanged by any of this.

  **Read-only by discipline, not by scope:** the client holds only `Mail.ReadWrite` /
  `Calendars.ReadWrite` (no read-only scope exists to request), so the minted token is
  write-capable and the stopgap adapter must stay GET-only — same posture as "writes
  behind `dry_run`" below.

  Adapter shape if we build the stopgap: Outlook REST v2.0 base
  (`outlook-dod.office365.us/api/v2.0`), fed by the `HeaderProvider` seam (done) reading a
  refresh-minted token. **Outlook REST v2.0 is deprecated by Microsoft** — functional in
  the DoD cloud today but on borrowed time — so keep the stopgap thin and swappable;
  MSAL-on-Graph-after-consent stays the real target.

  **The client is a Single-Page App (OWA/webmail, `webmail.apps.mil`).** A plain
  server-style refresh POST is refused with **AADSTS9002327** ("tokens issued for the
  Single-Page Application client-type may only be redeemed via cross-origin requests").
  AAD redeems an SPA refresh token only for a browser-style CORS request, so the POST
  must carry an `Origin` header matching a registered SPA redirect URI (and MSAL.js also
  sends `redirect_uri`). `scripts/ms_token_refresh.py` now sends both, defaulting to the
  `https://webmail.apps.mil` origin and `.../mail/oauthRedirect.html` redirect seen in
  the browser's own refresh call. **The Origin fix is confirmed working** (2026-11-14):
  9002327 is gone and AAD accepts the request shape.

  **Token cadence (corrected):** the access token is ~60 min, and although the response
  advertises `refresh_token_expires_in` ~24 h, **Conditional Access caps effective reuse
  at an 18 h sign-in-frequency window** (64800 s) — beyond it, redemption fails with
  **AADSTS70043** and a fresh interactive sign-in (smart card/MFA) is required. So the
  durable shape of the stopgap is: sign in once per ~18 h window (e.g. each morning),
  seed the refresh token, and let the tool renew access tokens silently through the day;
  re-seed at the next sign-in. Not per-hour debug-bridge scraping, and not a fire-and-
  forget daemon. **Live silent renewal confirmed** (2026-11-14): a fresh refresh token
  minted an Outlook token that read `/me`, `/me/calendars`, `/me/events` and
  `/me/messages` (all 200) via Outlook REST.

  **Stopgap build track (decided 2026-11-14: automate the token lifecycle first, then
  the adapter).** A new `microsoft/` package mirroring `servicenow/`:
  1. ~~**Token lifecycle.**~~ **Done** (2026-11-14), the `microsoft/` package.
     `MicrosoftSettings` reads the `[microsoft]` table (OAuth
     `client_id`/`tenant`/`authority`/`origin`/`redirect_uri`/`scope`, all **required** —
     no DoD defaults in core; `contrib/microsoft-dod.toml` carries the public DoD/OWA
     values to copy; `base_url` for the Outlook REST API). `MicrosoftSession` is stored in
     the keychain under token name `microsoft` — the refresh token (the only secret) plus
     the non-secret redemption parameters, so the runtime credential is self-contained
     (the `CredentialResolver` seam only gets `name`/`env_file`). `redeem_refresh_token`
     (httpx POST to the token endpoint, `Origin` header + `redirect_uri`, system trust —
     the login host is public, not behind the org CA). `MicrosoftCredential` provider:
     in-memory access-token cache + `asyncio.Lock` (one redemption under concurrency),
     reloads the session on the slow path so a re-seed while running is picked up, rotates
     and re-stores the refresh token on each use, and maps AADSTS70043/invalid_grant to an
     `AuthenticationError` that says to re-seed. A `login-microsoft` CLI verb seeds it
     (paste the refresh token, or `--from-response` the captured JSON) and verifies by
     redeeming once. `[microsoft]` is a reserved table in `parse_settings` (no product yet,
     so `serve`/`doctor` ignore it). Full unit coverage; no adapter/tools yet.
  2. ~~**Outlook REST adapter + read-only `outlook_calendar`/`outlook_mail` products**, GET
     only, on `outlook-dod.office365.us/api/v2.0`, fed by `microsoft_token`.~~ **Done
     (2026-11-15), version 0.6.0.** The `microsoft/` package gained `OutlookClient` (the
     adapter, Outlook's `_interpret` plus `Prefer: body-content-type=text, timezone=UTC`),
     a shared `OutlookSettings`, a shared `reads.py` (OData `value`/`nextLink`, `/me`,
     PascalCase→snake_case helpers), and two products that **share the `microsoft` token**:
     - `outlook_calendar`: `outlook_calendar_status`, `_list_calendars`,
       `_list_events` (via `calendarview`, recurrences expanded, default now→+7d),
       `_get_event` (body as text, attendees, truncation).
     - `outlook_mail`: `outlook_mail_status`, `_list_messages` (newest first, unread
       filter), `_search_messages` (OData `$search`), `_get_message` (body as text,
       truncation), and `_get_message_headers` (internet/MIME headers via `$select`, for
       spam/phishing/mailing-list triage — Authentication-Results, Received chain,
       List-Unsubscribe, etc.).

     Decision taken here (the open design point below): **two products, not one.** They
     share a token, each has its own `[outlook_calendar]`/`[outlook_mail]` table so either
     can be enabled alone, and `[microsoft]` stays an OAuth-only config table (not a served
     product). To keep the shared *rotating* refresh token safe, `cli._open_server` now
     resolves a token name **once** and hands the same credential provider to every product
     that shares it (one cache, one lock, one rotation) — see DESIGN.md, Outlook REST
     specifics. `contrib/microsoft-dod.toml` gained the two product tables. Fully unit
     tested against a fake Outlook; **never run against the live tenant yet** (see
     "Confirm against the real servers").
  3. ~~**Optional: automate the seed.**~~ **Done (2026-11-18) as `login microsoft`**, the
     ServiceNow model rather than a debug-bridge pull: the browser drives the sign-in on
     a throwaway profile, the tool watches tab addresses for the OAuth redirect and
     redeems the authorization code itself (PKCE, so the code is useless without this
     process's verifier). `microsoft/signin.py`; `[microsoft]` gained `browser` and
     `login_timeout_seconds`; `login outlook_mail`/`outlook_calendar` are aliases;
     `set-token microsoft` points at it. Rejected: reading MSAL's token cache
     (`localStorage`, maybe encrypted) or intercepting the token response — both need
     page attach, which the work laptop's Chrome refuses. Unit-tested; **not yet run
     live** (see "Confirm against the real servers").

  Planned order, each a pull request once unblocked:
  1. ~~**The OAuth seam.**~~ **Done**, on `main`, shared with
     ServiceNow: the adapter is given a `HeaderProvider` (async, returns the authentication
     headers) instead of a static `token: str`. The Atlassian adapter's PAT is wrapped in
     `bearer_token`, so Jira, Bitbucket and Confluence do not change in behaviour.
  2. **`MicrosoftGraphClient` adapter:** MSAL, auth-code + PKCE primary with a device-code
     fallback, configurable `client_id`/`tenant` (defaulting to the public client), token
     cache persisted via `keyring` under token name `microsoft`; a `login microsoft` CLI
     verb for the interactive sign-in; `doctor` calling `GET /me`. Own `base_url`
     (`https://graph.microsoft.com/v1.0`), system trust store, no cookies.
  3. **`outlook_calendar` product**, read-only: list calendars, list and read events.
  4. **`outlook_mail` product**, read-only: list and search messages, read a message.
  5. **Writes behind `dry_run`** (draft or send mail, create or update events) — a separate,
     larger consent (`Mail.Send`, `Calendars.ReadWrite`), deferred until the read path is
     approved and trusted.
  Open design point **(resolved 2026-11-15)**: one `microsoft` product with many tools, or
  several products (`outlook_calendar`, `outlook_mail`, ...) sharing a `microsoft` token.
  Went with **several products sharing the token** (favoured; mirrors the planned GitLab
  split), and already implemented that way for the Outlook REST stopgap. When the Graph
  path is built, reuse the same two products and swap only the adapter/credential. The
  shared-token machinery (`cli._open_server` resolving a token name once) is in place.
- **ServiceNow (helpdesk requests):** request software for the laptop and keep track of
  the requests. Sign-in is SAML through a federation host that asks for a smart-card
  certificate, so there is no token to store: basic auth has no password to use, and OAuth
  needs a client that an instance admin registers. **Status: probe passed, design not
  started.**

  Confirmed by `scripts/servicenow_probe.py` on the real instance (2026-11-14):
  - **The browser session works from a plain client, but only with the CSRF token.** The
    `Cookie` header alone gets 401; adding `X-UserToken` (the page's `g_ck`, found in the
    portal page's HTML) gets 200. The credential is therefore a pair: cookie and token.
  - **Anonymous callers:** the Table API answers 401 with a Basic challenge (no SSO
    redirect on API paths). GraphQL answers **200 to a guest**, so an expired session cannot
    be detected from a GraphQL status code; check the `x-is-logged-in` header or use REST.
  - **Own records are readable:** `sc_request`, `sc_req_item` and `incident`, filtered with
    `javascript:gs.getUserID()`, return rows and a total.
  - **Always filter by the user.** An unfiltered query returns the table's whole row count
    in `X-Total-Count` and no rows (the access rules hide them), so totals from unfiltered
    queries mean nothing, and `sc_task` showed no readable rows.
  - **An unfiltered `sys_journal_field` query times out.** Read comments through the
    record's own `comments` field with `sysparm_display_value`, or query the journal by
    `element_id`; which works is unchecked.
  - **A request item has the fields a tracker needs:** `number`, `short_description`,
    `cat_item`, `stage`, `state`, `approval`, `request`, `estimated_delivery`, `due_date`,
    `opened_at`, `closed_at`, `close_notes`, `comments`, plus site-specific `u_*` fields
    (96 in all).
  - **The Service Catalog API works:** it lists catalogs, text search finds software items
    (many, so results need paging), and an item's form comes back with its variables (text,
    multi-line text, select box, yes/no, plus layout-only kinds to skip).
  - **GraphQL adds nothing here:** introspection is disabled, and `GlideRecord_Query` only
    repeats what the Table API gives. **Decision: use REST.**

  **Sign-in decision (2026-11-14): a `login servicenow` verb that drives the installed
  browser**, chosen over pasting the cookie from DevTools. It starts Chrome (or Edge) with a
  temporary empty profile, the user signs in there as usual, and the tool reads the
  instance's cookies (`Storage.getCookies`) and the open tab's address
  (`Target.getTargets`) over the DevTools protocol. It then fetches that page itself to
  read `g_ck` from the HTML and confirms the pair with one API read. Rejected: reading the
  browser's cookie database (looks like credential theft to endpoint protection), an
  embedded web view (cannot do the certificate prompt), an extension (blocked on managed
  browsers), and performing the SAML exchange in Python (the tool would handle the smart
  card and PIN). Also rejected: the `pysnc` client (Table API only, no Service Catalog, none
  of its sign-in flows apply, and it always follows redirects).
  - The DevTools channel is `--remote-debugging-pipe`, not a TCP port: no listening port
    that another local program could attach to, and no websocket dependency (the spike is
    standard library only). A debugging *port* is confirmed to work on the work laptop; the
    pipe is now confirmed too.
  - **Do not attach to the page.** On the work laptop's Chrome, `Target.attachToTarget`
    is refused with "Not allowed" (cause not established; a policy limiting page
    inspection is the guess), so `Runtime.evaluate` is unavailable. Only browser-level
    commands are used.
  - Signed-in is detected from the API: without the token it answers 401 either way, but
    its `x-is-logged-in` header turns `true`. Nothing is sent to the instance while the tab
    is away at the sign-in host.
  - `scripts/servicenow_login_probe.py` proves the loop, and **passed on the work laptop
    against the real instance (2026-11-14, Chrome 156):** the pipe channel is allowed, the
    smart-card sign-in works in the temporary profile, and plain Python read the user's
    record with the collected session.
  - After sign-in the tab rests on the classic UI's home page, not the portal, and that
    page's HTML carries `g_ck` too, so no portal path needs configuring.
  - Eight cookies are collected, six of them HttpOnly. `JSESSIONID` is session-only;
    `glide_session_store` is issued with a one-hour life, which suggests (not proves) a
    one-hour idle timeout.

  Planned order, each a pull request:
  1. ~~**The credential seam**~~, shared with Microsoft Graph (step 1 there). **Done
     (2026-11-14), on `main`:**
     `credentials.py` with `HeaderProvider`, `Credential`, `CredentialResolver`,
     `bearer_token` and `personal_access_token`; `Product.resolve_credential`; the adapter
     asks for headers before every request. No behaviour change for the Atlassian products.
  2. ~~**`ServiceNowClient` adapter and `login servicenow`**~~ **Done (2026-11-14), on
     `main`.** What was built:
     - `transport.py`: `HttpAdapter`, the machinery every adapter shares, split out of
       `AtlassianClient` with no change in its behaviour.
     - `servicenow/http.py`: `ServiceNowClient`. A 401, a redirect, or an answer served to
       a guest (`x-is-logged-in: false`) each become "the session has expired, run
       `login servicenow`".
     - `servicenow/session.py`: the session (cookie header and `X-UserToken`) stored as
       JSON in the Keychain under `servicenow`. The provider reads the Keychain before
       every request, so a new sign-in reaches a running server without a restart.
     - `servicenow/browser.py` and `login.py`: the spike's browser driver and sign-in
       flow, moved into the package. `login <product>` is a generic CLI verb; a product
       offers it through `Product.login`.
     - A minimal product so `doctor` works: `[servicenow]` settings (`base_url`, `browser`,
       `login_timeout_seconds`) and `servicenow_status`.
     - `set-token servicenow` is refused and points at `login`.

     Not done, on purpose: no `logout` verb yet (the Keychain entry can be deleted by
     hand), and `login` is POSIX-only.
  3. **`servicenow` product, read-only** (next): my requests and items with stage and approval, one
     item in detail with comments, my incidents, catalog search, an item's form.
  4. **Writes behind `dry_run`:** order an item (`order_now` with the form's variables), add
     a comment. Not probed: the probe sent no writes.

  Open questions:
  - How long a session lasts in practice (see "Confirm against the real servers").
  - A `logout servicenow` verb that deletes the stored session.
  - The session's idle timeout, which decides how painful pasting is. The tool should not
    keep a session alive artificially.
  - Whether scripted use of the helpdesk is within the acceptable-use policy; ask before
    building writes.
  - Reading the checked-out item's variables (what was asked for) on an existing request.
- **Sprints:** put an issue in a sprint by name (needs the board API to find the sprint's
  id), and list a board's sprints.
- **Other custom Jira fields** (`customfield_*`), read and set through the same screen
  metadata.
- **More Bitbucket writes:** comments on a file or line of the diff (needs the anchor's
  path, line, line type and file type), resolving and reopening tasks, editing or deleting
  the user's own comments.

## Open questions

- **Deployment listing is parked.** With no CI server reporting to Bitbucket there is
  nothing to list. `ci = false` under `[bitbucket]` now hides the build and deployment
  tools; set it in the work laptop's config.
- The smoke test's values now live in an untracked `.env` (copy `.env.example`). The real
  issue key, project key and repository slug that were in the script are still in the git
  history of `main`. Leave them, or rewrite history?
