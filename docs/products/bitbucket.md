# Bitbucket

Bitbucket Data Center: reads of projects, repositories, pull requests, code,
builds and deployments, plus three writes (a branch, a pull-request comment, a
review). Writes are blocked while `dry_run` is on.

## Setup

```toml
[bitbucket]
path = "/bitbucket"         # or its own base_url if it is on a different host
default_project = "PROJ"    # optional: the project key you usually work in
```

Create an HTTP access token in Bitbucket (your profile, Manage account, HTTP
access tokens) with read permission, or write permission if you want the
writes, and store it with `set-token bitbucket`.

| Key | Default | Meaning |
|---|---|---|
| `path` | `/bitbucket` | Sub-path Bitbucket is served under |
| `default_max_results` | `25` | Rows returned when a call names no limit |
| `default_project` | none | Project key used when a call names none |
| `user_slug` | asked of Bitbucket | Your user slug, if Bitbucket cannot say |
| `ci` | `true` | Set `false` when no CI or CD server reports to this Bitbucket |

## Tools

| Tool | Kind | What it does |
|---|---|---|
| `bitbucket_status` | read | Bitbucket version, the account, where the token came from, the default project |
| `bitbucket_search_code` | read | Search file contents across repositories, optionally within a project or repository |
| `bitbucket_list_projects` | read | Projects you can see, with their keys |
| `bitbucket_set_default_project` | session | Choose the project used when a call names none, until restart |
| `bitbucket_list_repositories` | read | Repositories in a project |
| `bitbucket_list_pull_requests` | read | Pull requests where you are reviewer, author or participant |
| `bitbucket_list_repository_pull_requests` | read | Every pull request in one repository, by state and text |
| `bitbucket_get_pull_request` | read | One pull request with description, reviewers, newest comments and build results |
| `bitbucket_get_pull_request_commits` | read | Commits in a pull request |
| `bitbucket_get_pull_request_changes` | read | Files a pull request changes |
| `bitbucket_get_pull_request_diff` | read | Unified diff, cut at a size limit |
| `bitbucket_get_pull_request_merge_status` | read | Whether a pull request can merge, and each check blocking it |
| `bitbucket_get_pull_request_activity` | read | History: pushes, comments, approvals, needs-work reviews, merges |
| `bitbucket_list_pull_request_tasks` | read | Open or resolved tasks on a pull request |
| `bitbucket_get_pull_request_issues` | read | Jira issues linked to a pull request, with summary, status and assignee when Jira is configured |
| `bitbucket_get_commit_builds` | read | CI build results for any commit, with totals and test counts |
| `bitbucket_list_required_builds` | read | Builds that must pass before a repository's pull requests can merge |
| `bitbucket_get_deployment` | read | One deployment's state, by key, environment and run number |
| `bitbucket_create_branch` | write | Create a feature, bugfix, hotfix, release or custom branch, named after a Jira issue |
| `bitbucket_add_pull_request_comment` | write | Comment on a pull request, reply to a comment, or add a task (Markdown) |
| `bitbucket_review_pull_request` | write | Approve, mark as needing work, or withdraw your review |

## Notes

Most tools need a project key. Set `default_project` to the project you usually
work in and the agent can leave it out, or tell the agent which project to use:
it calls `bitbucket_set_default_project`, which lasts until the server restarts.

`bitbucket_create_branch` names a branch the way Bitbucket's own "Create branch"
dialog does: the branch type's prefix, the issue key, and the issue's summary in
lower case with dashes, such as `feature/ABC-123-retry-failed-uploads`. The
prefixes come from the repository's branching model, so a repository that uses
`feat/` or has switched a type off is respected; `custom` has no prefix. The
summary is looked up when Jira is configured. The branch starts from the model's
development branch (its production branch for a hotfix) unless you name a start
point. Because the name carries the issue key, the branch shows up on the issue
in Jira.

A review is recorded as the token's owner, so the server needs your Bitbucket
user slug. It asks Bitbucket once per run (`bitbucket_status` shows the answer).
If `bitbucket_status` shows `user_slug: null`, set `user_slug` to the name in
your Bitbucket profile URL.

`bitbucket_search_code` uses the endpoint behind Bitbucket's own search page
(`rest/search/1.0/search`). Atlassian does not document it, so it may behave
differently between versions; if a server does not have it, the tool says so.
It accepts Bitbucket's
[search syntax](https://confluence.atlassian.com/bitbucketserver/search-for-code-in-bitbucket-server-814204781.html),
searches every project unless you name one, and only covers default branches.

If no CI or CD server reports builds or deployments to your Bitbucket, set
`ci = false`. The three build and deployment tools are then not offered, so the
agent does not try them, and reading a pull request skips the build lookup.

Bitbucket has no way to list deployments. `bitbucket_get_deployment` needs the
deployment key, environment key and sequence number that your CI or CD server
reported, so the agent asks you for them rather than guessing.

Bitbucket text (comments) uses Markdown.
