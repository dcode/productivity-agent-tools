# Architecture

This page explains how the server is put together and why: what an agent sees,
what it is spared, and what that does to the number of tokens a task costs. The
detailed design record, with the reasons for each decision and what was
rejected, is
[DESIGN.md](https://github.com/dcode/productivity-agent-tools/blob/main/docs/DESIGN.md).

## The shape of it

```text
 agent (Goose, Claude Code, ...)
   │  MCP over stdio: tool calls in, typed results out
   ▼
 productivity-agent-tools serve
   ├── server.py      offers only the tools of the products in your config
   ├── jira/ bitbucket/ confluence/ servicenow/ microsoft/ obsidian/  (products)
   │      tools.py    what the agent can ask, as typed functions
   │      client.py   what to ask the service for, and what to keep of the answer
   ▼
 adapters (transport.py and one per kind of service)
   │  TLS trust, cookies, dry-run, logging, plain-language errors
   ▼
 credential providers (credentials.py): token, browser session or OAuth,
   asked for headers before every request
   ▼
 Jira · Bitbucket · Confluence · ServiceNow · Outlook · your Obsidian vault
```

Each layer knows only the one below it. A tool never builds a URL, a client
never sees a secret, and an adapter never knows which product it serves. That is
what lets a product be added as a [plugin](contributing/plugin.md) without
touching the rest.

## What a naïve implementation looks like

The simplest way to let an agent use these services is to give it a generic
HTTP tool (or `curl`) and an API token, and let it work out the rest. That
works, after a fashion, and every step of it costs tokens:

- **The agent has to know each API.** Endpoints, query languages, pagination,
  field names and custom-field ids all have to be in the model's head or in the
  prompt, and wrong guesses come back as errors it then reads and retries.
- **Every answer is the whole answer.** A Jira issue's REST response carries
  every field, rendered HTML, avatars, links to itself and change history the
  task never needed. A Bitbucket diff can be megabytes. All of it goes into the
  context, and stays there for the rest of the conversation.
- **Failures are expensive.** Behind single sign-on, an expired session answers
  with a login page: tens of kilobytes of HTML the agent reads before
  concluding something is wrong, and often retries.
- **Joining data is the agent's job.** "Which pull requests belong to this
  issue?" means calling two services, matching keys, and reading both answers in
  full.
- **Writes have no guard rails.** A mistyped request changes shared data; a
  retried call comments twice.

## What the agent gets here instead

| Concern | Naïve | Here |
|---|---|---|
| Knowing the API | In the prompt, or guessed | Each tool is one question with typed parameters; its docstring is the manual |
| Size of an answer | The service's whole response | A typed result with only the fields that matter; HTML and markup turned into text; lists capped (`max_results`) and long text cut (`max_chars`), with the cut said |
| Expired sign-in | A login page to read | One sentence: "the session has expired, ask the user to run `login servicenow`; do not retry" |
| Joins across services | Several calls and the agent's matching | One tool: `jira_get_issue_pull_requests` merges Jira's development panel with Bitbucket's commits |
| Forms with rules | Submit, read the error, try again | Answers checked against the form, its rules included, before anything is sent |
| Writes | Sent at once | Blocked until `dry_run` is off; a blocked write returns the request it would send; an identical write within 30 seconds is not sent again |
| Work that needs no judgement | Done by the agent, in tokens | Done without it: `obsidian_sync` (from a button or cron) and `call` |

### An example

"Can my pull request for ABC-123 be merged yet?"

- **Naïve:** search Jira for the issue (the full issue JSON), list the
  repository's pull requests (every open one, in full), find the one whose
  branch mentions ABC-123, fetch it, fetch its merge status, fetch its builds,
  fetch its tasks, and read all of it to answer.
- **Here:** `jira_get_issue_pull_requests key=ABC-123` returns the linked pull
  requests, one short record each; `bitbucket_get_pull_request_merge_status`
  returns whether it can merge and, if not, each blocking check in a sentence.
  Two calls, and two small answers.

## What it costs: the tool definitions

Typed tools are not free. Their names, descriptions and parameter schemas are
sent to the model with every request, so they are a fixed cost per turn
(usually cached by the model provider, which makes repeats cheap). Measured on
version 0.12.0:

| Product | Tools | Definitions (bytes) |
|---|---|---|
| Jira (with the Jira and Bitbucket integration) | 18 | 51,529 |
| Bitbucket | 21 | 64,399 |
| Confluence | 8 | 23,052 |
| ServiceNow | 10 | 34,531 |
| Outlook mail and calendar | 11 | 36,686 |
| Obsidian | 9 | 34,642 |
| Everything | 77 | 244,839 |

At roughly four bytes to a token, every product switched on is about 60,000
tokens of definitions. That is why **only the products in your config are
offered**: a server with just Confluence and ServiceNow costs under a quarter of that,
and one with Jira and Bitbucket about half.
Leave out the products a given agent does not need, and the definitions shrink
with them.

The trade is still a good one for daily work. The definitions are paid once per
turn and are usually cached; the answers a naïve agent would read are paid
every time, are rarely cached, and stay in the conversation.

## How an agent is steered

Beyond the tools themselves, the server shapes how an agent uses them:

- **Instructions per product.** When a product is on, the server's instructions
  gain a few sentences for it: which tools change shared data and should be
  confirmed with the user, what to do when a sign-in expires, which tool to
  read before which write.
- **Errors that say what to do.** Every failure the code expects is raised with
  a plain-language message (`AgentToolsError`) that reaches the agent intact, so
  "the token was refused: ask the user to run `set-token jira`" replaces a stack
  trace.
- **Read before write.** Write tools name the read that should come first
  (`servicenow_get_catalog_form` before `servicenow_order_item`,
  `jira_get_create_fields` before `jira_create_issue`), and their checks name
  what is missing, so a failed write costs one short message, not a round trip.
- **Read and write annotations.** Every tool is marked read or write in MCP, so
  the agent's own approval settings apply as well.
