# Roadmap

Read this first when coming back to the project. The design and the reasons behind it are in
[DESIGN.md](DESIGN.md). Update this file in the same commit as the work it describes.

Last updated: 2026-11-14.

## Where things stand

- **Version:** 0.4.0, tagged `v0.4.0` at ec90a60 on `main`.
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
- **Tests:** 389 passing, about 99.9% branch coverage. All hooks clean.
- **In use:** Goose on the work laptop, against Jira 11.3.12, Bitbucket 10.2.8 and Confluence
  10.2.19. No CI
  server (Bamboo or other) reports builds or deployments to this Bitbucket.
- **Last smoke run:** build `v0.2.0-3-g721e8b3`, 2026-11-12: all 41 calls answered, none
  failed. The dry-run branch create and the dry-run Epic create both work.
- **Microsoft Graph (new):** design settled and the sign-in probe passed against the real
  tenant (the public client authenticates and is not Conditional-Access blocked).
  **Waiting on admin consent** for the read-only delegated scopes before any build. See
  "Later" for the decisions and planned order.
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
        not kept
  - [ ] With a top-level `[cookies]` table, add an empty `[servicenow.cookies]` so those
        cookies are not sent to the helpdesk

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

  So read-only calendar+mail is feasible **now via Outlook REST**, not via Graph, with
  the token already in hand (live probe of the read calls still pending). Tokens last
  ~60 min; the response also carries a **refresh token (~24 h)**, so the durable form of
  the stopgap is: harvest a refresh token once, mint access tokens silently against the
  tenant token endpoint, re-harvest daily — not per-hour debug-bridge scraping. Open
  question for the adapter: Graph (needs the right scopes consented — the real target) vs
  Outlook REST (works today but is a deprecated API). An optional `HeaderProvider` that
  reads a hand-supplied / refresh-minted token is the seam either way; MSAL-after-consent
  stays the real target.
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
  Open design point: one `microsoft` product with many tools, or several products
  (`outlook_calendar`, `outlook_mail`, ...) sharing a `microsoft` token (favoured; mirrors
  the planned GitLab split).
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
