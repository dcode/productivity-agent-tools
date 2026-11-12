# Roadmap

Read this first when coming back to the project. The design and the reasons behind it are in
[DESIGN.md](DESIGN.md). Update this file in the same commit as the work it describes.

Last updated: 2026-11-12.

## Where things stand

- **Version:** 0.1.0, tagged `v0.1.0` at 92a7d95 (the design-record commit) and released.
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
- **In progress:** products as entry-point plugins, then neutral names for the shared
  error and adapter classes (branch `claude/beautiful-hamilton-k4u2un`). Restructuring
  only; no tool or config-format changes except that an unknown product table is now an
  error.
- **Tests:** 281 passing, about 99.9% branch coverage. All hooks clean.
- **In use:** Goose on the work laptop, against Jira 11.3 and Bitbucket 10.2.8.

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

## Confirm against the real servers

These have automated tests but have never run against the live servers. Check each when the
chance comes up and tick it here.

- [ ] Jira comments: run `call jira_get_issue key=<ISSUE>` on an issue that has comments and
      compare the result with the browser
- [ ] Bitbucket pull-request comments: run `call bitbucket_get_pull_request` on a pull
      request that has comments
- [ ] Bitbucket build results, on a pull request whose commits have a CI build
- [ ] A moved or renamed file in `bitbucket_get_pull_request_changes`
- [ ] `bitbucket_search_code`, once the server's search index stops returning 503
- [ ] `call --field` colouring, viewed in a real terminal
- [ ] The new Bitbucket reads, through `scripts/smoke-test.sh` (fill in the new placeholders):
  - [ ] `bitbucket_get_pull_request_merge_status` on a pull request blocked by a check
  - [ ] `bitbucket_get_pull_request_activity`: check that REVIEWED is shown for needs-work
  - [ ] `bitbucket_list_pull_request_tasks` with `state=all` on a pull request with tasks
  - [ ] `bitbucket_get_pull_request_issues` (needs the Bitbucket-to-Jira application link)
  - [ ] `bitbucket_get_commit_builds`: that the deprecated `build-status` resource still
        answers on 10.2.8, and which of the newer fields (tests, duration) it sends
  - [ ] `bitbucket_list_required_builds`
  - [ ] `bitbucket_get_deployment`, if the CI/CD server reports deployments; also check that
        `updated` is a sensible date (seconds vs milliseconds, see DESIGN.md)
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
  - [ ] `jira_get_edit_fields` and `jira_get_create_fields` on the projects in daily use
        (which fields are on the screens, and whether createmeta answers on 11.3)
  - [ ] One real `jira_edit_issue` (a label and the priority) and one real
        `jira_create_issue` in a test project
  - [ ] One real `jira_link_issues` with "blocks": **confirm the direction** in the browser
        (the first issue should say "blocks", the second "is blocked by")
  - [ ] `jira_transition_issue` with a resolution, on a workflow whose Done screen has one
- [ ] Jira and Bitbucket together:
  - [ ] `jira_get_issue_pull_requests` on an issue with a pull request linked only by branch
        name: both sources listed under `sources`, and the pull request found by the panel.
        If `rest/dev-status` fails, paste the error (its shape is undocumented).
  - [ ] `bitbucket_get_pull_request_issues` shows summaries and statuses
- [ ] Bitbucket writes:
  - [ ] `bitbucket_status` shows your `user_slug` (the `applinks/whoami` servlet accepts a
        bearer token and gets past the banner proxy); if not, set `user_slug` and note it
  - [ ] The smoke test's two dry-run writes show the request they would send
  - [ ] One real comment and one real approve and unapprove on a test pull request, with
        `--no-dry-run`, compared in the browser

## Next up

Rough order. Each item is one pull request. API references: the Jira 11.0.2 and Bitbucket
10.5 OpenAPI specs (`https://dac-static.atlassian.com/server/jira/platform/jira_software_dc_11002_swagger.v3.json`,
`https://dac-static.atlassian.com/server/bitbucket/10.5.swagger.v3.json`).

1. **Deployment listing** (being done by hand on the work laptop): Bitbucket's public API cannot list
   deployments, but its commit page shows them, so the page uses some internal endpoint.
   Find it with the browser's network tab on the work laptop and decide whether to use it
   (as code search does) for a `bitbucket_list_deployments`.

## Later

- **GitLab (self-managed)**: `gitlab_wiki` and `gitlab_scm` products sharing a `gitlab`
  token, a GitLab `ApiClient` adapter (error format differs from Atlassian's), and a
  Jira–GitLab integration if issues are tracked in Jira. Not started; the plugin structure
  was built for it. Open question: does `jira_get_issue_pull_requests` become an
  integration per code host, or a capability (`CodeHost`) that every code host offers?
- **Confluence** product module (read and search first) on the same adapter. The 10.2.20
  OpenAPI spec is at
  `https://dac-static.atlassian.com/server/confluence/10.2.20.swagger.v3.json`.
- **OpenTelemetry** traces of httpx requests and responses, alongside structlog.
- **Microsoft Graph adapter** (Microsoft 365 calendar and mail): a new `ApiClient` with its
  own sign-in, plus a product module. Needs its own design interview first, covering
  authentication and data handling.
- **Custom Jira fields** (`customfield_*`, such as story points and epic links), read and
  set through the same screen metadata.
- **More Bitbucket writes:** comments on a file or line of the diff (needs the anchor's
  path, line, line type and file type), resolving and reopening tasks, editing or deleting
  the user's own comments.
- Release `v0.2.0` (the Bitbucket reads from
  [#3](https://github.com/dcode/productivity-agent-tools/pull/3) and the Bitbucket writes) once the checks
  above are confirmed.

## Open questions

- None outstanding. Add any that come up here, rather than leaving them in chat.
