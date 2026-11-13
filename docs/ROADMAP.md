# Roadmap

Read this first when coming back to the project. The design and the reasons behind it are in
[DESIGN.md](DESIGN.md). Update this file in the same commit as the work it describes.

Last updated: 2026-11-12.

## Where things stand

- **Version:** 0.3.0 in the code, not yet tagged. The last release is `v0.2.0`. Tag
  `v0.3.0` on `main` to release (see README, Releasing).
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
- **In review:** nothing.
- **Tests:** 328 passing, about 99.9% branch coverage. All hooks clean.
- **In use:** Goose on the work laptop, against Jira 11.3.12, Bitbucket 10.2.8 and Confluence
  10.2.19. No CI
  server (Bamboo or other) reports builds or deployments to this Bitbucket.
- **Last smoke run:** build `v0.2.0-3-g721e8b3`, 2026-11-12: all 41 calls answered, none
  failed. The dry-run branch create and the dry-run Epic create both work.

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
  - [ ] An edit with a stale `version`, to see it refused on the real server
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

## Next up

Rough order. Each item is one pull request. API references: the Jira 11.0.2 and Bitbucket
10.5 OpenAPI specs (`https://dac-static.atlassian.com/server/jira/platform/jira_software_dc_11002_swagger.v3.json`,
`https://dac-static.atlassian.com/server/bitbucket/10.5.swagger.v3.json`).

1. One real story in the new epic and one real branch from an issue (see the checks above).
2. Tag and push `v0.3.0` from `main` (#11 is merged; the tag publishes the release).

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
- **Microsoft Graph adapter** (Microsoft 365 calendar and mail): a new `ApiClient` with its
  own sign-in, plus a product module. Needs its own design interview first, covering
  authentication and data handling.
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
