# Development

The design, the decisions behind it and what was rejected are in
[DESIGN.md](https://github.com/dcode/productivity-agent-tools/blob/main/docs/DESIGN.md);
what is done, what still needs checking against real servers, and what comes
next is in
[ROADMAP.md](https://github.com/dcode/productivity-agent-tools/blob/main/docs/ROADMAP.md).
Read the roadmap first when coming back to the project.

## Checks

Install the git hooks once after cloning:

```bash
uv run pre-commit install
```

The hooks lint and format Python (ruff), Markdown (rumdl) and TOML (taplo), and
type-check with ty. To run everything by hand:

```bash
uv run pre-commit run --all-files
uv run pytest
```

`pytest` measures branch coverage and fails below 90%; the suite sits just under
100%. Tests run against fakes of each server (and a stand-in browser); nothing
in them contacts a real server.

`scripts/smoke-test.sh` is the opposite: it runs every tool once against your
real servers through `call`, read-only or in dry-run, and checks what comes back.
Copy `.env.example` to `.env` first for the values it needs (an issue key, a
repository).

## The documentation site

This site is built with [MkDocs](https://www.mkdocs.org/) and
[Material for MkDocs](https://squidfunk.github.io/mkdocs-material/) from
`docs/` and `mkdocs.yml`:

```bash
uv run --group docs mkdocs serve           # preview at http://127.0.0.1:8000
uv run --group docs mkdocs build --strict  # what CI runs
```

`DESIGN.md` and `ROADMAP.md` live in `docs/` but are left off the site: they
record facts about the deployment this was built against.

## How the code is laid out

Product modules never make HTTP calls themselves. They talk to an `ApiClient`
(a protocol in `api.py`: `get`, `write`, `aclose`, plus `base_url` and
`dry_run`), and each kind of service has one adapter that implements it.
`HttpAdapter` in `transport.py` holds what every adapter shares: TLS trust,
extra cookies, dry-run, logging and connectivity errors. `AtlassianClient` in
`http.py`, `ServiceNowClient` in `servicenow/http.py` and `OutlookClient` in
`microsoft/outlook.py` add how their servers report results and failures.

An adapter is never handed a secret: it asks a credential provider
(`credentials.py`) for the authentication headers before every request: a fixed
bearer token for the Atlassian products, the stored browser session for
ServiceNow, and a renewed OAuth access token for Outlook. A new service with
different sign-in is a new adapter and a new product module; the existing
products do not change. Every adapter must honor `dry_run` in `write`.

The shared core is `transport.py`, `http.py`, `config.py`, `tokens.py`,
`credentials.py`, `browser.py` (the DevTools driver both browser sign-ins use)
and `products.py`. Each product lives in a sub-package (`jira/`, `bitbucket/`,
`confluence/`, `servicenow/`, and `microsoft/` for the two Outlook products)
whose product module exposes a `Product` object, listed under the
`productivity_agent_tools.products` entry-point group in `pyproject.toml`.
Nothing in the shared core names a product: `server.py`, `config.py` and the CLI
find them through the entry points. Tools that need two products at once are an
`Integration` (`links.py`), listed under `productivity_agent_tools.integrations`.
To add a product, write the sub-package, add its entry point, and run `uv sync`
so the entry point is installed.

## Continuous integration

GitHub Actions runs on every pull request and on every push to `main`:

| Job | What it does |
|---|---|
| Lint, format and type-check | All pre-commit hooks: ruff, rumdl, taplo and ty |
| Test | The test suite with the coverage threshold, on Linux and macOS |
| Build | Builds the sdist and wheel, installs the wheel into a clean environment, and uploads both as an artifact |
| Docs | Builds this site with `--strict`, so a broken link fails the pull request |

A push to `main` that changes the docs also publishes the site to GitHub Pages.
Dependabot opens weekly pull requests for GitHub Actions and Python dependency
updates.

## Releasing

1. Bump the version: `hatch version minor` (or `patch`, `major`), then commit.
2. Once it is on `main`, tag that commit with the same version, prefixed with
   `v`, and push the tag. A pull request merged by rebase gets new commit ids on
   `main`, so tag the commit there, not the one on the branch.

```bash
git tag v0.2.0 <commit-on-main> && git push origin v0.2.0
```

The release workflow checks that the tag matches `__version__`, runs the full
CI suite, and publishes a GitHub release with the sdist and wheel attached and
generated release notes. It does not publish to PyPI.

## With hatch

The same checks are available as [hatch](https://hatch.pypa.io/) commands, which
manage their own environments:

| Command | What it runs |
|---|---|
| `hatch test` | The test suite with branch coverage and the 90% threshold |
| `hatch test -k name --no-cov` | A subset of tests, without the coverage threshold |
| `hatch fmt` | ruff lint (with fixes) and format, using this project's settings |
| `hatch fmt --check` | The same, reporting only |
| `hatch run types` | ty type check |
| `hatch run hooks` | Every pre-commit hook on all files |
| `hatch run check` | Hooks, then tests |
| `hatch run doctor` | The read-only connection check |
| `hatch run docs:serve` | Preview this site |
| `hatch run docs:build` | Build this site the way CI does |
| `hatch build` | Build the sdist and wheel into `dist/` |

Tool settings live in `pyproject.toml`, except taplo's, which it only reads from
`.taplo.toml`.
