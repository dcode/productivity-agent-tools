# Daily workflow in Obsidian

A day planned in your Obsidian vault: your Jira issues, the pull requests
waiting on your review and your meetings become notes once each, the agent
helps you choose what to do today and writes the plan into your daily note, and
the evening wrap-up closes what got done. Nothing is copied from day to day:
unfinished work carries over because its task note stays open.

It needs the [Obsidian](products/obsidian.md) product, plus any of
[Jira](products/jira.md), [Bitbucket](products/bitbucket.md) and the
[Outlook calendar](products/outlook.md) you want synced.

## Set up once

Add the vault to the config, and allow writes to it alone:

```toml
[obsidian]
vault = "~/Notes"
meeting_date_format = "YYYY/MM/YYYY-MM-DD"
dry_run = false
```

Check it, and register the server with Goose if you have not:

```bash
uv run productivity-agent-tools doctor
uv run productivity-agent-tools configure-agent
```

Then try the sync without writing anything:

```bash
uv run productivity-agent-tools call obsidian_sync --dry-run
```

## The loop

| When | What | How |
|---|---|---|
| Morning, before you start | Sync issues, reviews and meetings into the vault | in the background, or the sync button |
| Morning | Plan the day with the agent | `contrib/goose/plan-day.yaml` |
| Before a meeting | Gather what you need into its note | `contrib/goose/meeting-prep.yaml` |
| End of the day | Close what got done, carry the rest, turn action items into tasks | `contrib/goose/wrap-day.yaml` |

The recipes run in a terminal and stay interactive (`-s`), because the agent
asks you before it changes anything:

```bash
goose run --recipe contrib/goose/plan-day.yaml -s
goose run --recipe contrib/goose/meeting-prep.yaml -s --params meeting=standup
goose run --recipe contrib/goose/wrap-day.yaml -s
```

`--params day=tomorrow` plans or wraps another day. `--explain` shows a
recipe's parameters without running it.

### What each recipe does

- **Plan the day** runs the sync, reads yesterday's daily note and what was
  scheduled then, and ranks your open tasks: carried over, due soon, in
  progress, high priority, in the current sprint, reviews waiting. It fills the
  time between meetings with [time blocks](products/obsidian.md#time-blocks) and
  proposes at most three priorities and a timeline. Once
  you agree, it sets `scheduled` on the chosen task notes and writes the plan
  into the daily note's `plan` section, every line a `[[link]]`.
- **Prepare for a meeting** finds the meeting, reads its attendees and agenda,
  looks up the Jira issues and pull requests it mentions and the open action
  items from the last occurrence, and writes a `prep` section into the meeting's
  note.
- **Wrap up the day** asks what got done: tasks made in the vault are marked
  done; Jira issues are transitioned (and time logged, if you want) in Jira,
  then synced back. It moves or unschedules the rest, and turns meeting action
  items into task notes, checking each against the tasks you already have.

### Running the sync in the background (macOS)

The sync needs no agent and no model, so it can run on its own. On macOS a
launchd agent runs it every 30 minutes while you are logged in, and at login,
whether or not Obsidian is open. That is safe while you work in Obsidian: each
note is written whole, and one you edited since it was read is left alone.

```bash
sed "s#CHECKOUT#$PWD#g" contrib/macos/local.productivity-agent-tools.sync.plist \
  > ~/Library/LaunchAgents/local.productivity-agent-tools.sync.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.productivity-agent-tools.sync.plist
```

Run that from the checkout. It runs `contrib/macos/sync.sh`, which appends each
result to `~/Library/Logs/productivity-agent-tools/sync.log` and shows the
summary as a notification. To run it now, outside the schedule:

```bash
launchctl kickstart gui/$(id -u)/local.productivity-agent-tools.sync
```

To stop it: `launchctl bootout gui/$(id -u)/local.productivity-agent-tools.sync`.

A source that cannot be reached (no VPN, an expired sign-in) is listed under
`unavailable` and the rest still sync, so a later run catches up. If the log
says the vault cannot be read ("Operation not permitted"), macOS is keeping the
background job out of `~/Documents`: allow it in System Settings, Privacy &
Security, Full Disk Access, for the Python that `.venv/bin/python` points to.

### Buttons

Two scripts do the work, so any way of running a command can be the button: an
Obsidian plugin that runs shell commands, a macOS Shortcut, Raycast, or a
keyboard shortcut.

| Button | Command |
|---|---|
| Productivity sync | `/path/to/productivity-agent-tools/contrib/macos/sync.sh` |
| Plan my day | `/path/to/productivity-agent-tools/contrib/macos/run-recipe.sh plan-day` |
| Prepare for a meeting | `/path/to/productivity-agent-tools/contrib/macos/run-recipe.sh meeting-prep meeting=next` |
| Wrap up the day | `/path/to/productivity-agent-tools/contrib/macos/run-recipe.sh wrap-day` |

`sync.sh` runs in the background and shows a notification when it is done (add
`--dry-run` to see what would change). `run-recipe.sh` opens a new iTerm2
window (Terminal if iTerm2 is not installed) and types the `goose run` command
into your usual shell, so `goose` is found on your normal PATH; the window stays
open for the conversation. Use full paths: a plugin's commands do not start in
the checkout.

### A chat pane in Obsidian

The [Agent Client](https://community.obsidian.md/plugins/agent-client) plugin
runs Goose in a chat pane over ACP (Agent Client Protocol), with this server's
tools available, so the day can be planned without leaving Obsidian. Ask it to
plan your day, prepare for a meeting or wrap up, and it follows the same steps
as the recipes; for the exact steps, paste a recipe's `instructions` as the
first message. It asks before each change, as in the terminal, and the same
`dry_run` settings apply. Checked on the work laptop on 2026-11-19: it planned
the day, and closed a Jira issue with a comment when told it was already done.

## Templates

`contrib/obsidian/` holds templates that put each section where the recipes
expect it. Copy them into your templates folder.

| Template | Use it for | Notes |
|---|---|---|
| `Daily note.md` | Obsidian's Daily notes template | `plan` and `wrap-up` sections; a Dataview table of the tasks scheduled that day (drop it if you do not use Dataview) |
| `Meeting (sync).md` | `[obsidian] meeting_template` | what the sync starts each new meeting note from |
| `Meeting (ad hoc).md` | meetings that are not on your calendar | Templater: asks for a title and files the note at the meeting layout |

The daily and synced-meeting templates use only Obsidian's core variables
(`{{title}}`, `{{date}}`, `{{date:FORMAT}}`, `{{time}}`). The recipes and the
sync fill those themselves when they create a note, whether or not Obsidian is
open; Templater's `<% %>` is only filled when Obsidian is open with Templater set
to run on new files. Do not use the ad hoc template for a calendar meeting: a
note made by hand has no `ical_uid`, so the sync would make a second one.

## Where things land

| Note | Where | Key |
|---|---|---|
| Daily note | Obsidian's Daily notes settings, such as `Journal/2026/11/2026-11-19.md` | the date |
| Task note | `tasks_folder`, by title | `jira`, `bitbucket` or `uid` |
| Meeting note | `meetings_folder` and `meeting_date_format`, such as `Meetings/2026/11/2026-11-19 Standup.md` | `ical_uid` |

The plan, prep and wrap-up are sections between hidden markers, so running a
recipe again replaces its section and leaves the rest of the note, including
anything you typed, alone. TaskNotes' own views (Agenda, Kanban) show the same
task notes, so a task scheduled by the plan shows up there for the day.

## If a recipe cannot see the tools

A recipe with no `extensions` block is expected to use the extensions you have
enabled in Goose. If the agent says it has no `obsidian_` tools, run
`uv run productivity-agent-tools configure-agent --show` and copy its entry into
the recipe, as a list:

```yaml
extensions:
  - type: stdio
    name: productivity
    cmd: /path/to/productivity-agent-tools/.venv/bin/productivity-agent-tools
    args: [serve]
    timeout: 300
```

## Other agents

The recipes are plain instructions over the server's tools. Claude Code or any
other MCP agent can follow them: paste a recipe's `instructions` as a custom
command, with `{{ day }}` replaced by the day.
