#!/usr/bin/env bash
# watch.sh: stop a workers-mode team after its Delegator leaves.
#
# scripts/teamflow starts one watcher per team as a tmux background job, so
# it opens no window. Once a minute it looks at <mailbox>/delegator, the agent
# session that last ran a teamflow command (see delegator.sh). When that
# session is gone:
#   1. it waits one minute, in case the user restarts the agent. A new
#      Delegator that runs any teamflow command takes over the record and
#      cancels the stop.
#   2. then it runs scripts/teamflow trim: idle workers go, and once every
#      worker is idle the team stops like --kill. Busy workers finish their
#      tasks first, so results reach the mailbox.
#   3. it trims again every minute until 10 minutes have passed, then every
#      hour. A worker that records a result (task.sh done) or finishes a note
#      wakes the watcher, so finished workers go at once.
#   4. two hours after the Delegator left, it stops the team even if a
#      worker is still busy, in case one hangs.
# Stopping removes merged worktrees and keeps every other worktree. Nothing
# is discarded. The stop writes <mailbox>/stopped, a note the next session's
# teamflow list and start show. Progress goes to <mailbox>/watch.log.
#
# Usage: watch.sh <tmux socket> <session> <session created> <mailbox> <launcher> <root>
#                 [grace] [cap] [every] [fast] [slow]   (seconds)
set -u

socket="$1" session="$2" created="$3" mailbox="$4" launcher="$5" root="$6"
grace="${7:-60}" cap="${8:-7200}" every="${9:-60}" fast="${10:-600}" slow="${11:-3600}"

tmx() { tmux -S "$socket" "$@"; }
minutes() { local m=$(($1 / 60)); [ "$m" = 1 ] && echo "1 minute" || echo "$m minutes"; }
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

detached() { # $1 stop reason, then launcher arguments. Output in <mailbox>/watch.out
  # Stopping the last session ends the tmux server and its background jobs,
  # this watcher included. A detached run with its output in a file (not a
  # pipe) always finishes its worktree cleanup and its stop note.
  local reason="$1"
  shift
  (cd "$root" && AGENT_ROLE=teamflow-watcher TEAMFLOW_STOP_REASON="$reason" python3 -c \
    'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' \
    bash "$launcher" "$@") > "$mailbox/watch.out" 2>&1
}

nap() { # sleep $1 seconds; a USR1 from task.sh done or a finished note wakes it early
  sleep "$1" &
  wait $! 2>/dev/null
  kill $! 2>/dev/null
}
trap : USR1

# The launcher starts a watcher only when none runs for this very session.
echo "$$ $created" > "$mailbox/watcher"
log "watching $session for its Delegator (trims idle workers ${grace}s after it leaves, stops the team after ${cap}s)"
gone_since="" record="" last=""
while team_running; do
  wait_for="$every"
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
    if [ "$away" -ge "$cap" ]; then
      log "workers are still busy $(minutes "$cap") after the Delegator left. Stopping the team"
      detached "$(minutes "$cap") after the Delegator left, with workers still busy" --kill
      log "stop: $(paste -sd ' ' - < "$mailbox/watch.out")"
      break
    elif [ "$away" -ge "$grace" ]; then
      detached "$(minutes "$away") after the Delegator left, with every worker idle" trim
      out="$(cat "$mailbox/watch.out" 2>/dev/null)"
      [ "$out" = "$last" ] || log "trim: $(printf '%s' "$out" | paste -sd ' ' -)"
      last="$out"
      [ "$away" -lt "$fast" ] || wait_for="$slow"
      [ "$wait_for" -le $((cap - away)) ] || wait_for=$((cap - away))
    else
      wait_for=$((grace - away))
    fi
  fi
  nap "$wait_for"
done
[ "$(cut -d' ' -f1 "$mailbox/watcher" 2>/dev/null)" = $$ ] && rm -f "$mailbox/watcher"
log "watcher for $session ended"
