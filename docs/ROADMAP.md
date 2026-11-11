# Roadmap

Read this first when coming back to the project. The design and the reasons behind it are in
[DESIGN.md](DESIGN.md). Update this file in the same commit as the work it describes.

Last updated: 2026-11-11.

## Where things stand

- **Version:** 0.1.0. The `v0.1.0` tag marks `main` at 92a7d95 (the design-record commit).
  It was made in a cloud session that could not push tags; if `git ls-remote --tags origin`
  does not show it, push it from a local clone (see Open questions).
- **Merged to `main`:**
  - [#1](https://github.com/dcode/productivity-agent-tools/pull/1): Jira server, CLI,
    CI, `configure-agent`
  - [#2](https://github.com/dcode/productivity-agent-tools/pull/2): read-only
    Bitbucket, `call`, opt-in products, smoke test
  - [#3](https://github.com/dcode/productivity-agent-tools/pull/3): Bitbucket builds,
    deployments and more pull-request reads
  - [#4](https://github.com/dcode/productivity-agent-tools/pull/4): Bitbucket pull-request
    writes, shared `writes.py`
- **In review:** Jira log work and assign (branch `claude/beautiful-hamilton-k4u2un`)
- **Tests:** 232 passing, about 99.9% branch coverage. All hooks clean.
- **In use:** Goose on the work laptop, against Jira 11.3 and Bitbucket 10.2.8.

## Done

- [x] Jira: status, presets, search, issue detail, transitions, worklogs; add comment,
      transition, log work, assign (writes)
- [x] Bitbucket (read-only): status, projects, repositories, pull-request lists, pull-request
      detail, commits, changes, diff, code search, default project
- [x] Bitbucket pull requests: merge status (blocking checks), activity history, tasks, linked
      Jira issues
- [x] Bitbucket builds and deployments: richer build results (key, description, tests, ref,
      duration), builds for any commit with totals, required builds, one deployment by its
      identifiers
- [x] Bitbucket writes: comment, reply or task on a pull request; approve, needs-work or
      withdraw a review; user slug from `applinks/whoami` or `user_slug` in the config
- [x] Safety: `dry_run` on by default, MCP read/write annotations, 30-second repeat suppression
- [x] Connection: bearer tokens (environment variable, env file, Keychain), system trust store,
      generic extra cookies, per-product hosts and paths behind the `ApiClient` protocol
- [x] Diagnostics: structlog, debug HTTP logging without secrets, explained redirects and
      suspicious paths, `doctor` per product
- [x] CLI: `serve`, `doctor`, `set-token`, `export-ca`, `call` (with `--field`),
      `configure-agent` (Goose)
- [x] Project: hatch, uv, ruff, ty, rumdl, taplo, pre-commit, CI on Linux and macOS, tag
      releases, Dependabot, `scripts/smoke-test.sh`

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
- [ ] Bitbucket writes:
  - [ ] `bitbucket_status` shows your `user_slug` (the `applinks/whoami` servlet accepts a
        bearer token and gets past the banner proxy); if not, set `user_slug` and note it
  - [ ] The smoke test's two dry-run writes show the request they would send
  - [ ] One real comment and one real approve and unapprove on a test pull request, with
        `--no-dry-run`, compared in the browser

## Next up

Rough order. Each item is one pull request.

1. **More Jira writes, part 2:** edit fields and create issue. Field rules vary by project,
   so start by reading `issue/{key}/editmeta` and `issue/createmeta` and offering only what
   they allow. Needs a short design interview first (which fields, how to show them).
2. **Link Jira and Bitbucket:** pull requests linked to an issue. The other direction
   (issues linked to a pull request) is done as `bitbucket_get_pull_request_issues`. Needs
   both products configured.
3. **Deployment listing:** Bitbucket's public API cannot list deployments, but its commit
   page shows them, so the page uses some internal endpoint. Find it with the browser's
   network tab on the work laptop and decide whether to use it (as code search does) for a
   `bitbucket_list_deployments`.

## Later

- **Confluence** product module (read and search first) on the same adapter.
- **OpenTelemetry** traces of httpx requests and responses, alongside structlog.
- **Microsoft Graph adapter** (Microsoft 365 calendar and mail): a new `ApiClient` with its
  own sign-in, plus a product module. Needs its own design interview first, covering
  authentication and data handling.
- **More Bitbucket writes:** comments on a file or line of the diff (needs the anchor's
  path, line, line type and file type), resolving and reopening tasks, editing or deleting
  the user's own comments.
- Release `v0.2.0` (the Bitbucket reads from
  [#3](https://github.com/dcode/productivity-agent-tools/pull/3) and the Bitbucket writes) once the checks
  above are confirmed.

## Open questions

- **`v0.1.0` tag push:** the tag was created in a cloud session whose git proxy refused tag
  pushes. If it is missing on GitHub, run `git tag -a v0.1.0 92a7d95 -m "v0.1.0"` and
  `git push origin v0.1.0` from a local clone; the release workflow then publishes it.
