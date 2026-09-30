#!/usr/bin/env bash
# delegator.sh: run task.sh or pane.sh as this repo's Delegator from any
# terminal, e.g. a Codex, Claude, or z.ai session outside tmux while the
# workers run under `scripts/ai-team up --workers`.
#
# Usage:
#   delegator.sh task <task.sh args...>   e.g. task new dev-mid 'Title' <<'EOF'
#   delegator.sh pane <pane.sh args...>   e.g. pane send-to dev-mid "Task T-0001: ..."
#
# AGENT_ROLE, AGENT_MAILBOX, and AGENT_LIB_DIR come from this file's own
# location (its repo's git common dir), never from the caller's environment
# or working directory, so every call reaches the same mailbox. Inside a
# worker pane (AGENT_ROLE set to another role) it refuses: a worker never
# acts as the Delegator.
set -u

fail() { echo "delegator.sh: $*" >&2; exit 1; }

case "${AGENT_ROLE:-delegator}" in
  delegator) ;;
  *) fail "AGENT_ROLE is '$AGENT_ROLE': only the Delegator runs this helper (workers use task.sh directly)" ;;
esac
tool="${1:-}"
case "$tool" in
  task|pane) shift ;;
  *) fail "usage: delegator.sh task|pane <args...>" ;;
esac
lib="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || fail "cannot locate $0"
common="$(git -C "$lib" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" \
  || fail "$lib is not inside a git repository"

export AGENT_ROLE=delegator AGENT_ID="${AGENT_ID:-delegator}" \
  AGENT_MAILBOX="$common/ai-team" AGENT_LIB_DIR="$lib"
exec bash "$lib/$tool.sh" "$@"
