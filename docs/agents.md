# Agents

The server speaks MCP over stdio, so any MCP-capable agent can run it. Goose is
the one it is set up for today.

## Goose

Let the tool register itself:

```bash
uv run productivity-agent-tools configure-agent
```

This adds a `productivity` extension to `~/.config/goose/config.yaml` that starts
the server by the absolute path of the command in this checkout's environment,
so it does not depend on Goose's working directory or `PATH`. The rest of the
file, comments included, is kept, and the previous version is saved beside it as
`config.yaml.bak`. Running it again updates the entry in place. Restart Goose to
load it.

| Option | Effect |
|---|---|
| `--show` | Print the entry without changing any file |
| `--agent` | Agent to configure; `goose` is the default and the only one so far |
| `--name` | Extension name, `productivity` by default |
| `--config` | Start the server with a config file other than the default |
| `--agent-config` | Goose config file to edit, if it is not in the default place |

To do it by hand instead, add a stdio extension with `goose configure` (Add
Extension, Command-line Extension) or in `~/.config/goose/config.yaml`. Keep the
arguments in this order; `uv run` treats the first word after its own options as
the program to start:

```yaml
extensions:
  productivity:
    name: productivity
    type: stdio
    enabled: true
    cmd: uv
    args:
      - run
      - --directory
      - /absolute/path/to/productivity-agent-tools
      - productivity-agent-tools
      - serve
    timeout: 300
```

To allow writes for a session without changing the config, add `--no-dry-run`
after `serve` in the entry's `args`. Keep Goose's tool approval on so you see
each comment or transition before it is sent.

## Recipes

`contrib/goose/` holds three recipes for the [daily workflow in
Obsidian](workflow.md): plan the day, prepare for a meeting, and wrap up the
day. Run one with `goose run --recipe contrib/goose/plan-day.yaml -s`.

## What the agent is told

When the server starts it gives the agent short instructions for each
configured product: which tools change shared data and should be confirmed with
you first, what a `DRY RUN` result means, which markup each product uses (Jira
wiki markup, Markdown elsewhere), and what to do when a credential is rejected
or a sign-in has expired: stop, and ask you to run `set-token` or `login`.
