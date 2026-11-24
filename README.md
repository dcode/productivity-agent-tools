# productivity-agent-tools

![productivity-agent-tools logo](docs/assets/logo.svg)

An MCP server that gives an AI agent (Goose first, Claude Code later) typed tools
for daily work, built for an enterprise network where those tools sit behind a
VPN, single sign-on and a consent-banner proxy. Each product is switched on by
its own table in the config:

| Product | Tools | Sign-in |
|---|---|---|
| Jira (Data Center) | reads, plus comments, transitions, worklogs, edits, new issues, links | personal access token |
| Bitbucket (Data Center) | reads, plus branches, pull-request comments and reviews | personal access token |
| Confluence (Data Center) | reads and search, plus creating and editing pages | personal access token |
| ServiceNow | your incidents and requests, plus opening incidents and requesting catalog items | browser sign-in (`login servicenow`) |
| Outlook mail and calendar | reads only | browser sign-in (`login microsoft`) |
| Obsidian vault (task notes, daily and meeting notes) | reads, plus task notes and sections of notes | none: a folder on this machine |

Writes are blocked until you turn `dry_run` off. No passwords are asked for or
stored; tokens and sign-ins are kept in the system keychain, or wherever your
secrets already live.

**Documentation:** <https://dcode.github.io/productivity-agent-tools/>, built from
[`docs/`](docs/index.md) in this repository.

## Quickstart

You need Python 3.14+, [uv](https://docs.astral.sh/uv/), a Jira personal access
token, and your VPN connected if Jira needs it. The other products are set up
the same way; see the [product pages](docs/products/index.md).

1. Install:

   ```bash
   git clone git@github.com:dcode/productivity-agent-tools.git && cd productivity-agent-tools
   uv sync
   ```

2. Create the config, then edit `base_url` (scheme and host only) and
   `[jira] path` in it:

   ```bash
   mkdir -p ~/.config/productivity-agent-tools
   cp config.example.toml ~/.config/productivity-agent-tools/config.toml
   ```

3. Store your token (hidden prompt) and check the connection:

   ```bash
   uv run productivity-agent-tools set-token jira
   uv run productivity-agent-tools doctor
   ```

4. Add the server to Goose, then restart Goose:

   ```bash
   uv run productivity-agent-tools configure-agent
   ```

5. Try a tool without the agent:

   ```bash
   uv run productivity-agent-tools call jira_search "jql=assignee = currentUser()" max_results=5
   ```

For a day planned in Obsidian (tasks, meetings and a daily plan synced from
these products, with Goose recipes for each step), see the
[daily workflow](docs/workflow.md).

More in the documentation: [getting started](docs/getting-started.md),
[configuration](docs/configuration.md) (one host or several, groups, cookies,
TLS, logging), [authentication](docs/authentication.md) (token sources,
`token_cmd`, the browser sign-ins), the [command line](docs/cli.md), and the
[architecture](docs/architecture.md): why typed tools cost an agent far fewer
tokens than raw APIs.

## Data handling

Whatever these tools return is sent to the model your agent is configured with:
issues, code, pages, helpdesk records, mail and calendar entries. Confirm that
model is approved for that data before using this on real work.

## Development

```bash
uv run pre-commit install    # once: ruff, rumdl, taplo and ty on every commit
uv run pre-commit run --all-files
uv run pytest
```

See [development](docs/development.md) for the code layout, CI, the docs site
and releasing, and [writing a product plugin](docs/contributing/plugin.md) to add
a service of your own. [docs/DESIGN.md](docs/DESIGN.md) records the design and the
reasons for it; [docs/ROADMAP.md](docs/ROADMAP.md) tracks what is done, what
still needs checking against real servers, and what comes next.

## License

[AGPL-3.0-or-later](LICENSE).
