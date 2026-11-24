# Contributing

Changes are welcome: a fix, a new tool for an existing product, or a new
product. This section covers how the project works and how to extend it.

- [Development](../development.md): setting up, the checks every change must
  pass, how the code is laid out, CI and releasing.
- [Writing a product plugin](plugin.md): a worked example, from an empty folder
  to tools an agent can call, as a separately installed package.
- [Architecture](../architecture.md): why the tools are shaped the way they are,
  which is the standard a new tool is held to.

## How work is done here

- **The roadmap is the source of truth.**
  [ROADMAP.md](https://github.com/dcode/productivity-agent-tools/blob/main/docs/ROADMAP.md)
  says where things stand, what was checked against real servers and what is
  next; update it in the same change as the work it describes.
  [DESIGN.md](https://github.com/dcode/productivity-agent-tools/blob/main/docs/DESIGN.md)
  records decisions and the reasons for them.
- **Every change keeps the checks green:** ruff, ty, rumdl and taplo through the
  pre-commit hooks, the test suite at full line and branch coverage (it is at
  100%), and the strict docs build. See [Development](../development.md#checks).
- **Tests use fakes, never real servers.** A tool is tested against a fake of its
  service built on `httpx.MockTransport`. What a fake cannot prove (does the
  real server answer this way?) goes in the roadmap as a check to make on the
  real thing, and `scripts/smoke-test.sh` runs every tool against real servers.
- **Writes are guarded.** A write goes through the adapter's `write`, which sends
  nothing while `dry_run` is on, and through the repeat guard. Design notes in
  [Writing a product plugin](plugin.md#tools-that-change-things).
- **Releases are tagged on working code.** A version tag goes on the commit where
  that release's code is complete, normally the last commit it lands with on
  `main`. See [Releasing](../development.md#releasing).
