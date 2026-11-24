# Roadmap

Read this first when coming back to the project. The design and the reasons behind it are in
[DESIGN.md](DESIGN.md). Update this file in the same commit as the work it describes.

Last updated: 2026-11-23 (evening).

## Where things stand

- **Docs, logo and a plugin guide (branch `claude/zealous-mendel-ny1eml`, from `main` after
  #21, 2026-11-24). Start here.** Asked for on 2026-11-24: use the user's responsive SVG
  as the repo icon and in the docs, review all the docs, add an architecture page (token
  use, agent versus a naïve implementation) and a contributing guide with a plugin
  example.
  - **Logo:** `docs/assets/logo.svg` (the user's SVG with a `<title>`; it switches colours
    with `prefers-color-scheme`), the site's logo and favicon (header now white in light
    mode so it reads), and the README. `docs/assets/social-preview.png` (1280×640) is
    for GitHub's Settings → Social preview, which only the user can upload.
  - **Review:** every tool (77, from a server built with all products on) is on its
    product page and no page names a missing one; every settings key is documented
    except Outlook's `default_max_results` (added); every CLI option except `export-ca
    --output` (added). Stale text fixed in `development.md` (Obsidian is a local product
    and was missing from the package list; the release steps now follow the tagging
    rule; the `ApiClient` methods). Not re-read line by line: getting-started,
    configuration and workflow beyond those checks.
  - **New pages:** `architecture.md` (layers, a naïve agent versus typed tools, measured
    tool-definition sizes: 77 tools, 244,839 bytes with every product on),
    `contributing/index.md`, `contributing/plugin.md`.
  - **Example plugin:** `examples/holidays-plugin` (public holidays from Nager.Date:
    `holidays_list`, `holidays_next`), a separately installable package with its entry
    point; tested by `tests/test_example_plugin.py` (on `pytest`'s `pythonpath` and ty's
    `extra-paths`); the guide's code is read from it by `pymdownx.snippets`. Checked: it
    installs beside the server (`uv pip install -e`), `call` lists its tools, `doctor`
    passes, and a live call returned the next German holidays.
  - **Coverage at 100% (asked for, 2026-11-24):** the two gaps were tests that never
    called the `bitbucket_search_code` tool itself, and the `set-token` picker without
    the Outlook products installed (a plugin-only install). Both are tested now; 968
    tests. The threshold in `pyproject.toml` is still 90%.
  - **CI fix:** the `ruff-format` hook (ruff 0.17.4) formats Python blocks in Markdown
    and broke the plugin guide's `--8<--` markers; the page is excluded from that hook.
- **0.12.0, ServiceNow catalog requests (branch `claude/zealous-mendel-ny1eml`, restarted
  from `main` after #20 merged, 2026-11-23). Start here.** Asked for on 2026-11-23: "the
  ability to file software requests".
  - **Built, from the incident work and the portal script seen earlier (not yet seen
    live):** `servicenow_search_catalog` (`GET .../servicecatalog/items?sysparm_text=`),
    `servicenow_get_catalog_form` (any item's form, read and described as the incident
    form is), `servicenow_order_item` (write: answers checked against the form, rules and
    lookups included, then `POST .../items/{id}/order_now` with the portal's body
    `sysparm_quantity`, `variables`, `get_portal_messages`, `sysparm_no_validation`; a
    record producer is submitted instead; after a real order its RITMs are read),
    `servicenow_list_requests` and `servicenow_get_request_item` (`sc_req_item` for the
    user, as the probe read it: `requested_for` or `opened_by`; comments as entries).
    `[servicenow.order_defaults]`: shared answers, used for an item only when its form has
    the question. Smoke test: requests, a request item, a search, a form, and a dry-run
    order (`SERVICENOW_SEARCH`, `SERVICENOW_ORDER_ITEM`, `SERVICENOW_ORDER_ANSWERS`).
  - **HAR of the portal's catalog (user, 2026-11-23, sanitized; no order submitted):**
    - **Software is one item per program,** class `pc_software_cat_item` (Acrobat 2019 DC
      MAC OS Only, MacPorts), plus a generic "Non-Standard Software Request"
      (`sc_cat_item`, ~70 questions, 11 rules). All allow "Order now" (`no_order_now`
      false, no cart forced), so `order_now` fits. Not yet seen: the order request.
    - Required on the software items: `preferred_contact_phone_number_vs`,
      `classified_workstation` (Yes/No), `platform_os` (`unix`/`mac`/`windows`);
      `requested_for` defaults to the user. Shared order defaults keyed by label
      identifier (`preferred_contact_phone_number`, `classified_workstation`,
      `platform`) reach the questions on both kinds of item.
    - **The portal's search** is `POST /api/now/sp/search` (`{"query", "source": "sc",
      "portal", "count", ...}`); the tool keeps the catalog API search the probe saw
      working. To check: that it finds "macports".
    - **Client scripts matter on software items:** onLoad `GlideAjax('ToGetLicenseRequiredInfo')`
      (MacPorts answered 0: no license; 1 makes `license_key_or_proof_of_purchase`
      required); onLoad `GlideAjax('CatItemModelOptions')` replaces `platform_os`'s
      choices with the item's own (both items: only Mac); onChange refuses a classified
      Mac, and makes `hostname` required when not classified (`barcode_asset_tag` when
      classified). Both calls go to `POST /xmlhttp.do` (form-encoded `sysparm_processor`,
      `sysparm_name`, item id; answer in an XML `answer` attribute).
    - **Changed the same day:** the `ANYTHING` condition operator (one NSSR rule used it);
      the form view lists the scripts that change the form (`scripts`), as code, for the
      agent to follow. **Open question for the user:** whether to also call those two
      lookups (`xmlhttp.do`) so the tool enforces the item's real platform choices and its
      license requirement.
  - **A real order through the portal (user, 2026-11-23, Non-Standard Software Request,
    terraform; REQ0094044):** the portal used the cart: `POST .../items/{id}/add_to_cart`
    with `sysparm_quantity`, `variables`, `sysparm_item_guid`, `sysparm_no_validation`,
    then checkout through the cart widget (`checkout_two`), not `order_now`. The request
    came back approved at once, stage Requested. The tool keeps `order_now`: the user
    confirmed the portal also offers ordering a single item directly ("Checkout now",
    no cart), which is the portal script's `order_now` path, and it never touches the
    user's cart. The variables matched the tool's questions; `platform_oms` "Mac" is
    `mac_oms`, which the tool sends. The portal also sent 18 layout variables
    (containers, labels, a widget, an attachment) as "true" or "", which the tool leaves
    out. **Caution seen:** the cart held an earlier copy of the same item (21:20), so the
    checkout likely ordered both; the user to check REQ0094044 for a second RITM.
  - **Software requested (user, 2026-11-23):** sops, gnupg-pkcs11-scd and OpenSC (for the
    CAC's encryption key: macOS's `ssh-keychain.dylib` exposes only the authentication
    and signature keys), as Non-Standard Software Requests. **Asked next:** how an agent
    finds that form when a program is not in the catalog. Added `[servicenow]
    unlisted_item` (name or sys_id); `servicenow_search_catalog` now returns
    `{items, unlisted_item}` and says to use it when nothing fits.
  - **Confirmed by the user (2026-11-23): all three requests were ordered through
    `servicenow_order_item` with `--no-dry-run`, the first real orders through
    `order_now`.** Ordering works end to end.
  - **Confirmed (user, 2026-11-23):** searching "macports" finds the item (with
    `unlisted_item` beside it).
  - **Still open:** the smoke test's catalog checks (`SERVICENOW_SEARCH`,
    `SERVICENOW_ORDER_ITEM`, `SERVICENOW_ORDER_ANSWERS` in `.env`); the user will run
    them later.
  - **Decided (user, 2026-11-23): the tool should run the item scripts' server lookups,
    so submissions match the web form.** Not started; planned as the next ServiceNow
    work, after #21 (0.12.0) merges. The plan:
    1. An adapter read for a form-encoded POST that returns text (`/xmlhttp.do`, as
       GlideAjax sends: `sysparm_processor`, `sysparm_name`, parameters; the answer is
       the XML `answer` attribute). Check live whether the session's `X-UserToken` is
       enough or `sysparm_ck` must be in the body too.
    2. `servicenow/scripts.py`: a small evaluator for the script shapes seen (not a
       JavaScript engine): `var x = g_form.getValue('f')`, `newValue`,
       `g_form.getUniqueValue()` (the item), literals; `if (a == 'x' && b == 'y') {...}
       else {...}`; `setVisible`/`setDisplay`/`setMandatory` with true/false;
       `removeOption`, and `addOption` from a `JSON.parse(answer)` list; `alert(...)`
       with `setValue(f, '')` meaning the combination is refused; GlideAjax answers bound
       through `getXMLAnswer(function (answer) ...)` or
       `responseXML.documentElement.getAttribute('answer')`. Anything else stays listed
       for the agent as not checked.
    3. Order: onLoad scripts (with their lookups) set the starting choices and states
       (platform list, license fields); then answers, rules, and the onChange scripts of
       answered fields (`isLoading`/empty guards skipped), which can override states or
       refuse. The form view marks each script checked or not.
    Seen on the work instance: `CatItemModelOptions.getOptions(sysparm_cat_item)` ->
    `[{"label": "Mac", "value": "mac"}]`; `ToGetLicenseRequiredInfo.filter_data(sysparm_id)`
    -> `0` (MacPorts); onChange: classified + mac refused; classified Yes hides and frees
    `hostname`, requires `barcode_asset_tag`; No requires `hostname`.
  - **Next action: merge the 0.12.0 pull request, tag it on its last commit, then the
    script lookups above.**
  - Tests: 956 passing, 99.97% branch coverage; ruff and ty clean.
- **0.11.0, ServiceNow incidents: merged to `main` via
  [#20](https://github.com/dcode/productivity-agent-tools/pull/20) (2026-11-23, rebase
  merge, 17 commits). Tagged `v0.11.0` at 3f25eb1 (the last commit; the user, 2026-11-23).**
  Asked for on 2026-11-23: "add the ServiceNow support to create tickets". Read as
  **incidents** (the helpdesk's tickets), not catalog orders.
  - **Tools:** `servicenow_get_incident_form` (read), `servicenow_create_incident`
    (write, behind `dry_run`, 30 s repeat guard), and the reads that go with it:
    `servicenow_list_incidents` (the user's, as caller or opener; open only by default)
    and `servicenow_get_incident` (by number, display values, comments).
  - **How a create works now (third version, 2026-11-23):** it submits the portal's
    "Create an incident" form, a record producer, through the Service Catalog API
    (`submit_producer`), as the portal does. The answers are checked against the form's
    definition first (`servicenow/forms.py`): every required question answered, every
    choice one the form offers (label or value, any case), unknown questions refused,
    nothing sent on a refusal. Defaults come from `[servicenow.incident_defaults]` (by
    name, label, or label as snake_case/camelCase, so the config needs no quoted keys;
    asked for 2026-11-23), then the form's own. `[servicenow] incident_form` names the form
    (default "Create an incident", or a `sys_id`). DESIGN.md, Write safety, has why.
  - **How it got here, all on 2026-11-23:**
    1. The first version wrote the `incident` table directly. A real create
       (`INC0162364`, since cancelled) was accepted, but the record had no site,
       network connection or contact phone, and came back with category "Software".
    2. The second required a category, checked against `sys_choice`. The smoke run
       showed `sys_choice` returns no rows to the user (`{"result": []}`, no error),
       so that check could never work.
    3. The user listed the portal form's required questions: Preferred Contact Phone
       Number (text), Site (N/A for the user), Category (12 choices, from "Above
       Baseline Project" to "Windows 11"), What's the impact (4 levels), What's the
       urgency (5 levels), Network Connection (6 choices), and "Please describe your
       issue below" (text area). None but category and the description are incident
       fields, so the form itself is now what is submitted.
    - Also seen on the first create: **urgency and impact read back empty** while the
      priority was "5 - Planning", so they were set but the account cannot read those
      fields. Documented; not a bug.
  - Smoke test: lists incidents, reads the first, reads the form (expects required
    questions), and dry-runs a create with `SERVICENOW_ANSWERS` from `.env` (JSON).
  - **Smoke run 2026-11-23, build `v0.10.0-6-g9e04fdc`: 96 passed, 0 failed.** The
    form tool found **the stock "Create an Incident"** (one question, `comments`;
    ServiceNow's own description), not the portal's form. The portal's form has the
    same title; its page address gives its sys_id, **`10f198405863e700eb7b1e2c93eae7ca`**.
    From a saved copy of the page (web archive), its questions are:
    `u_preferred_contact_phone_number` (text, max 40), `u_site` (**a reference**, picked
    from a table), `category` (choice), `subcategory` (choice: Desktop, Desk Phones,
    Laptop, Mobile Device, Other Peripheral, Printer, Tablet, Webcams; likely depends on
    the category), `impact` ("What's the impact?", values 1-4), `urgency` (values 1-5),
    `u_network_connection` (values aConnect, VPN, Wired, SIPR, Stand-Alone, Not
    Applicable), `comments` ("Please describe your issue below"), `caller_id` (a
    reference, filled with the user), and `u_hostname`, `u_serial_number`, `u_barcode`,
    `u_network` (Classified/Unclassified), required or not unknown.
    **Changed the same day:** a name matching several items is refused with their
    sys_ids; a reference question shows its `lookup_table` and takes only a sys_id
    (a name is refused, saying how to find one); `servicenow_get_incident` takes
    `extra_fields` and returns each with its stored value and display text, which is
    how to find the "N/A" site's sys_id.
  - **Checked live (2026-11-23):** with `incident_form` set to the portal form's
    sys_id, the catalog API **still returned only `comments`**, so it does not serve
    this form's other questions. `extra_fields=["u_site"]` on an incident came back
    null in both forms: `u_site` is not a readable incident field (it is a form
    variable). The saved page's scripts show how the portal works: it reads the form
    with `GET /api/now/sp/page?id=sc_cat_item&sys_id=...&portal_id=...` (model in
    `sc_cat_item._fields`: `name`, `label`, `type`, `mandatory`, `choices`,
    `ed.reference`) and submits with `POST /api/sn_sc/v1/servicecatalog/items/{id}/
    submit_producer` `{"variables": ...}`, which is what the tool already sends.
    **Changed the same day:** the form is read from the portal page first (catalog API
    as the fallback; the result's `source` says which), `[servicenow] portal_id` is
    new, and a lookup answer given by name is looked up in its table (`name=`), used
    when exactly one record matches. The work portal's sys_id, from the saved page's
    style-sheet address, is `733f07dfaf2203508b18f269a02749dc`.
  - **Checked live (2026-11-23, second time):** with `incident_form` and `portal_id`
    set, the form tool still fell back to the catalog API, and failed outright on the
    `u_site` default ("not a question ... Questions: comments"). The user's HAR of the
    portal showed the portal's page answer holds the whole form where expected
    (`...widget.data.sc_cat_item._fields`, keyed by name, order in `_sections`); the
    tool's parser, run on that answer, read all fourteen entries correctly (thirteen
    questions and a search widget). So the tool's own request was answered
    differently. The portal's request also carries an `x-portal` header and
    `request_uri=/arlsp2?id=sc_cat_item&sys_id=...`; the tool sent neither, and its
    fallback hid the reason. Also seen: `u_site` picks from the table `u_site`;
    `caller_id` defaults to the user's sys_id; category has 12 choices; the portal
    submits with `get_portal_messages` and `sysparm_no_validation` set.
    **Changed the same day:** `[servicenow] portal` (the address suffix, `arlsp2`); the
    portal's sys_id is read from its page; the page is asked for as the portal asks
    (param, header, `request_uri`); a fallback says why in `source`; questions follow
    `_sections` order and the search widget is skipped; the form tool lists defaults
    that do not fit under `problems` instead of failing; the submission body matches
    the portal's. `ApiClient.get` takes optional extra `headers` (credentials win).
  - **Checked live (2026-11-23, third time):** with `portal = "arlsp2"`, the form tool
    reads the form from the portal page (`source: "portal page"`, 13 questions). The
    `u_site = "N/A"` default was listed under `problems`: the lookup asked for
    `name=N/A`, but the site table names records in `u_name` (the portal's picker
    request, `angular.do?sysparm_type=sp_ref_list_data`, returned 29 sites with
    `u_name`; "N/A" is `998d2bf1afd2d5505732526559274912`). The form model already
    says which column the picker searches: `ed.searchField = "u_name"`.
    **Fixed the same day:** lookups search `ed.searchField` (else `name`) within
    `ed.qualifier` (unless a script), and the message names the column.
  - **Checked live (2026-11-23, fourth time): the lookup works.** `u_site = "N/A"`
    resolves to `998d2bf1afd2d5505732526559274912` (so the user can read the `u_site`
    table by `u_name`), and `problems` is empty. **Smoke run on build
    `v0.10.0-...-g2f50987`: 98 passed, 0 failed, 2 skipped** (Bitbucket builds and code
    search, as before), including the form read and the dry-run create with
    `SERVICENOW_ANSWERS`. Note for later sessions: `SERVICENOW_ANSWERS` is read only by
    the smoke test; `call` takes answers as `answers=...` on the command line.
    The user's working setup: `[servicenow]` `incident_form = "10f19840..."`,
    `portal = "arlsp2"`; `[servicenow.incident_defaults]` `u_site`,
    `u_preferred_contact_phone_number`, `u_network_connection`, `u_network`.
  - **Comments are readable (user, 2026-11-23):** the record's `comments` field comes
    back as one text, newest first, each entry headed `<date time> - <name>
    (Additional comments)`. **Changed the same day:** `servicenow_get_incident` returns
    them as a list (`at`, `author`, `kind`, `body`), split at the header lines
    (`servicenow/journal.py`), since a comment can hold blank lines.
  - **Real create through the form: done (user, 2026-11-23).** A `--no-dry-run`
    create succeeded and was cancelled; comments read back and split cleanly.
  - **Subcategory depends on category (user, 2026-11-23, sanitized HAR):** no request
    is made when the category changes. The form's four UI policies come in the page
    answer (`sc_cat_item.policy`) as data: Subcategory shown and required only for
    category `hardware`; Hostname and Network shown and required for hardware and
    subcategory desktop or laptop; two others touch variables not on the form. The
    subcategory's choices never change. The one client script (caller change sets
    Site from the caller's location by GlideAjax) does not run on load.
    **Changed the same day:** `servicenow/rules.py` evaluates the policies;
    `servicenow_create_incident` requires what they require, refuses answers to
    hidden questions, and drops defaults for them; the form tool lists `rules` in
    words and each question's `shown`/`required` for the defaults. Checked against
    the real page: Software needs no subcategory; Hardware needs one; Hardware laptop
    needs hostname (network from the default); Software with a subcategory is refused.
  - **To check on the laptop:** `call servicenow_get_incident_form` shows two rules,
    and a dry-run create with `category=Hardware` and no subcategory is refused.
  - Open, from the roadmap's ServiceNow questions: confirm the helpdesk is fine with
    tickets opened through the API.
  - **Smoke run 2026-11-23 (94 passed, 1 failed, 3 skipped), the failure explained.**
    "mail list unread" failed with `Could not reach the Microsoft login host
    login.microsoftonline.us (ReadTimeout)`: a 30 s timeout renewing the access token,
    3 s after the previous mail call had renewed fine. The cause was that the access
    token lived only in memory, so every `call` process redeemed the refresh token
    again. **Fixed the same day:** the access token is now stored with the session and
    reused across processes while it has more than a minute left, and a renewal that
    cannot reach the login host is tried once more after 2 s (DESIGN.md, Credentials).
    Also fixed: the smoke test's zip was *added to* when a build was run twice (same
    file name), so a second run's zip kept the first run's results; it is now removed
    first. **To check on the laptop:** a smoke run, then `doctor` or any `call` within
    the hour should not renew (run with `--log-level debug`: no request to the login
    host), and `outlook_mail_status` still describes the sign-in correctly.
- **0.10.0, the Obsidian vault, all 6 steps done; merged to `main` as a6899ac
  ([#19](https://github.com/dcode/productivity-agent-tools/pull/19), 2026-11-21).
  Tagged `v0.10.0` (confirmed by the user, 2026-11-23).**
  Asked for on 2026-11-19: use these tools from Obsidian to plan the day, keep task notes
  (TaskNotes) for what I am responsible for, keep meeting notes, and never get the same
  task or meeting twice from one day to the next. The design is agreed and recorded in
  DESIGN.md (Roadmap, "Obsidian vault", and "Obsidian specifics"); the six steps are
  under "Next up".
  - **Step 1 (commit 8f71f8e):** `Product` split into `RemoteProduct` (every product so
    far) and `LocalProduct` (`open(settings, dry_run)` yields the client; settings extend
    `LocalProductSettings`, so no `base_url` is needed). `server.py` takes
    `Opened(client)` beside `Connected(api)`; the CLI connects and runs `doctor` for both
    kinds; `set-token` and `login` list only remote products. **Per-product `dry_run`:**
    a product's table may set it and wins for that product; the command-line flag wins
    over every table; `doctor` prints a product's value when it differs. Groups do not
    carry it (agreed).
  - **Step 2 (this commit): the `obsidian` product, reads only.** `[obsidian]` takes
    `vault` (required), `tasks_folder` (`TaskNotes/Tasks`), `meetings_folder`
    (`Meetings`), `task_tag` (`task`), `done_statuses` (`["done"]`),
    `default_max_results` (50) and `dry_run`. Tools: `obsidian_status`,
    `obsidian_list_tasks`, `obsidian_find_task`, `obsidian_get_note`. `doctor` checks for
    `.obsidian/`, counts tasks and says whether the tasks folder exists yet. The smoke
    test has an Obsidian section (checked here against a scratch vault: 8 passed).
  - **Dated notes and time zone (2026-11-19, asked for during step 2):** meetings go to
    `Meetings/YYYY/MM/YYYY-MM-DD <title>` (`meeting_date_format`), daily notes follow
    Obsidian's Daily notes settings (the user's are `Journal/YYYY/MM/YYYY-MM-DD`), with
    `daily_folder` / `daily_format` to override and `doctor` failing when they disagree.
    `obsidian_get_daily_note` reads a day's note. "Today" is an aware time in
    `time_zone`, else this computer's zone (`timezones.py`, looked up on every call).
  - **Checked by the user (2026-11-19):** `doctor` gives the expected daily-note and
    meeting lines with `meeting_date_format = "YYYY/MM/YYYY-MM-DD"`, with other daily
    formats in Obsidian, and with and without today's note.
  - **Step 3: writes.** `obsidian_create_task` (refuses an existing key; holds back a
    title scoring 0.8+ against an open task until `allow_similar`), `obsidian_update_task`
    (by path, `[[link]]` or `jira:ABC-123`; refuses source-owned fields on synced tasks;
    sets `completedDate`), `obsidian_write_section` (between hidden markers; a new daily
    note starts from Obsidian's daily template). Round-trip properties, temporary file
    plus rename, and a note changed since it was read is left alone. The smoke test checks
    two writes with `--dry-run` only, so it never changes the vault.
  - **Step 4: meeting identity.** Event summaries (`outlook_calendar_list_events`) now
    carry `ical_uid`, `series_master_id`, `type`, `is_organizer`, `response` (the user's
    own: Accepted, Declined ...) and `online_meeting_url` (from `OnlineMeetingUrl`, else
    `OnlineMeeting.JoinUrl`), read from what `calendarview` already returns; the query
    tool's default select gains `ICalUId`, `IsOrganizer` and `ResponseStatus`. **To check
    live:** the smoke test now expects every event to have an `ical_uid`; also look at
    whether `online_meeting_url` is filled for a Teams meeting (REST v2 may leave both
    empty, in which case the link is only in the body).
  - **Step 5: sync.** `TaskSource` (Jira through `[jira] tasks_jql`; Bitbucket pull
    requests awaiting the user's review) and `MeetingSource` (Outlook calendar) in
    `capabilities.py`, `ToolContext.peers_of`, and `obsidian_sync`: made once and found by
    key, only its own fields, never deletes, skips and reports a source that fails or a
    note edited meanwhile; `call obsidian_sync` runs it without an agent. New settings:
    `[obsidian] sync_days_ahead` (1), `meeting_tag` (`meeting`), `[jira] tasks_jql`.
    Found and fixed while testing: a second run removed `priority` from Bitbucket review
    tasks (the source has none), and declined meetings were counted as up to date. **To
    check live:** `call obsidian_sync --dry-run` on the laptop with Jira, Bitbucket and
    the calendar on; then for real, twice, and see the second run change nothing.
  - **Step 6: recipes and docs.** `contrib/goose/plan-day.yaml`, `meeting-prep.yaml`
    and `wrap-day.yaml` (checked against the Goose recipe reference, now at
    goose-docs.ai: title, description, prompt, optional parameters with defaults;
    `tests/test_recipes.py` holds them to it and checks every tool they name exists),
    and `docs/workflow.md` (setup, the loop, cron, Shell Commands, where notes land).
    Added for the prep recipe: a note can be named by key (`ical_uid:...`,
    `jira:ABC-123`) wherever a path or `[[link]]` is taken. **To check on the laptop:**
    whether a recipe with no `extensions` block sees the `productivity` extension (the
    Goose docs do not say; `docs/workflow.md` has the fix if not), then run each recipe
    once with `-s`.
  - **First live run (2026-11-19, build `v0.9.0-10-g621435e`, work laptop):** `doctor`
    passed for every product, including the vault (26 notes, 6 tasks, America/Chicago,
    `Journal/YYYY/MMM/YYYY-MM-DD` read from Obsidian's settings), and the smoke test ran.
    The dry-run sync read Jira, Bitbucket and the calendar with nothing unavailable and
    would make 14 Jira task notes. **Two bugs found and fixed the same day:** (1) every
    event's `ical_uid` was empty, so no meeting got a note: the API names it `iCalUId`
    (camelCase), not `ICalUId`; the join link, type and response were all there. (2) A
    Templater template (`Templates/Meeting Template.md`) was reported as broken YAML;
    template folders are now skipped. **To re-check:** the smoke test's "calendar events
    have a meeting key", and a dry-run sync showing meeting notes. **Config note for the
    user:** `meeting_date_format = "YYYY-MM-DD/MM/YYYY-MM-DD"` gives
    `Meetings/2026-11-19/11/...`; `"YYYY/MM/YYYY-MM-DD"` gives the asked-for
    `Meetings/2026/11/2026-11-19 <title>`.
  - **Asked for after the first live run (2026-11-19):** synced task notes are named
    `<key> <slug>` (`ELK-928 review-il5-ansible-and.md`), the slug cut at
    `[obsidian] task_title_length` (25) at a word; `obsidian_create_task` with a key
    names its note the same way. And `docs/index.md` lists Obsidian in its product table.
  - **Second live run (2026-11-19):** a real sync worked and was idempotent on the user's
    data; renaming a meeting note's file changed nothing on the next sync. **Gap found:**
    cancelling a meeting in Outlook did not reach its note, because the meeting leaves
    `calendarview` (the user's diagnosis). Fixed the same day: notes dated in the window
    whose meeting did not come back are looked for a week either side, and marked
    `cancelled: true` when not found. **To re-check:** cancel (or decline) a meeting that
    has a note, sync, and see `cancelled: true` with the report's note.
  - **Templates and buttons (2026-11-19, asked for):** `[obsidian] meeting_template`, the
    `prep` markers in the default meeting body, templates in `contrib/obsidian/`, and
    `contrib/macos/` (`sync.sh`, `run-recipe.sh`, a launchd agent running the sync every
    30 minutes). The user had found the Shell Commands plugin unmaintained and wants
    whatever works; Runsh and Lodestone could not be confirmed in the community list, so
    nothing depends on a particular plugin. **To check on the laptop:** the launchd agent
    reads the vault in `~/Documents` (macOS may ask for Full Disk Access), the
    notification shows, and `run-recipe.sh plan-day` opens iTerm2 with Goose.
  - **Chat pane works (2026-11-19, user):** the Agent Client plugin, set up for Goose over
    ACP, planned the day from inside Obsidian, and on the user's word commented on and
    closed a Jira issue that was already fixed. The user noted the plan was thin because
    few tasks have due dates: plan-day leans on due dates (see below).
  - **Time blocks and ranking (2026-11-19, asked for):** `[obsidian] timeblock_minutes`
    (25) and `max_timeblock_minutes` (50); each listed task carries `estimate_minutes`
    and `block_minutes`; the sync brings Jira's estimate in as `source_estimate`; plan-day
    ranks by carried over, due soon, in progress, high priority, current sprint, reviews.
    Corrected the same day: a task with only a due date gets `timeblock_minutes`, not
    the longest block. Breaks added: `[obsidian] break_minutes` (5) and
    `long_break_minutes` (10, after any block longer than `timeblock_minutes`); each task
    carries its `break_minutes`. **To check on the laptop:** plan a day from the chat pane and see
    25-minute blocks, and estimated tasks capped at 50, around the meetings.
  - **Third live run (2026-11-20, user):** the sync pulls tasks and is idempotent; planning
    over ACP works; the calendar smoke check passes after the `iCalUId` fix; a cancelled
    meeting's note was marked cancelled; the plan used the time-block rules. The launchd
    agent cannot be run on the work laptop (not allowed there), but `contrib/macos/sync.sh`
    run by hand works, so the button route is the one to use there. **Bug found and fixed:**
    a task marked done in TaskNotes was reopened by the next sync (Jira still said in
    progress); a status changed in Obsidian is now kept until the source moves, detected
    with `source_status`, and wrap-day offers to close such tasks in Jira. **Validated by the
    user (2026-11-21).** Also fixed: a
    test that only passed on 2026-11-19 (it read the real date through `doctor`).
  - **Decided with the user:** TaskNotes' standard field names stay; the stable keys
    (`jira`, `bitbucket`, `servicenow`, `ical_uid`, `series_id`, `uid`) are extra
    frontmatter. Writes will be atomic and skip a note changed since it was read.
  - **To check on the laptop:** `doctor` and the smoke test against the real vault
    (add an `[obsidian]` table with `meeting_date_format = "YYYY/MM/YYYY-MM-DD"`), that
    `doctor` shows `Journal/YYYY/MM/YYYY-MM-DD` read from Obsidian's settings and the
    right time zone, that `obsidian_get_daily_note day=yesterday` finds yesterday's note,
    and that `obsidian_list_tasks` sees the same tasks TaskNotes shows. Tell me if your
    TaskNotes identifies tasks by a property rather than a tag; that is a TaskNotes
    setting this does not read yet.
  - Properties are parsed once per note and kept until the file changes, and the task
    tools skip notes that cannot be tasks: about 5 s per call on a 5,000-note vault
    became 0.84 s for the first and 0.11 s after (DESIGN.md, Obsidian specifics).
  - Tests: 838 passing, about 99.98% branch coverage, all hooks clean.
  - **Next action: merge the pull request, then tag `v0.10.0` on the commit it lands as
    on `main`.** The branch was squashed into one commit on 2026-11-21 at the user's
    request, so that commit also carries the version change. Every laptop check listed
    above has passed, except the launchd agent, which the work laptop does not allow.
- **0.9.0, on `main` via [#18](https://github.com/dcode/productivity-agent-tools/pull/18)
  (2026-11-19, merged as one commit, 39b34d8). Tagged `v0.9.0` and `v0.8.0` (confirmed by
  the user, 2026-11-19).**
  Asked for on 2026-11-19: make credentials and the user-facing text match what the project
  has become. All defaults below were agreed before the work started.
  - **`token_cmd`** (new): a command that prints a token (`sops`, `pass`, `op`), at the
    top level, in a group or in a product table; no shell, 30 s limit, output never
    logged, `{product}` replaced by the token name. Lookup order is now environment,
    `token_cmd`, env file, keychain; the not-found error lists where it looked.
  - **`token_env_file` per product and per group**, not only at the top; each source
    resolves from the most specific level. **`$HOME` / `${VAR}` now expand** in paths and
    commands (the cause of `${HOME}/.secrets/jira_token.env` not being found); an unset
    variable is an error naming the key.
  - **`set-token` with no name** lists the tokens and asks (number or name); without a
    terminal it fails with the list. It no longer defaults to Jira.
  - **`set-token microsoft`** stores a hand-captured refresh token (`--from-response`,
    `--verify`); **`login-microsoft` is deprecated** (hidden, prints a note). Remove it in a
    later release.
  - **Text audit:** CLI help (one placeholder was being swallowed by the help renderer),
    error messages, agent instructions (Atlassian products now say what to do on a bad
    token, as ServiceNow and Outlook already did), the six status tools, README, DESIGN,
    config examples and package metadata. "macOS Keychain" became "the system keychain".
    Deliberately left: the status models' field names (`token_source`, and
    `session_source` for ServiceNow) since agents and scripts read them, and the
    `scripts/*_probe.py` spikes, which are history.
  - **Checked live (2026-11-19, `doctor` on the work laptop):** every product passed.
    `token_env_file = "${HOME}/.secrets/jira_token.env"` (the case that failed) and
    `"~/.secrets/bitbucket_token.env"` both found their tokens, and `token_cmd` (a `cut`
    over an env file) supplied Confluence's. **Bug found and fixed the same day:**
    `token_cmd = "awk -F= '{ print $2 }' ${HOME}/..."` was refused as "$2 is not set",
    because every word was expanded after splitting, by which point the single quotes
    were gone. A string is now expanded the way a shell would (nothing inside single
    quotes) before splitting, and a list is taken exactly as written.
    The `awk` line was only a test that `token_cmd` works; it would also have cut a
    token at its first `=` padding character. Asked for after it: **`{product}` in
    `token_env_file`** (done the same day), so one line,
    `token_env_file = "~/.secrets/{product}_token.env"`, serves every product; a
    product with no file of its own falls through to the keychain.
  - **In use now (2026-11-19): all tokens are in the keychain** on the work laptop, so
    neither `token_cmd` nor `token_env_file` is needed there day to day.
  - **Still to check live:** a real `sops` setup (waiting on helpdesk approval to allow
    it), and `set-token` with no argument in a real terminal.
  - **Documentation site (2026-11-19, same branch):** the README had grown to ~760
    lines, so it now covers only what this is, a quickstart and pointers, and the rest
    moved to a Material for MkDocs site built from `docs/` (`mkdocs.yml`): getting
    started, configuration, authentication, a page per product with its tools, the
    command line, agents and development. CI builds it with `--strict` on every pull
    request; `.github/workflows/docs.yml` publishes it to GitHub Pages on pushes to
    `main`. `DESIGN.md` and this file stay in `docs/` but are **excluded from the
    site** (`exclude_docs`), because they record facts about the work deployment
    (internal host name, banner cookie, tenant); site pages link to them on GitHub.
    **To do (you):** the repository is private, so Pages needs a paid plan, and on
    GitHub Pro the published site is public even though the repo is not. If that is
    fine, turn Pages on (Settings, Pages, Source: GitHub Actions); until then the docs
    workflow fails at the deploy step and the README's site link is dead, though
    `docs/` reads fine on GitHub. **Later:** Material for MkDocs reaches end of life on
    2027-05-05; its successor, Zensical (same team, reads `mkdocs.yml`), was at 0.1.2
    when this was set up. Revisit before then.
  - **Merged** as one commit, 39b34d8, which also carries the version bump, so that is
    where `v0.9.0` goes.
- **Version:** 0.12.0 on the branch (catalog requests); 0.11.0 on `main` (#20).
  Before that, 0.8.0, on `main` via
  [#17](https://github.com/dcode/productivity-agent-tools/pull/17) (2026-11-19, rebase
  merge, so the branch's commit ids differ from `main`'s). **To do: tag `v0.8.0` at
  f45647f** (the bump commit on `main`), from the laptop. Tags:
  `v0.5.0` at 804f319, `v0.6.0` at 9be367b, `v0.7.0` at 0235736, `v0.11.0` at 3f25eb1.
  **Tagging rule (the user, 2026-11-23): a release tag goes on the commit where that
  release's code is complete and working, normally the last commit it lands with on
  `main`, not the commit that changes `__version__`.** Earlier tags followed the old
  rule (the bump commit) and stay where they are. Cloud sessions cannot push tags, so
  tag from the laptop.
- **0.8.0 (on `main` via #17), all confirmed live on the work laptop.
  `login microsoft` (2026-11-18):** the Outlook sign-in now works
  like `login servicenow`. It opens
  Chrome/Edge on a temporary profile at the Microsoft authorize page (PKCE + `state`), the
  user signs in as usual (smart card stays in the browser), and the tool reads the
  redirect's `code` from the tab list over the DevTools pipe (no page attach), redeems it
  itself, and stores the refresh token. Replaces "capture the token in DevTools and paste
  it" for the daily ~18 h re-sign-in; `login-microsoft` (paste) stays as the fallback.
  The browser driver moved to `productivity_agent_tools/browser.py`, shared by both.
  **Confirmed on the work laptop (2026-11-18):** `login microsoft` signed in from the
  temporary profile, `doctor` passed, and Goose answered "how many unread email messages
  do I have" by picking `outlook_mail_list_messages` on its own (10 unread, correct).
  Later the same day calendar reads, mail search by sender, reading a message, and the
  `Received` headers all worked through Goose.
  **Live sign-in status (fixed 2026-11-18, same branch):** asking "when does my token
  expire" showed the same "seeded at" before and after a second `login microsoft`. Not a
  failed pickup: the status string was fixed at server start, and `obtained_at` is
  overwritten on every hourly rotation, so the sign-in time was not kept at all. Now the
  session stores `signed_in_at` and `[microsoft] sign_in_hours` (18 in contrib), and the
  status tools read the Keychain when called (`Credential.describe`), reporting sign-in
  time, "usable until about …", last renewal and access-token expiry. Also fixed: a
  renewal in flight during `login microsoft` could overwrite the new sign-in.
  **Re-checked live (2026-11-18): passes** — a second `login microsoft` while Goose ran
  showed the new sign-in time in the same session. A session stored before this change
  has no `signed_in_at` or window until the next `login microsoft`.
  **OData query tools (new, 2026-11-18, same branch; the main cases confirmed live):**
  `outlook_mail_query_messages` and `outlook_calendar_query_events` pass `$filter`,
  `$select`, `$orderby`, `$expand`, `$top`, `$skip` and `$count` through (GET only), so
  a message can be found by `InternetMessageId`. Asked for after reading headers by
  iterating messages. Header contents are not filterable (API limit). See DESIGN.md,
  Outlook REST specifics.
  **Smoke test covers Outlook and ServiceNow (2026-11-18):** `scripts/smoke-test.sh` now
  runs every Outlook tool and `servicenow_status`, chaining real values (an inbox
  message's `InternetMessageId` is looked up again, PascalCase and camelCase; page 2 is
  checked against page 1), asserts results with `expect`, and can record API behaviour
  with no right answer as "observed" (`observe`; none left after 2026-11-19). New `.env`
  value:
  `OUTLOOK_SEARCH`. **Run on the work laptop (2026-11-19): every check passed**, with the
  expected skips (products not configured there).
  Tagged `v0.8.0` (2026-11-19). The one open check is the 18 h expiry
  (needs a lapsed window; see "Confirm against the real servers"). Settled
  2026-11-19: a filter plus a sort on another property is accepted (now a normal smoke
  check), and the full header set comes back.
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
- **Tests:** 968 passing, 100% branch coverage. All hooks clean.
- **In use:** Goose on the work laptop, against Jira 11.3.12, Bitbucket 10.2.8 and Confluence
  10.2.19. No CI
  server (Bamboo or other) reports builds or deployments to this Bitbucket.
- **Last smoke run:** 2026-11-19 on the 0.8.0 branch, with the Outlook and ServiceNow
  checks: all passed, the skips expected. Before that, build `v0.2.0-3-g721e8b3`
  (2026-11-12): all 41 Atlassian calls answered, none failed.
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
  **Next action: the laptop checks for incidents (see the top of this list), then the
  catalog reads under "Later", step 3.** The slower session checks
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
- [x] CLI: `serve`, `doctor`, `set-token` (asks which when unnamed), `login`, `export-ca`,
      `call` (with `--field`), `configure-agent` (Goose)
- [x] Token sources: environment, `token_cmd`, env file, keychain; command and file set
      per product, per group or at the top; `$VAR` and `~` expanded
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
  - [x] The smoke test's incident checks: list, get, and the dry-run create show you
        as the caller (2026-11-23)
  - [x] One real `servicenow_create_incident`: accepted, `INC0162364`, read back with
        `servicenow_get_incident`, cancelled by the user (2026-11-23)
  - [x] `sys_choice` is readable: no, it returns no rows (2026-11-23), so the create
        submits the portal's form instead
  - [ ] `servicenow_get_incident_form` reads the form, and a real create through it
  - [x] `servicenow_get_incident` on an incident with comments: readable (2026-11-23)
  - [ ] With a top-level `[cookies]` table, add an empty `[servicenow.cookies]` so those
        cookies are not sent to the helpdesk
- [x] `login microsoft` (browser sign-in) on the work laptop. **Worked first time
      (2026-11-18)**, and every check below passed:
  - [x] Conditional Access accepts a sign-in from a **fresh temporary profile** (the
        ServiceNow SSO did, but AAD may want a compliant/registered device, which an
        empty profile cannot prove). Failure shows as an `AADSTS53000`/`53003` error in
        the redirect, reported as "The Microsoft sign-in failed: …".
  - [x] The authorize page accepts our request for the OWA client (`response_mode=fragment`,
        PKCE S256, `openid profile offline_access` + the Outlook `.default`).
  - [x] `Target.getTargets` / `Target.setDiscoverTargets` show the redirect **with its
        fragment** before `oauthRedirect.html` rewrites it. If the debug log shows only
        "no tab came back", this is the suspect.
  - [x] The code redeems (Origin header, same as the refresh) and `doctor` works without
        any paste.
  - [x] A second `login microsoft` while `serve` runs is picked up without a restart.
        First try (2026-11-18) was inconclusive (the status was a start-up string); on
        the fixed build (686c4c1) the status in the same Goose session showed the new
        sign-in time at once. Passed 2026-11-18.
- [ ] Outlook REST (`outlook_calendar`/`outlook_mail`), after `login microsoft` (or the
      `login-microsoft` paste fallback) on the work laptop. First live run 2026-11-15:
      `login-microsoft` seeded and the token is
      **accepted** (redemption works, no 401), but `GET /me` returned 404 because both
      Outlook tables were **empty**, so they inherited the top-level Atlassian `base_url`
      (stormwind) and the `dodacknowledged` cookie — the request went to stormwind, not
      Outlook. **Fixed by the group layer:** put the Outlook host in `[group.microsoft]`
      and `group = "microsoft"` on each product (contrib updated), so the top-level
      Atlassian settings no longer leak. **Re-run 2026-11-18 after `login microsoft`:**
      `doctor` passed and a Goose session read the mailbox (below).
  - [ ] `doctor`: both products authenticate `GET /me` and name you (one seeded
        `[microsoft]` token, shared). Confirm the shared-credential path mints the Outlook
        token once, not once per product. (Note: `doctor` redeems per product in sequence,
        so the two "seeded at" times differ by the rotation between them — that is expected
        and confirms the token is shared.)
  - [x] `call outlook_calendar_status` and `outlook_mail_status` (smoke test, 2026-11-19;
        both report a live "signed in at").
  - [x] Calendar via Goose (2026-11-18): found the 3 meetings in the next 7 days.
  - [x] `outlook_calendar_list_calendars`, `outlook_calendar_list_events` (every start in
        UTC, asserted) and `outlook_calendar_get_event` (smoke test, 2026-11-19); the
        3-meeting count matched through Goose the day before.
  - [x] Under Goose, "how many unread email messages do I have" chose
        `outlook_mail_list_messages` unprompted and answered 10, which was correct
        (2026-11-18).
  - [x] Mail via Goose (2026-11-18): found messages from a named sender and showed the
        most recent one's body.
  - [x] `outlook_mail_list_messages` (unread filter asserted), `outlook_mail_search_messages`
        on a known term, and `outlook_mail_get_message` with truncation asserted (smoke
        test, 2026-11-19).
  - [x] `outlook_mail_get_message_headers` through Goose (2026-11-18): the `Received`
        chain came back and named the first receiving server, and messages with
        `Authentication-Results` and `List-Unsubscribe` were found by iterating messages
        and reading their headers.
  - [x] `outlook_mail_get_message_headers` returns the **full header set** (every header,
        not just the well-known names), confirmed 2026-11-19.
  - [x] OData query tools (`scripts/smoke-test.sh` runs and asserts these; passed
        2026-11-19):
    - [x] `outlook_mail_query_messages` with
          `filter=InternetMessageId eq '<a real Message-ID>'` found the message
          (2026-11-18). The result had `returned`, `total`, `more_available`,
          `next_skip`, `values_truncated` and `items`, as designed.
    - [x] `count=true` reports `total`; `skip=<next_skip>` gives a next page with no
          overlap.
    - [x] `folder=junkemail` reads the Junk folder.
    - [x] A filter plus a sort on another property (`IsRead eq false` with
          `ReceivedDateTime desc`) is **accepted** (2026-11-19), despite Graph's documented
          rule; the tool docstrings now give it as advice for an `InefficientFilter`, and
          the smoke test checks it as a normal call.
    - [x] camelCase (`internetMessageId`, as in Graph) is accepted too (2026-11-18);
          results still come back PascalCase (`Id`, `Subject`, `InternetMessageHeaders`).
    - [x] `select=Subject,InternetMessageHeaders` on a collection returns the headers for
          every item (2026-11-18), so triage pages through mail reading headers in one
          request per page. The extended-property fallback (`String 0x007D`) is not needed.
    - [x] `outlook_calendar_query_events` with `filter=Type eq 'SeriesMaster'` returns
          only series.
  - [ ] Confirm a 401 (let the 18 h sign-in window lapse) is reported as "run
        login microsoft", and that signing in again fixes a running server without a
        restart.

## Next up

Rough order. Each item is one pull request. API references: the Jira 11.0.2 and Bitbucket
10.5 OpenAPI specs (`https://dac-static.atlassian.com/server/jira/platform/jira_software_dc_11002_swagger.v3.json`,
`https://dac-static.atlassian.com/server/bitbucket/10.5.swagger.v3.json`).

1. One real story in the new epic and one real branch from an issue (see the checks above).
2. **Obsidian vault** (design in DESIGN.md, Roadmap, "Obsidian vault"), in order:
   1. [x] Product kinds (`RemoteProduct`, `LocalProduct`) and per-product `dry_run`
      (on the branch, see above).
   2. [x] `obsidian` product, reads: `obsidian_status`, `obsidian_list_tasks`,
      `obsidian_get_note`, `obsidian_find_task` (by key, or near matches by title);
      `doctor` checks the vault (on the branch, see above).
   3. [x] `obsidian` writes behind its own `dry_run`: `obsidian_create_task` (refuses an
      existing key, returns near matches), `obsidian_schedule_task`,
      `obsidian_write_daily_section` (between markers); atomic writes, skip a note
      changed since it was read. (The daily-note and meeting layouts and the time zone
      are done; see above.)
   4. [x] Meeting identity on calendar events (live check pending): `ICalUId`,
      `SeriesMasterId`, `Type`, `IsOrganizer`, `ResponseStatus` and the join link on the
      event summary, so a meeting has a key that lasts across days and declined meetings
      can be left out.
   5. [x] `TaskSource` and `MeetingSource` capabilities (Jira through a preset, Bitbucket
      pull requests awaiting review, the Outlook calendar), `ToolContext.peers_of`, and
      `obsidian_sync`: create or update by key, sync-owned fields only, never delete.
   6. [x] Goose recipes (plan the day, prepare a meeting, wrap up the day) and a page in
      the docs on running them from Obsidian (Shell Commands plugin) and `call
      obsidian_sync` from cron.

## Later

- **Delta queries for the calendar (and mail), with Graph** — discussed 2026-11-19, kept
  for later by the user. Microsoft's recommended way to see changed and deleted events is
  `calendarView/delta`, repeating the returned token. Not done now because the token is
  bound to a fixed start and end while the sync's window moves daily (so little saving),
  a deleted event comes back as `@removed` with only its `Id` (notes would need to store
  an immutable event id), the token needs a local state store and handling for expiry,
  and the calendar is still on the Outlook REST v2.0 stopgap. The sync instead looks a
  week either side for meetings that left the window; its one gap is a meeting moved more
  than 7 days away, marked cancelled until it comes back into range. **Middle step if
  that gap matters:** keep `event_id` (with `Prefer: IdType="ImmutableId"`) in each
  meeting note and check a missing one with `GET /me/events/{id}` (404 means deleted).
  **Plan:** delta for calendar and mail together when moving to Graph, with one shared
  token store.
- **GitLab (self-managed)**: `gitlab_wiki` and `gitlab_scm` products sharing a `gitlab`
  token, a GitLab `ApiClient` adapter (error format differs from Atlassian's), and a
  Jira–GitLab integration if issues are tracked in Jira. Not started; the plugin structure
  was built for it. Open question: does `jira_get_issue_pull_requests` become an
  integration per code host, or a capability (`CodeHost`) that every code host offers?
  **Leaning (2026-11-19): a capability**, as `TaskSource` / `MeetingSource` did for the
  Obsidian sync; see DESIGN.md, Overall shape.
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
     page attach, which the work laptop's Chrome refuses. **Confirmed live
     (2026-11-18)**: sign-in, `doctor` and a Goose mail query all worked.

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
  needs a client that an instance admin registers. **Status: sign-in, status and
  incidents (list, read, create) built; catalog requests next.**

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
  3. **`servicenow` product, reads and writes.** **Incidents done (2026-11-23, 0.11.0):**
     `servicenow_list_incidents`, `servicenow_get_incident`, `servicenow_get_incident_form`,
     `servicenow_create_incident` through the form (see "Where things stand"). Still to
     build, next: my requests and items with stage and approval, one item in detail with
     comments, catalog search, and any item's form (`servicenow/forms.py` reads one
     already).
  4. **More writes behind `dry_run`:** order an item (`order_now` with the form's
     variables, checked by the same `forms.py`), add a comment to an incident or item.
     Not probed beyond what the incident create checks will show.

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
