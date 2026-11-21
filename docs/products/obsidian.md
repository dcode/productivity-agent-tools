# Obsidian

Your Obsidian vault, read straight from its folder: task notes made with the
[TaskNotes](https://github.com/callumalpass/tasknotes) plugin, and any other
note. Obsidian does not need to be running, and nothing is sent anywhere but to
your agent.

It is the base of a [daily-planning workflow](../workflow.md): the agent reads your tasks,
schedules the ones for today, writes the day's plan into your daily note, and
(with a sync) brings in Jira issues, pull requests awaiting your review and
calendar meetings, without ever making the same note twice.

## Setup

```toml
[obsidian]
vault = "~/Notes"
```

`vault` is the vault's top folder, the one holding `.obsidian/`. Run `doctor`
to check it, then restart your agent to load the tools. There is no token and
no `base_url`.

| Key | Default | Meaning |
|---|---|---|
| `vault` | required | The vault's folder; `~` and `$VARIABLES` are expanded |
| `tasks_folder` | `TaskNotes/Tasks` | Where new task notes go (TaskNotes' default) |
| `task_title_length` | `25` | Characters of the title kept in a synced task's file name; `0` for the key alone |
| `meetings_folder` | `Meetings` | Where meeting notes go |
| `meeting_date_format` | `YYYY-MM-DD` | Date part of a meeting note's path; the note is `<date part> <title>` |
| `timeblock_minutes` | `25` | Time block planned for a task with no estimate (a pomodoro) |
| `max_timeblock_minutes` | `50` | Longest time block planned for one task |
| `break_minutes` | `5` | Break planned after a block of `timeblock_minutes` or less |
| `long_break_minutes` | `10` | Break planned after a longer block |
| `meeting_tag` | `meeting` | Tag on the meeting notes the sync makes |
| `meeting_template` | built-in layout | Note that new synced meeting notes start from, such as `Templates/Meeting (sync)`; see [templates](../workflow.md#templates) |
| `sync_days_ahead` | `1` | Days after today whose meetings `obsidian_sync` brings in (0 to 14) |
| `daily_folder` | Obsidian's setting | Folder of the daily notes |
| `daily_format` | Obsidian's setting | Date format of a daily note's path inside `daily_folder` |
| `time_zone` | this computer's | IANA name, such as `America/New_York`, that decides what day it is |
| `task_tag` | `task` | Tag that marks a note as a task, as TaskNotes uses it |
| `done_statuses` | `["done"]` | Statuses that count as finished |
| `default_max_results` | `50` | Tasks returned when a call names no limit |
| `dry_run` | top-level value | Block writes to the vault only; see [Configuration](../configuration.md#writes-and-dry_run) |

Task notes are found anywhere in the vault by their tag, not only in
`tasks_folder`. Folders whose names start with a dot (`.obsidian`, `.trash`,
`.git`) are never read, and neither are the template folders set in the
Templates and Templater plugins: a template is not a note, and a template tagged
`#task` is not a task. `obsidian_get_note` still opens one when asked by path.

## Tools

| Tool | Kind | What it does |
|---|---|---|
| `obsidian_status` | read | The vault, how many notes and tasks it holds, notes with unreadable properties, dry-run |
| `obsidian_list_tasks` | read | Task notes, open, done or all, by day scheduled, due date, project, context or source; earliest first |
| `obsidian_find_task` | read | Tasks that may already be the one described: the same source key, or a similar title |
| `obsidian_get_note` | read | One note's properties and text, by path or `[[link]]` |
| `obsidian_get_daily_note` | read | A day's daily note (`today`, `yesterday`, `tomorrow` or a date), or where it would be |
| `obsidian_create_task` | write | A new task note, refused when the key exists and held back for open tasks with a close title |
| `obsidian_update_task` | write | Schedule, unschedule, finish or re-plan a task; by path, `[[link]]` or `jira:ABC-123` |
| `obsidian_write_section` | write | Write a named section of a daily note (made from your template if new) or another note |
| `obsidian_sync` | write | Bring open Jira issues, pull requests awaiting your review and the coming meetings into the vault |

## Syncing tasks and meetings

`obsidian_sync` asks every other product that is on for what is waiting on you
and turns it into notes:

| From | Becomes | Key | Done when |
|---|---|---|---|
| Jira (`[jira] tasks_jql`, by default your unresolved issues) | a task note | `jira: ABC-123` | the issue is resolved |
| Bitbucket (open pull requests where your review is still unapproved) | a task note, "Review: ..." | `bitbucket: PROJ/repo#42` | merged, declined, or you approved it or marked it needs work |
| Outlook calendar (today and `sync_days_ahead` days after) | a meeting note at your meeting layout, with Agenda, Notes and Action items headings | `ical_uid` | (never; one cancelled, declined or deleted gets `cancelled: true`) |

Run it with no agent at all, by hand, from a button, or in the background
(see [the daily workflow](../workflow.md#running-the-sync-in-the-background-macos)):

```bash
uv run productivity-agent-tools call obsidian_sync
```

What makes it safe to run as often as you like:

- **Named by key.** A task from another system is named `<key> <short title>`:
  `ELK-928 review-il5-ansible-and.md`, `PROJ-web-42 review-change-42.md`. The
  title is lower-cased, joined with hyphens and cut to `task_title_length`
  characters at a word; the full title stays in the `title` property. A task you
  make in the vault is named by its title.
- **Found by key, made once.** A second run updates the notes the first made,
  even after an issue is renamed or a meeting moves (its note stays where it is,
  with the new time). Two notes with the same key are reported, not touched.
- **Only its own fields.** For a task: `title`, `status`, `priority` (only when
  the source has priorities), `due`, `completedDate`, `source_status`,
  `source_estimate` (Jira's remaining estimate, else its original, in minutes)
  and `url`.
  One exception: a status you change in Obsidian (marking a Jira task done in
  TaskNotes, say) is kept while the source has not moved since the last sync,
  which the note's `source_status` records; the report flags it until you make
  the same change in the source (wrap-day offers to) or set it back. Once the
  source moves, its status applies again.
  For a meeting: `title`, `date`, `start`, `end`, `organizer`, `location`,
  `join`, `url`, `response`, `cancelled` and `series_id`. Never `scheduled`,
  `contexts`, the note's text, or anything else you or the agent wrote.
- **Never deletes.** A resolved issue becomes `status: done` with a
  `completedDate`; one reopened in Jira goes back to its status.
- **Meetings that leave your calendar are noticed.** A cancelled, declined or
  deleted meeting disappears from Outlook's calendar view rather than coming back
  marked, so for each meeting note dated in the sync's days whose meeting did not
  come back, the sync looks a week either side of its day. Found, the meeting
  moved and its note gets the new time; not found, the note gets
  `cancelled: true`. If the calendar cannot be read, nothing is marked. A meeting
  moved more than a week away is marked cancelled until it comes into range,
  when the next sync clears it.
- **Not everything gets a note.** Finished tasks, and meetings you declined,
  that show you as free, that are all-day, or that were cancelled, get no new
  note (an existing note is still updated). The report counts them.
- **Possible duplicates are flagged.** A new synced task whose title is close to
  a task you made by hand is made anyway (its key is authoritative), and the
  report names the one you made so you can merge them.
- **A source that fails is skipped**, and listed under `unavailable`; the rest
  still sync. Under dry-run the report shows every change and nothing is
  written.

## Writing to the vault

Writes follow `dry_run`, and `[obsidian] dry_run = false` allows them for the
vault alone while Jira and the rest stay blocked. The agent is told to confirm
each change with you first.

- **No duplicate tasks.** `obsidian_create_task` refuses a task whose source key
  already exists and returns that task instead. A new task whose title is close
  (80% or more) to an open task's is held back and the close ones are shown; the
  agent asks you, and creates it only if you say it is a different task. A task
  made in the vault gets a `uid` key of its own.
- **Synced fields stay with their source.** A task tied to Jira or Bitbucket has
  its title, status, priority and due date owned there; change those in that
  system and sync. Here only `scheduled`, `contexts`, `projects` and
  `timeEstimate` change on such a task.
- **Sections, not appends.** A section sits between two hidden markers
  (`<!-- productivity-agent-tools:plan:start -->` and `...:end -->`), so writing
  today's plan again replaces it, and the rest of the note is untouched. A daily
  note that does not exist yet starts from the template in Obsidian's Daily
  notes settings, with `{{title}}`, `{{date}}`, `{{date:FORMAT}}` and `{{time}}`
  filled in; Templater's `<% %>` commands are left for Templater.
- **Your edits win.** Properties are changed in place (order, comments and
  quoting kept), each file is written to a temporary file and renamed, and a
  note that changed after it was read (you typed in it in Obsidian, say) is
  left alone with a message to try again.

## Where dated notes go

Daily notes and meeting notes are placed by a folder and a date format written
the way Obsidian's Daily notes plugin writes it (moment.js tokens), where a `/`
in the format makes sub-folders. To keep daily notes in
`Journal/YYYY/MM/YYYY-MM-DD` and meetings in
`Meetings/YYYY/MM/YYYY-MM-DD <title>`:

```toml
[obsidian]
vault = "~/Notes"
meetings_folder = "Meetings"
meeting_date_format = "YYYY/MM/YYYY-MM-DD"
```

and in Obsidian, Settings, Daily notes: date format `YYYY/MM/YYYY-MM-DD`, new
file location `Journal`.

**Daily notes follow Obsidian's Daily notes settings** (read from
`.obsidian/daily-notes.json`) unless `daily_folder` or `daily_format` is set. A
daily note written anywhere else would be a second note for the same day, not
the one Obsidian's Today button opens, so `doctor` fails when the config and
Obsidian disagree. Set them only if Obsidian's format uses a token this cannot
write (a time, for instance), and then set Obsidian's to match.

Supported tokens: `YYYY`, `YY`, `MMMM` (November), `MMM` (Nov), `MM`, `M`,
`DDDD` and `DDD` (day of the year), `DD`, `D`, `Do` (19th), `dddd` (Thursday),
`ddd` (Thu), `d` (weekday number, Sunday 0), `GGGG`, `WW`, `W` (ISO week), and
`[text]` for literal text. A meeting title loses the characters Obsidian does
not allow in a file name (`\ / : * ? " < > | # ^ [ ]`).

"Today" is the date in `time_zone`, or this computer's time zone, found from the
system each time (the `TZ` variable, then `/etc/localtime`), so it follows a
laptop that changes zone. `obsidian_status` and `doctor` say which zone is in
use.

## Time blocks

Each task listed by `obsidian_list_tasks` carries `estimate_minutes` and
`block_minutes`, the length to plan for it in one sitting. The estimate is the
first of these that is set and more than 0: your TaskNotes `timeEstimate`, then
the source's `source_estimate` (for Jira, its remaining estimate, else its
original estimate). Then:

| The task has | Its block |
|---|---|
| an estimate | the estimate, at most `max_timeblock_minutes` |
| no estimate (a due date or not) | `timeblock_minutes` |

Each task also carries `break_minutes`, the break to leave after its block:
`long_break_minutes` after a block longer than `timeblock_minutes` (a 50-minute
block is followed by 10 minutes), else `break_minutes`. The plan-day recipe
fills the free time between meetings with these blocks and breaks, and says how
much is left of a task bigger than one block.

## Task notes and source keys

A task note keeps TaskNotes' own properties (`title`, `status`, `priority`,
`due`, `scheduled`, `contexts`, `projects`, `tags`). A task that comes from
somewhere else also carries a **source key**, an extra property TaskNotes
ignores and keeps:

| Property | Ties the note to |
|---|---|
| `jira` | A Jira issue, such as `ABC-123` |
| `bitbucket` | A pull request, such as `PROJ/repo#42` |
| `servicenow` | A helpdesk request |
| `ical_uid`, `series_id` | A meeting, and its recurring series |
| `uid` | A task made in the vault itself |

The key is how the same task is recognized from one day to the next, whatever
its title or file name. The agent is told to call `obsidian_find_task` before
suggesting a new task: a task with the same key is the same task, and one with
a similar title is shown to you to decide.

## Data handling

The notes these tools read are sent to the model your agent uses, like any
other tool result.
