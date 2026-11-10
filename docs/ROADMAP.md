# Roadmap

Read this first when coming back to the project. The design and the reasons behind it are in
[DESIGN.md](DESIGN.md). Update this file in the same commit as the work it describes.

Last updated: 2026-11-10.

## Where things stand

- **Version:** 0.1.0, not yet tagged.
- **Merged to `main`:**
  - [#1](https://github.com/dcode/productivity-agent-tools/pull/1): Jira server, CLI,
    CI, `configure-agent`
  - [#2](https://github.com/dcode/productivity-agent-tools/pull/2): read-only
    Bitbucket, `call`, opt-in products, smoke test
- **Tests:** 184 passing, about 99.9% branch coverage. All hooks clean.
- **In use:** Goose on the work laptop, against Jira 11.3 and Bitbucket 10.2.8.

## Done

- [x] Jira: status, presets, search, issue detail, transitions; add comment, transition (writes)
- [x] Bitbucket (read-only): status, projects, repositories, pull-request lists, pull-request
      detail, commits, changes, diff, code search, default project
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

## Next up

Rough order. Each item is one pull request.

1. **Bitbucket writes:** comment on a pull request, approve, mark needs-work. Use the
   existing dry-run and repeat suppression, and add MCP write annotations. Write tools
   become available only when Bitbucket is configured.
2. **More Jira writes**, from the scope deferred in the design interview: log work, assign,
   edit fields, create issue. Field rules vary by project, so start with log work and assign.
3. **Link Jira and Bitbucket:** pull requests linked to an issue, and issues mentioned in a
   pull request. Needs both products configured.

## Later

- **Confluence** product module (read and search first) on the same adapter.
- **OpenTelemetry** traces of httpx requests and responses, alongside structlog.
- **Microsoft Graph adapter** (Microsoft 365 calendar and mail): a new `ApiClient` with its
  own sign-in, plus a product module. Needs its own design interview first, covering
  authentication and data handling.
- Tag and release `v0.1.0` once the checks above are confirmed.

## Open questions

- None outstanding. Add any that come up here, rather than leaving them in chat.
