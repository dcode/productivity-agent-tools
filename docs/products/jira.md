# Jira

Jira Data Center: reads, plus comments, transitions, worklogs, assignment,
edits, new issues and links. Writes are blocked while `dry_run` is on.

## Setup

```toml
[jira]
path = "/jira"            # sub-path Jira is served under; "" for the root of its host
default_max_results = 25

[jira.presets]
mine-open = "assignee = currentUser() AND resolution = Unresolved ORDER BY updated DESC"
```

Create a personal access token in Jira (your profile, Personal Access Tokens)
and store it with `set-token jira`, or see [Authentication](../authentication.md)
for the other sources. `doctor` then checks Jira.

| Key | Default | Meaning |
|---|---|---|
| `path` | `/jira` | Sub-path Jira is served under |
| `default_max_results` | `25` | Rows returned when a call names no limit |
| `[jira.presets]` | three presets | Named JQL queries the agent can run by name |
| `tasks_jql` | your unresolved issues | Issues the [Obsidian](obsidian.md#syncing-tasks-and-meetings) sync turns into task notes (100 at most) |

Presets ship as `mine-open`, `updated-since-yesterday` and `current-sprint`;
edit them under `[jira.presets]`.

## Tools

| Tool | Kind | What it does |
|---|---|---|
| `jira_status` | read | The account, Jira version, where the token came from, whether writes are blocked |
| `jira_list_presets` | read | Named JQL queries from your config |
| `jira_run_preset` | read | Run a preset, return compact rows |
| `jira_search` | read | Ad-hoc JQL, return compact rows |
| `jira_get_issue` | read | One issue with description, every standard field and newest comments |
| `jira_list_transitions` | read | Workflow transitions available on an issue |
| `jira_add_comment` | write | Add a comment (Jira wiki markup) |
| `jira_transition_issue` | write | Move an issue through a transition, with a resolution if it takes one |
| `jira_list_worklogs` | read | Time logged on an issue, with the total |
| `jira_log_work` | write | Log time (`1h 30m`, `2d`) with an optional comment and start time |
| `jira_assign_issue` | write | Assign to `me`, `none`, or a person found by name or username |
| `jira_get_edit_fields` | read | Fields an issue's edit screen allows, with their allowed values |
| `jira_edit_issue` | write | Summary, description, type, priority, reporter, due date, estimates, epic, epic name, story points; add or remove labels, components, versions |
| `jira_get_create_fields` | read | Issue types a project offers, and one type's fields |
| `jira_create_issue` | write | Create an issue, epic, story in an epic, or sub-task, with the standard fields and story points |
| `jira_list_link_types` | read | Kinds of issue links, with both wordings |
| `jira_link_issues` | write | Link two issues, read as a sentence: "ABC-1 blocks ABC-2" |

## How the writes behave

`jira_edit_issue` and `jira_create_issue` check every field against the issue's
edit screen or the project's create screen first, and match values
(priorities, components, versions) by name, so a field the screen lacks or a
value it does not allow is refused with the choices listed. Creating an issue
whose screen requires a custom field the tool cannot fill is refused; create it
in the browser instead. Status and resolution change only through
`jira_transition_issue`.

Agile issue types work through `jira_create_issue`: an Epic takes `epic_name`,
any other issue joins an epic with `epic` (the epic's key), a sub-task takes
`parent`, and `story_points` sets the estimate. These are custom fields whose
ids differ on every server; they are found by their type, so nothing needs
configuring. `jira_get_issue` shows them too.

`jira_assign_issue` only assigns someone Jira allows on that issue. A name that
matches more than one person is refused with the candidates listed, and the
agent has to ask you which one; it does this even in dry-run.

Jira text (comments, descriptions) uses Jira wiki markup, not Markdown.

## Jira and Bitbucket together

With both configured, `jira_get_issue_pull_requests` lists an issue's pull
requests from two sources and merges them: Jira's Development panel (which also
knows pull requests linked only by branch name or title) and the pull requests
containing commits that mention the key. If one source is missing, for example
when the products are not linked, the result says so and still shows the other.
The other direction is `bitbucket_get_pull_request_issues`, which adds each
issue's summary, status and assignee when Jira is configured.
