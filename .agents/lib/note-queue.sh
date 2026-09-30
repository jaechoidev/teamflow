#!/usr/bin/env bash
# Event-driven handoff to the notetaker. No polling or model call while idle.
set -u

fail() { echo "note-queue.sh: $*" >&2; exit 1; }
mailbox="${AGENT_MAILBOX:-}"
[ -n "$mailbox" ] || fail "AGENT_MAILBOX not set"
tasks="$mailbox/tasks"
queue="$mailbox/note-queue"
lib="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$queue" || fail "cannot create $queue"

valid_id() { [[ "$1" =~ ^T-[0-9]{4}$ ]]; }
lock() {
  local tries=0
  until mkdir "$queue/lock" 2>/dev/null; do
    tries=$((tries + 1))
    [ "$tries" -lt 100 ] || fail "queue lock is busy; inspect $queue/lock"
    sleep 0.1
  done
  trap 'rmdir "$queue/lock" 2>/dev/null' EXIT
}

notify_next() {
  local id pane
  [ -f "$mailbox/panes.tsv" ] || return 0
  # The Notetaker acknowledges only after its CLI clears any project trust
  # prompt. A release before then stays queued instead of typing into it.
  [ -f "$mailbox/acks/notetaker.json" ] || return 0
  pane=$(awk -F '\t' '$1 == "notetaker" {print $2; exit}' "$mailbox/panes.tsv")
  [ -n "$pane" ] || return 0
  if [ -f "$queue/active" ]; then return 0; fi
  for d in "$tasks"/T-*; do
    [ -d "$d" ] || continue
    [ -f "$d/released" ] && [ "$(cat "$d/status")" = done ] || continue
    id="${d##*/}"
    printf '%s\n' "$id" > "$queue/active"
    if ! bash "$lib/pane.sh" send-to notetaker "Note task $id. Read it with task.sh read $id, investigate the underlying evidence, then run task.sh noted $id."; then
      rm -f "$queue/active"
      echo "note-queue.sh: $id remains queued; restart the Notetaker to resume" >&2
      return 0
    fi
    return 0
  done
}

case "${1:-}" in
  poke)
    lock
    notify_next
    ;;
  resume)
    lock
    # A restarted pane may have missed its notification. Deliver it again.
    if [ -f "$queue/active" ]; then
      id="$(cat "$queue/active")"
      rm -f "$queue/active"
      [ -d "$tasks/$id" ] || id=""
    fi
    notify_next
    ;;
  finish)
    id="${2:-}"
    summary="${3:-}"
    valid_id "$id" || fail "invalid task id: $id"
    [ -n "$summary" ] || fail "note summary is required"
    [ "${AGENT_ROLE:-}" = notetaker ] || fail "only the notetaker finishes note tasks"
    lock
    [ "$(cat "$queue/active" 2>/dev/null)" = "$id" ] || fail "$id is not the active note task"
    [ -f "$tasks/$id/released" ] || fail "$id was not released by the Delegator"
    [ "$(cat "$tasks/$id/status" 2>/dev/null)" = done ] || fail "$id is not done"
    mkdir -p "$queue/completed"
    printf 'task: %s\nnoted: %s\nsummary: %s\n' "$id" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$summary" > "$queue/completed/$id.md"
    rm -rf "$tasks/$id" || fail "cannot remove $id"
    rm -f "$queue/active"
    notify_next
    ;;
  *) fail "usage: note-queue.sh poke|resume|finish <id>" ;;
esac
