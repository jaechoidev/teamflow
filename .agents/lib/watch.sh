#!/usr/bin/env bash
# watch.sh: stop a workers-mode team some time after its Delegator leaves.
#
# scripts/teamflow starts one watcher per team as a tmux background job, so
# it lives exactly as long as the team's tmux server and opens no window.
# Every check it looks at <mailbox>/delegator, the agent process that last
# ran a teamflow command (see delegator.sh). When that process is gone:
#   1. it waits a grace period, because the user may be restarting or
#      resuming the agent. A new Delegator that runs any teamflow command
#      takes over the record and cancels the stop.
#   2. then it waits while tasks are assigned or in progress, or a note is
#      being written, so finished results reach the mailbox. A cap bounds
#      this wait in case a worker hangs.
#   3. then it stops the team with scripts/teamflow --kill. That removes
#      merged worktrees and keeps every other worktree. Nothing is discarded.
# Progress goes to <mailbox>/watch.log.
#
# Usage: watch.sh <tmux socket> <session> <session created> <mailbox> <launcher> <root> [grace] [cap] [every]
set -u

socket="$1" session="$2" created="$3" mailbox="$4" launcher="$5" root="$6"
grace="${7:-600}" cap="${8:-7200}" every="${9:-30}"

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

busy() { # a task is assigned or in progress, or a note is being written
  local d
  [ -f "$mailbox/note-queue/active" ] && return 0
  for d in "$mailbox"/tasks/T-*; do
    [ -f "$d/status" ] || continue
    case "$(cat "$d/status")" in assigned|in-progress) return 0 ;; esac
  done
  return 1
}

echo $$ > "$mailbox/watcher"
log "watching $session for its Delegator (grace ${grace}s, task cap ${cap}s)"
gone_since="" record="" waiting=0
while team_running; do
  if delegator_alive; then
    [ -z "$gone_since" ] || log "Delegator $(who) is back. The stop is cancelled"
    gone_since="" waiting=0
  else
    now="$(date +%s)"
    current="$(cksum < "$mailbox/delegator" 2>/dev/null)"
    if [ -z "$gone_since" ] || [ "$current" != "$record" ]; then
      gone_since="$now" record="$current" waiting=0
      log "Delegator $(who) is gone. The team stops in ${grace}s unless a Delegator returns"
    fi
    away=$((now - gone_since))
    if [ "$away" -ge "$grace" ]; then
      if busy && [ "$away" -lt $((grace + cap)) ]; then
        [ "$waiting" = 1 ] || log "tasks are still in progress. Waiting for them, up to ${cap}s"
        waiting=1
      else
        busy && log "tasks are still in progress after ${cap}s. Stopping anyway"
        log "stopping the team: no Delegator for ${away}s"
        # Killing the last session ends the tmux server and its background
        # jobs, this one included. The stop runs in its own session so its
        # worktree cleanup always finishes.
        (cd "$root" && python3 -c 'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' \
          bash "$launcher" --kill) >> "$mailbox/watch.log" 2>&1
        break
      fi
    fi
  fi
  sleep "$every"
done
[ "$(cat "$mailbox/watcher" 2>/dev/null)" = $$ ] && rm -f "$mailbox/watcher"
log "watcher for $session ended"
