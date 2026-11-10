#!/usr/bin/env bash
#
# Smoke test: run every tool once against your real servers with `call`,
# no agent and no model credits. Nothing here changes Jira or Bitbucket:
# the only write tool it runs is forced into dry-run.
#
# 1. Replace the <placeholder> values below.
# 2. Run it from anywhere:  ./scripts/smoke-test.sh
#    Add extra options for every call after the script name, for example:
#      ./scripts/smoke-test.sh --log-level debug
#
# Full output of each call is kept in a temporary folder, printed at the end.

set -u

# ---- Edit these -------------------------------------------------------------

JIRA_ISSUE="<ISSUE-123>"          # an issue you can see, e.g. PROJ-123
JIRA_JQL="<jql-query>"            # e.g. assignee = currentUser() ORDER BY updated DESC

BITBUCKET_PROJECT="<project-key>" # e.g. PROJ
BITBUCKET_REPO="<repo-slug>"      # e.g. my-service
BITBUCKET_PR="<pr-number>"        # a pull request in that repo, e.g. 42
BITBUCKET_SEARCH="<search-text>"  # a string you know is in some repo
BITBUCKET_COMMIT="<commit-hash>"  # a commit with CI builds, e.g. the PR's latest_commit
# Deployments cannot be listed; fill these from your CI/CD server to check one.
BITBUCKET_DEPLOY_KEY="<deployment-key>"
BITBUCKET_DEPLOY_ENV="<environment-key>"
BITBUCKET_DEPLOY_RUN="<sequence-number>"

# ---- No need to edit below --------------------------------------------------

EXTRA_ARGS=("$@")
LINES_SHOWN=25

cd "$(dirname "$0")/.." || exit 1
OUT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/productivity-smoke.XXXXXX")"
PASSED=()
FAILED=()
SKIPPED=()

tool() {
  uv run productivity-agent-tools call "$@" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
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

# ---- Jira (only if [jira] is in your config) --------------------------------

if offers jira_status; then
  check "jira status" -- jira_status
  check "jira presets" -- jira_list_presets
  check "jira run preset" -- jira_run_preset name=mine-open max_results=3
  check "jira search" "$JIRA_JQL" -- jira_search "jql=$JIRA_JQL" max_results=3
  check "jira get issue" "$JIRA_ISSUE" -- jira_get_issue "key=$JIRA_ISSUE" max_comments=2
  check "jira transitions" "$JIRA_ISSUE" -- jira_list_transitions "key=$JIRA_ISSUE"
  # A write tool, forced into dry-run: it shows the request and sends nothing.
  check "jira comment (dry run)" "$JIRA_ISSUE" -- \
    jira_add_comment "key=$JIRA_ISSUE" "body=smoke test, not sent" --dry-run
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
  check "bitbucket required builds" "${B_REPO[@]}" -- \
    bitbucket_list_required_builds "repository=$BITBUCKET_REPO" "project=$BITBUCKET_PROJECT"
  check "bitbucket commit builds" "$BITBUCKET_COMMIT" -- \
    bitbucket_get_commit_builds "commit_id=$BITBUCKET_COMMIT" max_results=5
  check "bitbucket deployment" "${B_REPO[@]}" "$BITBUCKET_COMMIT" "$BITBUCKET_DEPLOY_KEY" \
    "$BITBUCKET_DEPLOY_ENV" "$BITBUCKET_DEPLOY_RUN" -- \
    bitbucket_get_deployment "repository=$BITBUCKET_REPO" "project=$BITBUCKET_PROJECT" \
    "commit_id=$BITBUCKET_COMMIT" "deployment_key=$BITBUCKET_DEPLOY_KEY" \
    "environment_key=$BITBUCKET_DEPLOY_ENV" "sequence_number=$BITBUCKET_DEPLOY_RUN"
  check "bitbucket code search" "$BITBUCKET_SEARCH" -- \
    bitbucket_search_code "query=$BITBUCKET_SEARCH" max_results=3
else
  printf '\n== bitbucket\n   skipped: no [bitbucket] table in the config\n'
  SKIPPED+=("bitbucket (not configured)")
fi

# ---- Summary ----------------------------------------------------------------

printf '\n== summary\n'
printf '   passed:  %s\n' "${#PASSED[@]}"
printf '   failed:  %s\n' "${#FAILED[@]}"
for name in ${FAILED[@]+"${FAILED[@]}"}; do printf '     - %s\n' "$name"; done
printf '   skipped: %s\n' "${#SKIPPED[@]}"
for name in ${SKIPPED[@]+"${SKIPPED[@]}"}; do printf '     - %s\n' "$name"; done
printf '   full output: %s\n' "$OUT_DIR"

[ "${#FAILED[@]}" -eq 0 ]
