#!/usr/bin/env bash
# watch.sh: stop a workers-mode team some time after its Delegator leaves.
#
# scripts/teamflow starts one watcher per team as a tmux background job, so
# it lives exactly as long as the team's tmux server and opens no window.
# Every check it looks at <mailbox>/delegator, the agent session that last
# ran a teamflow command (see delegator.sh). When that session is gone:
#   1. it waits a short grace period, in case the user restarts the agent.
#      A new Delegator that runs any teamflow command takes over the record
#      and cancels the stop.
#   2. then it runs scripts/teamflow trim on every check. Trim removes idle
#      workers and stops the team, like --kill, once every worker is idle.
#      Busy workers finish their tasks first, so results reach the mailbox.
#   3. a cap bounds the wait for busy workers in case one hangs. Then it
#      stops the team with --kill. Merged worktrees are removed, and every
#      other worktree is kept. Nothing is discarded.
# Progress goes to <mailbox>/watch.log.
#
# Usage: watch.sh <tmux socket> <session> <session created> <mailbox> <launcher> <root> [grace] [cap] [every]
set -u

socket="$1" session="$2" created="$3" mailbox="$4" launcher="$5" root="$6"
grace="${7:-60}" cap="${8:-7200}" every="${9:-30}"

tmx() { tmux -S "$socket" "$@"; }
log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$mailbox/watch.log"; }
field() { grep -E "^$1=" "$mailbox/delegator" 2>/dev/null | tail -1 | cut -d= -f2-; }

team_running() {
  tmx list-sessions -F '#{session_created} #{session_name}' 2>/dev/null \
    | awk -v n="$session" -v c="$created" '{ s = $1; sub(/^[^ ]+ /, ""); if ($0 == n && s == c) found = 1 } END { exit !found }'
}

delegator_alive() { bash "$root/.agents/lib/delegator.sh" alive 2>/dev/null; }

who() { # how the log names the Delegator
  if [ "$(field KIND)" = codex ]; then printf 'Codex (any Codex session in this project)'
  else printf '%s (pid %s)' "$(field NAME)" "$(field PID)"; fi
}

detached() { # run the launcher in its own session, output in <mailbox>/watch.out
  # Stopping the last session ends the tmux server and its background jobs,
  # this watcher included. A detached run with its output in a file (not a
  # pipe) always finishes its worktree cleanup.
  (cd "$root" && AGENT_ROLE=teamflow-watcher python3 -c \
    'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' \
    bash "$launcher" "$@") > "$mailbox/watch.out" 2>&1
}

# The launcher starts a watcher only when none runs for this very session.
echo "$$ $created" > "$mailbox/watcher"
log "watching $session for its Delegator (idle workers stop ${grace}s after it leaves, busy ones within ${cap}s)"
gone_since="" record="" last=""
while team_running; do
  if delegator_alive; then
    [ -z "$gone_since" ] || log "Delegator $(who) is back. The stop is cancelled"
    gone_since="" last=""
  else
    now="$(date +%s)"
    current="$(cksum < "$mailbox/delegator" 2>/dev/null)"
    if [ -z "$gone_since" ] || [ "$current" != "$record" ]; then
      gone_since="$now" record="$current" last=""
      log "Delegator $(who) is gone. Idle workers stop in ${grace}s unless a Delegator returns"
    fi
    away=$((now - gone_since))
    if [ "$away" -ge $((grace + cap)) ]; then
      log "workers are still busy after ${cap}s. Stopping the team anyway"
      detached --kill
      log "stop: $(paste -sd ' ' - < "$mailbox/watch.out")"
      break
    elif [ "$away" -ge "$grace" ]; then
      # teamflow trim removes idle workers, and stops the team once every
      # worker is idle. Busy workers finish their tasks first.
      detached trim
      out="$(cat "$mailbox/watch.out" 2>/dev/null)"
      [ "$out" = "$last" ] || log "trim: $(printf '%s' "$out" | paste -sd ' ' -)"
      last="$out"
    fi
  fi
  sleep "$every"
done
[ "$(cut -d' ' -f1 "$mailbox/watcher" 2>/dev/null)" = $$ ] && rm -f "$mailbox/watcher"
log "watcher for $session ended"
