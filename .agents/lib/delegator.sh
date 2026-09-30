#!/usr/bin/env bash
# delegator.sh: run task.sh or pane.sh as this repo's Delegator from any
# terminal, e.g. a Codex, Claude, or z.ai session outside tmux while the
# workers run in the team's tmux session.
#
# Usage:
#   delegator.sh task <task.sh args...>   e.g. task new developer-l-1 'Title' <<'EOF'
#   delegator.sh pane <pane.sh args...>   e.g. pane send-to developer-l-1 "Task T-0001: ..."
#   delegator.sh register                 record the calling agent session only
#   delegator.sh alive                    exit 0 while the recorded Delegator runs
#
# AGENT_ROLE, AGENT_MAILBOX, and AGENT_LIB_DIR come from this file's own
# location (its repo's git common dir), never from the caller's environment
# or working directory, so every call reaches the same mailbox. Inside a
# worker pane (AGENT_ROLE set to another role) it refuses: a worker never
# acts as the Delegator.
#
# task, pane, and register record the Delegator in <mailbox>/delegator. The
# team's watcher (watch.sh) stops the team some time after it leaves. The
# Delegator is the nearest ancestor that is not a shell or a command
# wrapper, e.g. claude, and its start time guards against a reused process
# ID. Codex runs commands through a shared app-server daemon that serves
# every Codex session, so for Codex the record names the project instead:
# the Delegator is present while a Codex session has the project open.
set -u

fail() { echo "delegator.sh: $*" >&2; exit 1; }

case "${AGENT_ROLE:-delegator}" in
  delegator) ;;
  *) fail "AGENT_ROLE is '$AGENT_ROLE': only the Delegator runs this helper (workers use task.sh directly)" ;;
esac
tool="${1:-}"
case "$tool" in
  task|pane|register|alive) shift ;;
  *) fail "usage: delegator.sh task|pane <args...> | register | alive" ;;
esac
lib="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || fail "cannot locate $0"
common="$(git -C "$lib" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" \
  || fail "$lib is not inside a git repository"

register() { # $1 mailbox
  local pid=$$ ppid name line started kind=process
  while [ "$pid" -gt 1 ]; do
    line="$(ps -o ppid=,comm= -p "$pid" 2>/dev/null)" || return 0
    ppid="$(printf '%s' "$line" | awk '{print $1}')"
    name="$(printf '%s' "$line" | sed 's/^ *[0-9]* *//')"
    name="${name##*/}"
    case "${name#-}" in
      sh|bash|zsh|dash|fish|ksh|tcsh|csh|env|nohup|timeout|sandbox-exec|sudo|script|xargs|nice|time|caffeinate) pid="$ppid" ;;
      *) break ;;
    esac
  done
  [ "$pid" -gt 1 ] && [ -d "$1" ] || return 0
  case "$(ps -o command= -p "$pid" 2>/dev/null)" in
    *app-server-daemon*) kind=codex name=codex ;;
  esac
  started="$(ps -o lstart= -p "$pid" 2>/dev/null | sed 's/ *$//')"
  printf 'KIND=%s\nPID=%s\nSTARTED=%s\nNAME=%s\nUPDATED_AT=%s\n' "$kind" "$pid" "$started" "$name" \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$1/delegator.$$" && mv "$1/delegator.$$" "$1/delegator"
}

field() { grep -E "^$1=" "$common/teamflow/delegator" 2>/dev/null | tail -1 | cut -d= -f2-; }

codex_open() { # a Codex session (not the daemon) runs in this project or one of its worktrees
  local p cwd
  for p in $(pgrep -x codex 2>/dev/null); do
    case "$(ps -o command= -p "$p" 2>/dev/null)" in *app-server-daemon*) continue ;; esac
    cwd="$(readlink "/proc/$p/cwd" 2>/dev/null || lsof -a -d cwd -p "$p" -Fn 2>/dev/null | sed -n 's/^n//p')"
    [ -n "$cwd" ] || continue
    [ "$(git -C "$cwd" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" = "$common" ] && return 0
  done
  return 1
}

alive() { # the recorded Delegator still runs (no record: nothing to judge)
  local pid
  [ -f "$common/teamflow/delegator" ] || return 0
  [ "$(field KIND)" = codex ] && { codex_open; return; }
  pid="$(field PID)"
  [ -n "$pid" ] || return 0
  kill -0 "$pid" 2>/dev/null \
    && [ "$(ps -o lstart= -p "$pid" 2>/dev/null | sed 's/ *$//')" = "$(field STARTED)" ]
}

[ "$tool" = alive ] && { alive; exit; }
register "$common/teamflow"
[ "$tool" = register ] && exit 0

export AGENT_ROLE=delegator AGENT_ID="${AGENT_ID:-delegator}" \
  AGENT_MAILBOX="$common/teamflow" AGENT_LIB_DIR="$lib"
exec bash "$lib/$tool.sh" "$@"
