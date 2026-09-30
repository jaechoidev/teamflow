#!/usr/bin/env bash
# pane.sh — address tmux panes by stable role for the ai-team workspace.
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
# Pane lookup: mailbox/panes.tsv (role<TAB>pane-id), written by the launcher.
set -u

fail() { echo "pane.sh: $*" >&2; exit 1; }

AGENT_MAILBOX="${AGENT_MAILBOX:-}"
[ -n "$AGENT_MAILBOX" ] || fail "AGENT_MAILBOX not set (launched by scripts/teamflow?)"
command -v tmux >/dev/null 2>&1 || fail "tmux not found"

pane_of() { # $1 = role -> pane id (%N)
  local role="$1" p
  p=$(awk -F '\t' -v r="$role" '$1 == r {print $2; exit}' "$AGENT_MAILBOX/panes.tsv" 2>/dev/null)
  [ -n "$p" ] || fail "no pane registered for role '$role' (workspace not up?)"
  tmux list-panes -a -F '#{pane_id}' 2>/dev/null | grep -qx "$p" || fail "pane $p for '$role' is gone"
  echo "$p"
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
    pane="$(pane_of "$role")" || exit 1
    # Newlines become separate lines; send-keys -l handles all other quoting.
    printf '%s' "$text" | while IFS= read -r line || [ -n "$line" ]; do
      tmux send-keys -t "$pane" -l -- "$line"
      tmux send-keys -t "$pane" Enter
    done
    # Concise delivery log: role + timestamp + first line only (no full
    # bodies, no secrets).
    printf '%s -> %s : %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$role" \
      "${text%%$'\n'*}" >> "$AGENT_MAILBOX/sends.log"
    ;;
  tail)
    role="${1:?usage: tail <role> [lines]}"; n="${2:-15}"
    pane="$(pane_of "$role")" || exit 1
    tmux capture-pane -p -t "$pane" -S -"$n" | sed '/^[[:space:]]*$/d' | tail -"$n"
    ;;
  *)
    fail "unknown command: ${cmd:-<none>} (send-to | tail)"
    ;;
esac
