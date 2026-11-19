# Getting started

These steps install the server, turn on Jira, and add the server to Goose. The
other products are switched on the same way; see [Products](products/index.md).

You need Python 3.14+, [uv](https://docs.astral.sh/uv/), a way to sign in to
each product you turn on, and your VPN connected if your servers need it.

## 1. Install

```bash
git clone git@github.com:dcode/productivity-agent-tools.git && cd productivity-agent-tools
uv sync
```

## 2. Create the config

The config names your internal host, so it lives outside the repository:

```bash
mkdir -p ~/.config/productivity-agent-tools
cp config.example.toml ~/.config/productivity-agent-tools/config.toml
```

Edit `base_url` to your host (scheme and host, plus a path only if every
product shares a prefix such as `/atlassian`), and set `[jira] path` to the
sub-path Jira is served under, or `""` if it is at the root of the host. See
[Configuration](configuration.md) for products on separate hosts.

## 3. Store your token and check the connection

Create a personal access token in Jira (your profile, Personal Access Tokens),
then store it at a hidden prompt and run the read-only checks:

```bash
uv run productivity-agent-tools set-token jira
uv run productivity-agent-tools doctor
```

`doctor` checks the config, then each configured product: where its credential
came from, that the server answers, and one read. If a check fails, run it
again with `--log-level debug` to see each request and what the server
answered.

The keychain is one of four places a token can come from; see
[Authentication](authentication.md) for environment variables, env files and
commands such as `sops`.

## 4. Add the server to your agent

```bash
uv run productivity-agent-tools configure-agent
```

This registers the server with Goose; restart Goose to load it. See
[Agents](agents.md) for the options and for doing it by hand.

Writes stay blocked until you set `dry_run = false` in the config (or pass
`--no-dry-run`). Keep your agent's tool approval on, so you see each comment or
transition before it is sent.

## 5. Try a tool without the agent

`call` runs one tool directly, with the same config and dry-run setting, and
spends no model credits:

```bash
uv run productivity-agent-tools call jira_search "jql=assignee = currentUser()" max_results=5
```

See [Command line](cli.md#call) for more.
