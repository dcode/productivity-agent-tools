# productivity-agent-tools

An [MCP](https://modelcontextprotocol.io/) server that gives an AI agent (Goose
first, Claude Code later) typed tools for daily work. It is built for an
enterprise network where the tools sit behind a VPN, single sign-on and a
consent-banner proxy, and it runs on the same laptop as the agent.

| Product | Tools | Sign-in |
|---|---|---|
| [Jira](products/jira.md) (Data Center) | reads, plus comments, transitions, worklogs, edits, new issues, links | personal access token |
| [Bitbucket](products/bitbucket.md) (Data Center) | reads, plus branches, pull-request comments and reviews | personal access token |
| [Confluence](products/confluence.md) (Data Center) | reads and search, plus creating and editing pages | personal access token |
| [ServiceNow](products/servicenow.md) | your incidents and requests, plus opening incidents and requesting catalog items | browser sign-in (`login servicenow`) |
| [Outlook mail and calendar](products/outlook.md) | reads only | browser sign-in (`login microsoft`) |
| [Obsidian](products/obsidian.md) vault | task notes, meeting notes and daily notes: reads, writes and a sync from the products above | none: a folder on this machine |

Each product is switched on by its own table in the config file, and only the
products you configure are offered to the agent.

## Safe by default

- **Writes are blocked** until you turn `dry_run` off. A blocked write returns
  the request it *would* have sent, so the agent can show you before you allow
  it.
- **No passwords.** Tokens and browser sign-ins are kept in the system keychain
  (or wherever your secrets already live: see
  [Authentication](authentication.md)). Smart cards and PINs stay between you
  and your browser.
- **Nothing secret is logged.** Tokens, cookie values and request bodies never
  appear in logs, even at `debug`.
- **Repeated writes are suppressed.** An identical write within 30 seconds is
  not sent twice, because agents sometimes issue a call twice.

## Where to go next

- [Getting started](getting-started.md): install, configure Jira, and add the
  server to your agent.
- [Configuration](configuration.md): one host or several, groups, cookies,
  TLS and logging.
- [Authentication](authentication.md): where tokens come from, and the browser
  sign-ins.
- [Products](products/index.md): what each product's tools do.
- [Command line](cli.md): `doctor`, `call`, `set-token`, `login` and the rest.

## Data handling

Whatever these tools return is sent to the model your agent is configured with:
issues, code, pages, helpdesk records, mail and calendar entries. Confirm that
model is approved for that data before using this on real work.
