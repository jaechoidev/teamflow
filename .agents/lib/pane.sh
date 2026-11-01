#!/usr/bin/env bash
# pane.sh — address tmux panes by stable role for the teamflow workspace.
#
# Commands:
#   send-to <role> <text...>   type text into that role's pane + Enter.
#                              Uses send-keys -l (literal-safe: spaces, quotes,
#                              newlines arrive intact). Delivery is logged but
#                              NOT proof of execution — check the mailbox
#                              (task.sh status/read) for outcomes.
#                              send-to delegator is rejected: the Delegator
#                              pane is the user's chat, so workers report via
#                              the mailbox (task.sh done / inbox) instead.
#   tail <role> [lines]        print the pane's last lines (default 15) to see
#                              whether the CLI is busy, waiting, or errored.
#
# Pane lookup: the launch record (mailbox/active) names the team's session
# and its tmux server. The worker's pane is the one in that session whose
# window carries the worker's name (tf_worker_pane), checked again right
# before every line typed. A pane that is gone, belongs to another session,
# or sits in another window is refused, and nothing is typed anywhere else.
set -u

fail() { echo "pane.sh: $*" >&2; exit 1; }

AGENT_MAILBOX="${AGENT_MAILBOX:-}"
[ -n "$AGENT_MAILBOX" ] || fail "AGENT_MAILBOX not set (launched by scripts/teamflow?)"
command -v tmux >/dev/null 2>&1 || fail "tmux not found"
lib="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || fail "cannot locate $0"
# shellcheck source=tmux-server.sh
. "$lib/tmux-server.sh" || fail "$lib/tmux-server.sh is missing. Reinstall teamflow"

record() { grep -E "^$1=" "$AGENT_MAILBOX/active" 2>/dev/null | tail -1 | cut -d= -f2-; }

SESSION="$(record SESSION)"
CREATED="$(record CREATED)"
SOCKET="$(record SOCKET)"
LEGACY=0
[ -n "$SOCKET" ] || { SOCKET="$TEAMFLOW_LEGACY_SOCKET"; LEGACY=1; }

team_running() { # the recorded session runs on the recorded server, as the same session
  [ -n "$SESSION" ] || return 1
  tmux -L "$SOCKET" list-sessions -F '#{session_created} #{session_name}' 2>/dev/null \
    | awk -v n="$SESSION" -v c="$CREATED" '{ s = $1; sub(/^[^ ]+ /, ""); if ($0 == n && s == c) found = 1 } END { exit !found }'
}

# target_of <role>: the tmux target of that worker's pane, after checking
# that it is alive, in this project's session, and in the worker's window.
# The worker's own one-pane window is targeted by session and window name,
# any other pane (such as one in the view window) by its pane ID.
target_of() {
  local role="$1" found rc pane dead panes window
  team_running || fail "worker '$role' is not running in this project's session: no team runs here. Start it with: teamflow start --last"
  found="$(tf_worker_pane "$SOCKET" "$SESSION" "$role")"
  rc=$?
  [ "$rc" != 2 ] || fail "worker '$role' has more than one pane in $SESSION. Close the extra pane, then try again"
  [ "$rc" = 0 ] || fail "worker '$role' is not running in this project's session $SESSION. Bring it back with: teamflow add $role"
  read -r pane dead panes window <<EOF
$found
EOF
  [ "$dead" = 0 ] || fail "worker '$role' has exited in $SESSION (pane $pane). Restart it with: teamflow sync"
  # A window name reaches the window's active pane, so it is used only for
  # the worker's own one-pane window.
  if [ "$window" = "$role" ] && [ "$panes" = 1 ]; then
    printf '=%s:=%s' "$SESSION" "$role"
  else
    printf '%s' "$pane"
  fi
}

legacy_note() {
  [ "$LEGACY" = 1 ] || return 0
  echo "pane.sh: note: session $SESSION runs on the shared default tmux server, started by an older teamflow. Move it to this project's own server when its workers are idle: teamflow --kill, then teamflow start --last" >&2
}

cmd="${1:-}"; shift || true
case "$cmd" in
  send-to)
    role="${1:?usage: send-to <role> <text>}"; shift
    # The delegator pane is the user's chat: nothing may ever be typed into
    # it, not even by the delegator itself. Completions travel via the
    # mailbox (task.sh done; the delegator polls `inbox delegator`).
    [ "$role" = "delegator" ] && fail \
      "send-to delegator is forbidden: record results with task.sh done; the delegator reads them via task.sh inbox delegator"
    text="${*:?usage: send-to <role> <text>}"
    target_of "$role" >/dev/null || exit 1
    legacy_note
    # Newlines become separate lines; send-keys -l handles all other quoting.
    # The pane is checked again before each line, in case it closed.
    printf '%s' "$text" | while IFS= read -r line || [ -n "$line" ]; do
      target="$(target_of "$role")" || exit 1
      tmux -L "$SOCKET" send-keys -t "$target" -l -- "$line" || exit 1
      tmux -L "$SOCKET" send-keys -t "$target" Enter || exit 1
    done || exit 1
    # Concise delivery log: role + timestamp + first line only (no full
    # bodies, no secrets).
    printf '%s -> %s : %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$role" \
      "${text%%$'\n'*}" >> "$AGENT_MAILBOX/sends.log"
    ;;
  tail)
    role="${1:?usage: tail <role> [lines]}"; n="${2:-15}"
    target="$(target_of "$role")" || exit 1
    legacy_note
    tmux -L "$SOCKET" capture-pane -p -t "$target" -S -"$n" | sed '/^[[:space:]]*$/d' | tail -"$n"
    ;;
  *)
    fail "unknown command: ${cmd:-<none>} (send-to | tail)"
    ;;
esac
