#!/usr/bin/env bash
#
# Smoke test: run every tool once against your real servers with `call`,
# no agent and no model credits. Nothing here changes Jira, Bitbucket or Confluence:
# every write tool it runs is forced into dry-run. The Outlook and ServiceNow tools
# are read-only.
#
# Some checks feed on earlier ones (a message id from the inbox list, say) and
# some assert something about a result rather than only that the call worked;
# both are skipped when what they need is missing. "Observed" checks probe API
# behaviour that is worth seeing but has no right answer here; they never fail.
#
# 1. Copy .env.example to .env in the repository root and fill in your values.
#    .env is ignored by git, so your issue keys and project names stay local.
# 2. Run it from anywhere:  ./scripts/smoke-test.sh
#    Add extra options for every call after the script name, for example:
#      ./scripts/smoke-test.sh --log-level debug
#
# Full output of each call is kept in a temporary folder, printed at the end.

set -u

EXTRA_ARGS=("$@")
LINES_SHOWN=25

cd "$(dirname "$0")/.." || exit 1

# Your values come from .env; anything it leaves out stays a <placeholder>,
# and a check that needs a placeholder is skipped.
if [ -f .env ]; then
  # shellcheck source=/dev/null
  . ./.env
else
  printf 'No .env found: copy .env.example to .env and fill it in. Running without it.\n'
fi
: "${JIRA_ISSUE:=<ISSUE-123>}"
: "${JIRA_JQL:=assignee = currentUser() ORDER BY updated DESC}"
: "${JIRA_ISSUE_TYPE:=Task}"
: "${BITBUCKET_PROJECT:=<project-key>}"
: "${BITBUCKET_REPO:=<repo-slug>}"
: "${BITBUCKET_PR:=<pr-number>}"
: "${BITBUCKET_SEARCH:=<search-text>}"
: "${BITBUCKET_COMMIT:=<commit-hash>}"
: "${BITBUCKET_DEPLOY_KEY:=<deployment-key>}"
: "${BITBUCKET_DEPLOY_ENV:=<environment-key>}"
: "${BITBUCKET_DEPLOY_RUN:=<sequence-number>}"
: "${CONFLUENCE_SPACE:=<space-key>}"
: "${CONFLUENCE_PAGE:=<page-id>}"
: "${OUTLOOK_SEARCH:=<search-text>}"
OUT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/productivity-smoke.XXXXXX")"
PASSED=()
FAILED=()
SKIPPED=()
OBSERVED=()

tool() {
  uv run productivity-agent-tools call "$@" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
}

build-identifier() {
  local previous_tag
  local build_id
  previous_tag="$(git describe --tags --abbrev=0 2>/dev/null || true)"
  if [ -n "$previous_tag" ]; then
    build_id="$(git describe --tags --always --match "$previous_tag" 2>/dev/null || true)"
  else
    build_id="$(git describe --tags --always 2>/dev/null || true)"
  fi

  echo -n "${build_id}"
}

# has_placeholder VALUE... -> true if any value is still a <placeholder>
has_placeholder() {
  local value
  for value in "$@"; do
    case "$value" in
      "<"*">") return 0 ;;
    esac
  done
  return 1
}

# check NAME NEEDED_VALUES -- TOOL [ARGS...]
#   Runs one tool, shows the first lines of its result, and records pass/fail.
#   Skips it if any of NEEDED_VALUES is still a placeholder.
check() {
  local name="$1"
  shift
  local needed=()
  while [ "$1" != "--" ]; do
    needed+=("$1")
    shift
  done
  shift

  printf '\n== %s\n' "$name"
  if has_placeholder ${needed[@]+"${needed[@]}"}; then
    printf '   skipped: fill in the placeholder values at the top of this script\n'
    SKIPPED+=("$name")
    return
  fi

  local file
  file="$(out_file "$name")"
  if tool "$@" >"$file" 2>"$file.err"; then
    PASSED+=("$name")
    head -n "$LINES_SHOWN" "$file" | sed 's/^/   /'
    local total
    total=$(wc -l <"$file" | tr -d ' ')
    if [ "$total" -gt "$LINES_SHOWN" ]; then
      printf '   ... (%s lines in all: %s)\n' "$total" "$file"
    fi
  else
    FAILED+=("$name")
    sed 's/^/   /' "$file.err"
    # Leave no result behind, so checks that feed on this one are skipped.
    rm -f "$file"
  fi
}

# out_file NAME -> where check NAME keeps its output
out_file() {
  local name="$1"
  printf '%s/%s.txt' "$OUT_DIR" "${name// /_}"
}

# json_py FILE CODE [ARGS...] -> run Python CODE with the JSON in FILE as `r` (ARGS are
#   sys.argv[3:]); prints what CODE prints.
#   Uses the project's own Python, so it needs nothing beyond what `call` needs.
json_py() {
  uv run --quiet python -I -c '
import json, sys
with open(sys.argv[1]) as handle:
    r = json.load(handle)
exec(sys.argv[2])
' "$@"
}

# pick CHECK_NAME KEY... -> print one value from a check's JSON result, or "<missing>".
#   Keys walk objects by name and arrays by index: pick "mail list" messages 0 id
pick() {
  local file
  file="$(out_file "$1")"
  shift
  local found=""
  if [ -s "$file" ]; then
    found="$(json_py "$file" '
value = r
for key in sys.argv[3:]:
    if isinstance(value, list) and key.isdigit() and int(key) < len(value):
        value = value[int(key)]
    elif isinstance(value, dict) and key in value:
        value = value[key]
    else:
        value = None
        break
if isinstance(value, (str, int, float)) and not isinstance(value, bool):
    print(value)
' "$@" 2>/dev/null)"
  fi
  printf '%s' "${found:-<missing>}"
}

# needs VALUE -> a stand-in for VALUE in a check's needed values: "<missing>" when pick
#   found nothing, else "found". For values such as a Message-ID that are themselves
#   in <angle brackets>, and so would look like an unfilled placeholder.
needs() {
  if [ "$1" = "<missing>" ]; then printf '<missing>'; else printf 'found'; fi
}

# expect NAME CHECK_NAME DESCRIPTION PYTHON_CONDITION
#   Passes if the condition holds for the JSON of CHECK_NAME's result (as `r`);
#   skipped if that check did not produce a result.
expect() {
  local name="$1" source="$2" description="$3" condition="$4"
  local file
  file="$(out_file "$source")"
  printf '\n== %s\n   expect: %s\n' "$name" "$description"
  if [ ! -s "$file" ]; then
    printf '   skipped: "%s" has no result\n' "$source"
    SKIPPED+=("$name")
    return
  fi
  if json_py "$file" "sys.exit(0 if ($condition) else 1)" 2>"$(out_file "$name").err"; then
    printf '   ok\n'
    PASSED+=("$name")
  else
    printf '   NOT MET\n'
    sed 's/^/   /' "$(out_file "$name").err"
    FAILED+=("$name")
  fi
}

# observe NAME NEEDED_VALUES -- TOOL [ARGS...]
#   Like check, but records what happened instead of passing or failing: for API
#   behaviour that is worth seeing (does it refuse this?) with no right answer.
observe() {
  local name="$1"
  shift
  local needed=()
  while [ "$1" != "--" ]; do
    needed+=("$1")
    shift
  done
  shift
  printf '\n== %s (observed)\n' "$name"
  if has_placeholder ${needed[@]+"${needed[@]}"}; then
    printf '   skipped: a value it needs is missing\n'
    SKIPPED+=("$name")
    return
  fi
  local file
  file="$(out_file "$name")"
  if tool "$@" >"$file" 2>"$file.err"; then
    OBSERVED+=("$name: accepted")
    head -n "$LINES_SHOWN" "$file" | sed 's/^/   /'
  else
    OBSERVED+=("$name: refused")
    sed 's/^/   /' "$file.err"
  fi
}

# odata_quote VALUE -> VALUE with each single quote doubled, for an OData string literal
odata_quote() {
  printf '%s' "${1//\'/\'\'}"
}

# ---- Which products are configured ------------------------------------------

# The tool list holds only the tools of products that have a table in the config.
if ! TOOLS="$(tool 2>"$OUT_DIR/tool-list.err")"; then
  printf 'Could not list the tools, so nothing was checked:\n'
  sed 's/^/   /' "$OUT_DIR/tool-list.err"
  exit 1
fi

# offers TOOL_NAME -> true if the server offers that tool
offers() {
  printf '%s\n' "$TOOLS" | grep -q "^$1\( \|\$\)"
}

printf '== smoke test\n   build: %s\n' "$(build-identifier)" | tee "$OUT_DIR/00-build.txt"

# ---- Jira (only if [jira] is in your config) --------------------------------

if offers jira_status; then
  check "jira status" -- jira_status
  check "jira presets" -- jira_list_presets
  check "jira run preset" -- jira_run_preset name=mine-open max_results=3
  check "jira search" "$JIRA_JQL" -- jira_search "jql=$JIRA_JQL" max_results=3
  check "jira get issue" "$JIRA_ISSUE" -- jira_get_issue "key=$JIRA_ISSUE" max_comments=2
  check "jira transitions" "$JIRA_ISSUE" -- jira_list_transitions "key=$JIRA_ISSUE"
  check "jira worklogs" "$JIRA_ISSUE" -- jira_list_worklogs "key=$JIRA_ISSUE" max_results=3
  check "jira edit fields" "$JIRA_ISSUE" -- jira_get_edit_fields "key=$JIRA_ISSUE"
  check "jira create fields" "$JIRA_ISSUE" -- jira_get_create_fields "project=${JIRA_ISSUE%%-*}"
  check "jira link types" -- jira_list_link_types
  # Write tools, forced into dry-run: they show the request and send nothing.
  check "jira comment (dry run)" "$JIRA_ISSUE" -- \
    jira_add_comment "key=$JIRA_ISSUE" "body=smoke test, not sent" --dry-run
  check "jira log work (dry run)" "$JIRA_ISSUE" -- \
    jira_log_work "key=$JIRA_ISSUE" "time_spent=15m" "comment=smoke test, not sent" --dry-run
  # Looks you up as an assignable user even in dry-run.
  check "jira assign to me (dry run)" "$JIRA_ISSUE" -- \
    jira_assign_issue "key=$JIRA_ISSUE" assignee=me --dry-run
  # Checked against the edit and create screens even in dry-run.
  check "jira edit (dry run)" "$JIRA_ISSUE" -- \
    jira_edit_issue "key=$JIRA_ISSUE" 'add_labels=["smoke-test"]' --dry-run
  # Needs an Epic type in the project; a project without one fails this check.
  check "jira create epic (dry run)" "$JIRA_ISSUE" -- \
    jira_create_issue "project=${JIRA_ISSUE%%-*}" issue_type=Epic \
    "summary=smoke test, not sent" "epic_name=smoke test" --dry-run
  check "jira create (dry run)" "$JIRA_ISSUE" -- \
    jira_create_issue "project=${JIRA_ISSUE%%-*}" "issue_type=$JIRA_ISSUE_TYPE" "summary=smoke test, not sent" \
    --dry-run
else
  printf '\n== jira\n   skipped: no [jira] table in the config\n'
  SKIPPED+=("jira (not configured)")
fi

# ---- Bitbucket (only if [bitbucket] is in your config) ----------------------

if offers bitbucket_status; then
  B_PROJECT=("$BITBUCKET_PROJECT")
  B_REPO=("$BITBUCKET_PROJECT" "$BITBUCKET_REPO")
  B_PR=("$BITBUCKET_PROJECT" "$BITBUCKET_REPO" "$BITBUCKET_PR")
  pr_args=("repository=$BITBUCKET_REPO" "pull_request_id=$BITBUCKET_PR" "project=$BITBUCKET_PROJECT")

  check "bitbucket status" -- bitbucket_status
  check "bitbucket projects" -- bitbucket_list_projects max_results=5
  check "bitbucket my reviews" -- bitbucket_list_pull_requests role=reviewer state=all max_results=3
  check "bitbucket my pull requests" -- bitbucket_list_pull_requests role=author state=all max_results=3
  check "bitbucket repositories" "${B_PROJECT[@]}" -- \
    bitbucket_list_repositories "project=$BITBUCKET_PROJECT" max_results=5
  check "bitbucket repo pull requests" "${B_REPO[@]}" -- \
    bitbucket_list_repository_pull_requests "repository=$BITBUCKET_REPO" \
    "project=$BITBUCKET_PROJECT" state=all max_results=3
  check "bitbucket pull request" "${B_PR[@]}" -- bitbucket_get_pull_request "${pr_args[@]}" max_comments=3
  check "bitbucket pr commits" "${B_PR[@]}" -- bitbucket_get_pull_request_commits "${pr_args[@]}" max_results=5
  check "bitbucket pr changes" "${B_PR[@]}" -- bitbucket_get_pull_request_changes "${pr_args[@]}"
  check "bitbucket pr diff" "${B_PR[@]}" -- \
    bitbucket_get_pull_request_diff "${pr_args[@]}" max_chars=2000 --field diff
  check "bitbucket pr merge status" "${B_PR[@]}" -- \
    bitbucket_get_pull_request_merge_status "${pr_args[@]}"
  check "bitbucket pr activity" "${B_PR[@]}" -- \
    bitbucket_get_pull_request_activity "${pr_args[@]}" max_results=5
  check "bitbucket pr tasks" "${B_PR[@]}" -- \
    bitbucket_list_pull_request_tasks "${pr_args[@]}" state=all max_results=5
  check "bitbucket pr jira issues" "${B_PR[@]}" -- bitbucket_get_pull_request_issues "${pr_args[@]}"
  if offers bitbucket_get_commit_builds; then
    check "bitbucket required builds" "${B_REPO[@]}" -- \
      bitbucket_list_required_builds "repository=$BITBUCKET_REPO" "project=$BITBUCKET_PROJECT"
    check "bitbucket commit builds" "$BITBUCKET_COMMIT" -- \
      bitbucket_get_commit_builds "commit_id=$BITBUCKET_COMMIT" max_results=5
    check "bitbucket deployment" "${B_REPO[@]}" "$BITBUCKET_COMMIT" "$BITBUCKET_DEPLOY_KEY" \
      "$BITBUCKET_DEPLOY_ENV" "$BITBUCKET_DEPLOY_RUN" -- \
      bitbucket_get_deployment "repository=$BITBUCKET_REPO" "project=$BITBUCKET_PROJECT" \
      "commit_id=$BITBUCKET_COMMIT" "deployment_key=$BITBUCKET_DEPLOY_KEY" \
      "environment_key=$BITBUCKET_DEPLOY_ENV" "sequence_number=$BITBUCKET_DEPLOY_RUN"
  else
    printf '\n== bitbucket builds and deployments\n   skipped: ci = false under [bitbucket]\n'
    SKIPPED+=("bitbucket builds and deployments (ci = false)")
  fi
  # Write tools, forced into dry-run: they show the request and send nothing.
  # The review also checks that the server can tell who you are (user_slug).
  check "bitbucket branch (dry run)" "${B_REPO[@]}" "$JIRA_ISSUE" -- \
    bitbucket_create_branch "repository=$BITBUCKET_REPO" "project=$BITBUCKET_PROJECT" \
    "issue_key=$JIRA_ISSUE" branch_type=feature --dry-run
  check "bitbucket comment (dry run)" "${B_PR[@]}" -- \
    bitbucket_add_pull_request_comment "${pr_args[@]}" "text=smoke test, not sent" --dry-run
  check "bitbucket review (dry run)" "${B_PR[@]}" -- \
    bitbucket_review_pull_request "${pr_args[@]}" review=approve --dry-run
  check "bitbucket code search" "$BITBUCKET_SEARCH" -- \
    bitbucket_search_code "query=$BITBUCKET_SEARCH" max_results=3
else
  printf '\n== bitbucket\n   skipped: no [bitbucket] table in the config\n'
  SKIPPED+=("bitbucket (not configured)")
fi

# ---- Confluence (only if [confluence] is in your config) ----------------------

if offers confluence_status; then
  check "confluence status" -- confluence_status
  check "confluence spaces" -- confluence_list_spaces max_results=5
  check "confluence my pages" -- \
    confluence_search "cql=contributor = currentUser() ORDER BY lastmodified DESC" max_results=3
  check "confluence space search" "$CONFLUENCE_SPACE" -- \
    confluence_search "cql=space = \"$CONFLUENCE_SPACE\" AND type = page" max_results=3
  check "confluence page" "$CONFLUENCE_PAGE" -- \
    confluence_get_page "page_id=$CONFLUENCE_PAGE" max_chars=2000
  check "confluence child pages" "$CONFLUENCE_PAGE" -- \
    confluence_list_child_pages "page_id=$CONFLUENCE_PAGE" max_results=5
  # Forced into dry-run: checks the title is free and shows the converted body.
  check "confluence create (dry run)" "$CONFLUENCE_SPACE" -- \
    confluence_create_page "space=$CONFLUENCE_SPACE" "title=Smoke test, not sent" \
    "body="$'## Not sent\n\n- converted from **Markdown**' --dry-run
else
  printf '\n== confluence\n   skipped: no [confluence] table in the config\n'
  SKIPPED+=("confluence (not configured)")
fi

# ---- Outlook mail (only if [outlook_mail] is in your config) ------------------

if offers outlook_mail_status; then
  check "mail status" -- outlook_mail_status
  # Read from the keychain when called, so a sign-in while a server runs shows at once.
  expect "mail status is live" "mail status" "token_source says when you signed in" \
    '"signed in at" in r["token_source"]'
  check "mail list" -- outlook_mail_list_messages max_results=3
  check "mail list unread" -- outlook_mail_list_messages unread_only=true max_results=3
  expect "mail unread are unread" "mail list unread" "every message has is_read false" \
    'all(m["is_read"] is False for m in r["messages"])'
  check "mail search" "$OUTLOOK_SEARCH" -- \
    outlook_mail_search_messages "query=$OUTLOOK_SEARCH" max_results=3

  MAIL_ID="$(pick "mail list" messages 0 id)"
  check "mail get" "$MAIL_ID" -- outlook_mail_get_message "message_id=$MAIL_ID" max_chars=300
  expect "mail get truncates" "mail get" "body is at most 300 characters, flagged if cut" \
    'len(r["body"]) <= 300 and r["truncated"] == (r["body_chars"] > 300)'
  check "mail headers" "$MAIL_ID" -- outlook_mail_get_message_headers "message_id=$MAIL_ID"
  expect "mail headers present" "mail headers" "a Received header is among them" \
    'any(h["name"].lower() == "received" for h in r["headers"])'

  # OData queries. Find an inbox message's Message-ID, then look it up by it.
  check "mail query inbox" -- outlook_mail_query_messages folder=inbox top=1 \
    select=Id,Subject,InternetMessageId "orderby=ReceivedDateTime desc"
  INTERNET_ID="$(pick "mail query inbox" items 0 InternetMessageId)"
  INBOX_ID="$(pick "mail query inbox" items 0 Id)"
  check "mail query by message-id" "$(needs "$INTERNET_ID")" -- outlook_mail_query_messages \
    "filter=InternetMessageId eq '$(odata_quote "$INTERNET_ID")'" select=Id,Subject
  expect "mail query finds it" "mail query by message-id" "exactly that one message" \
    "r['returned'] == 1 and r['items'][0]['Id'] == '$INBOX_ID'"
  check "mail query camelCase" "$(needs "$INTERNET_ID")" -- outlook_mail_query_messages \
    "filter=internetMessageId eq '$(odata_quote "$INTERNET_ID")'" select=id,subject
  expect "mail query camelCase finds it" "mail query camelCase" \
    "the same message, with PascalCase names back" \
    "r['returned'] == 1 and r['items'][0]['Id'] == '$INBOX_ID'"

  # Paging: two pages of two, newest first, should not overlap.
  check "mail query page 1" -- outlook_mail_query_messages top=2 count=true \
    "orderby=ReceivedDateTime desc"
  expect "mail query counts" "mail query page 1" "total is reported, next_skip is 2" \
    'isinstance(r["total"], int) and (r["next_skip"] == 2 or not r["more_available"])'
  NEXT_SKIP="$(pick "mail query page 1" next_skip)"
  check "mail query page 2" "$NEXT_SKIP" -- outlook_mail_query_messages top=2 \
    "skip=$NEXT_SKIP" "orderby=ReceivedDateTime desc"
  P1_A="$(pick "mail query page 1" items 0 Id)"
  P1_B="$(pick "mail query page 1" items 1 Id)"
  expect "mail query pages differ" "mail query page 2" "page 2 shares no message with page 1" \
    "not {i['Id'] for i in r['items']} & {'$P1_A', '$P1_B'}"

  # Internet headers for a whole page, in one request.
  check "mail query headers" -- outlook_mail_query_messages top=3 \
    select=Subject,InternetMessageHeaders max_chars=500
  expect "mail query headers present" "mail query headers" \
    "every message on the page carries its headers" \
    'r["items"] and all(isinstance(i.get("InternetMessageHeaders"), list) for i in r["items"])'
  check "mail query junk folder" -- outlook_mail_query_messages folder=junkemail top=2
  # A filter and a sort on different properties: accepted on the DoD tenant (2026-11-19),
  # though Graph documents refusing it. A failure here means that changed.
  check "mail query filter plus orderby" -- outlook_mail_query_messages \
    "filter=IsRead eq false" "orderby=ReceivedDateTime desc" top=2
else
  printf '\n== outlook mail\n   skipped: no [outlook_mail] table in the config\n'
  SKIPPED+=("outlook mail (not configured)")
fi

# ---- Outlook calendar (only if [outlook_calendar] is in your config) ----------

if offers outlook_calendar_status; then
  check "calendar status" -- outlook_calendar_status
  check "calendar list" -- outlook_calendar_list_calendars max_results=5
  check "calendar events" -- outlook_calendar_list_events max_results=5
  expect "calendar events in UTC" "calendar events" "every start is in UTC" \
    'all((e["start"] or {}).get("time_zone") == "UTC" for e in r["events"])'
  expect "calendar events have a meeting key" "calendar events" \
    "every event has an iCalUId (the key that ties a meeting note to its meeting)" \
    'all(e.get("ical_uid") for e in r["events"])'
  EVENT_ID="$(pick "calendar events" events 0 id)"
  check "calendar event" "$EVENT_ID" -- outlook_calendar_get_event "event_id=$EVENT_ID" max_chars=300
  CALENDAR_ID="$(pick "calendar list" calendars 0 id)"
  check "calendar events one calendar" "$CALENDAR_ID" -- \
    outlook_calendar_list_events "calendar_id=$CALENDAR_ID" max_results=3
  check "calendar query series" -- outlook_calendar_query_events \
    "filter=Type eq 'SeriesMaster'" top=3 count=true
  expect "calendar query series only" "calendar query series" "every item is a series master" \
    'all(i.get("Type") == "SeriesMaster" for i in r["items"])'
  check "calendar query one calendar" "$CALENDAR_ID" -- \
    outlook_calendar_query_events "calendar_id=$CALENDAR_ID" top=2
else
  printf '\n== outlook calendar\n   skipped: no [outlook_calendar] table in the config\n'
  SKIPPED+=("outlook calendar (not configured)")
fi

# ---- ServiceNow (only if [servicenow] is in your config) ----------------------

if offers servicenow_status; then
  check "servicenow status" -- servicenow_status
  expect "servicenow status is live" "servicenow status" "session_source says when you signed in" \
    '"signed in at" in r["session_source"]'
else
  printf '\n== servicenow\n   skipped: no [servicenow] table in the config\n'
  SKIPPED+=("servicenow (not configured)")
fi

# ---- Obsidian (only if [obsidian] is in your config; reads the vault only) ----

if offers obsidian_status; then
  check "obsidian status" -- obsidian_status
  expect "obsidian vault found" "obsidian status" "the folder is an Obsidian vault" \
    'r["is_vault"] is True'
  check "obsidian open tasks" -- obsidian_list_tasks max_results=5
  check "obsidian all tasks" -- obsidian_list_tasks state=all max_results=3
  expect "obsidian tasks in order" "obsidian all tasks" "earliest scheduled or due first" \
    'all(min([d[:10] for d in (a["scheduled"], a["due"]) if d] or ["~"]) <= min([d[:10] for d in (b["scheduled"], b["due"]) if d] or ["~"]) for a, b in zip(r["tasks"], r["tasks"][1:]))'
  TASK_PATH="$(pick "obsidian all tasks" tasks 0 path)"
  TASK_TITLE="$(pick "obsidian all tasks" tasks 0 title)"
  check "obsidian get note" "$TASK_PATH" -- obsidian_get_note "note=$TASK_PATH" max_chars=200
  check "obsidian find by title" "$TASK_TITLE" -- obsidian_find_task "title=$TASK_TITLE"
  expect "obsidian finds its own task" "obsidian find by title" "a task's own title scores 1" \
    'r["similar"] and r["similar"][0]["score"] == 1.0'
  check "obsidian daily note" -- obsidian_get_daily_note day=yesterday max_chars=200
  expect "obsidian daily note path" "obsidian daily note" "the path follows the layout" \
    'r["path"].endswith(".md") and r["day"] in r["path"]'
  # Writes, always with --dry-run: nothing in the vault changes.
  check "obsidian plan (dry run)" -- obsidian_write_section section=smoke-test \
    "markdown=- smoke test" day=today --dry-run
  expect "obsidian plan not written" "obsidian plan (dry run)" "dry run writes nothing" \
    'r["dry_run"] is True and r["written"] is False'
  check "obsidian create task (dry run)" "$TASK_TITLE" -- obsidian_create_task \
    "title=$TASK_TITLE" --dry-run
  expect "obsidian refuses a duplicate" "obsidian create task (dry run)" \
    "an existing task's title is held back" 'r["created"] is False and r["similar"]'
  check "obsidian sync (dry run)" -- obsidian_sync days_ahead=0 --dry-run
  expect "obsidian sync writes nothing" "obsidian sync (dry run)" \
    "dry run, and every configured source read" 'r["dry_run"] is True and not r["unavailable"]'
else
  printf '\n== obsidian\n   skipped: no [obsidian] table in the config\n'
  SKIPPED+=("obsidian (not configured)")
fi

# ---- Jira and Bitbucket together (only if both are configured) ---------------

if offers jira_get_issue_pull_requests; then
  check "issue pull requests" "$JIRA_ISSUE" -- jira_get_issue_pull_requests "key=$JIRA_ISSUE"
fi

# ---- Summary ----------------------------------------------------------------

printf '\n== summary\n'
printf '   passed:  %s\n' "${#PASSED[@]}"
printf '   failed:  %s\n' "${#FAILED[@]}"
for name in ${FAILED[@]+"${FAILED[@]}"}; do printf '     - %s\n' "$name"; done
printf '   skipped: %s\n' "${#SKIPPED[@]}"
for name in ${SKIPPED[@]+"${SKIPPED[@]}"}; do printf '     - %s\n' "$name"; done
if [ "${#OBSERVED[@]}" -gt 0 ]; then
  printf '   observed:\n'
  for name in "${OBSERVED[@]}"; do printf '     - %s\n' "$name"; done
fi
printf '   full output: %s\n' "$OUT_DIR"

zip_name="$(basename $(pwd))-$(build-identifier).zip"
mkdir -p dist
zip -j -r "dist/${zip_name}" "$OUT_DIR" >/dev/null 2>&1
printf '   full output zip: dist/%s.zip\n' "${zip_name%.zip}"

[ "${#FAILED[@]}" -eq 0 ]
