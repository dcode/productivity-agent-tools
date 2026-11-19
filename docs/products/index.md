# Products

| Product | Config table | Tool prefix | Writes |
|---|---|---|---|
| [Jira](jira.md) | `[jira]` | `jira_` | comments, transitions, worklogs, assignment, edits, new issues, links |
| [Bitbucket](bitbucket.md) | `[bitbucket]` | `bitbucket_` | branches, pull-request comments, reviews |
| [Confluence](confluence.md) | `[confluence]` | `confluence_` | creating and editing pages |
| [ServiceNow](servicenow.md) | `[servicenow]` | `servicenow_` | none |
| [Outlook mail and calendar](outlook.md) | `[outlook_mail]`, `[outlook_calendar]` | `outlook_mail_`, `outlook_calendar_` | none |

Each tool is offered only when its product is configured. With both Jira and
Bitbucket configured, two tools link them; see [Jira](jira.md#jira-and-bitbucket-together).

## What every product has

- **A status tool** (`jira_status`, `bitbucket_status`, and so on) that reports
  the account, where its credential came from, and whether writes are blocked.
  The agent is told to call it first when another tool fails. When a credential
  is rejected or missing, the agent is told not to retry but to ask you to run
  the fix (`set-token <product>` or `login <product>`).
- **Writes behind `dry_run`.** While it is on, a write returns the request it
  would send, and the agent is told to say that nothing changed. Tools carry MCP
  read and write annotations, so your agent's approval mode applies as well.
- **Repeat suppression.** An identical write (the same comment on the same issue
  or pull request, the same transition, worklog, assignee, edit, link, new issue
  or review) repeated within 30 seconds is not sent again; the tool returns the
  first result with `repeat_suppressed` set. Agents sometimes issue one call
  twice, for example when a generated script runs its body two times.
- **Row caps.** List and search tools return `default_max_results` rows (25 by
  default, settable per product) and at most 100 per call.
