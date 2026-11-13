#!/usr/bin/env bash
#
# Smoke test: run every tool once against your real servers with `call`,
# no agent and no model credits. Nothing here changes Jira, Bitbucket or Confluence:
# every write tool it runs is forced into dry-run.
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
OUT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/productivity-smoke.XXXXXX")"
PASSED=()
FAILED=()
SKIPPED=()

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

  local file="$OUT_DIR/${name// /_}.txt"
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
  fi
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
printf '   full output: %s\n' "$OUT_DIR"

zip_name="$(basename $(pwd))-$(build-identifier).zip"
mkdir -p dist
zip -j -r "dist/${zip_name}" "$OUT_DIR" >/dev/null 2>&1
printf '   full output zip: dist/%s.zip\n' "${zip_name%.zip}"

[ "${#FAILED[@]}" -eq 0 ]
