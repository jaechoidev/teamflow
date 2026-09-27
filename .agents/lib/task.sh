#!/usr/bin/env bash
# task.sh — shared task mailbox for the ai-team workspace.
#
# Lives at .agents/lib/task.sh inside the project (copied by `ai-team init`).
# State lives under the git COMMON dir (<repo>/.git/ai-team/) so every
# worktree sees it immediately, without commits and without git-status noise.
#
# Commands:
#   new <to-role> <title>     create a task; body from stdin; prints task id
#   take <id>                 claim: status assigned -> in-progress
#   done <id>                 complete: result from stdin, status -> done
#   read <id>                 print task + result (if any)
#   status <id>               print status word
#   inbox <role>              list tasks addressed to <role>
#   list                      list all tasks (one line each)
#   ack                       record that this role loaded its instructions
#   clean [days]              prune done tasks older than N days (default 7)
#
# Concurrency: one directory per task; creation is an atomic mkdir race;
# status updates are writes to distinct files, so parallel writers never
# share a file. No shared mutable research.md/review.md — ever.
set -u

fail() { echo "task.sh: $*" >&2; exit 1; }

AGENT_MAILBOX="${AGENT_MAILBOX:-}"
[ -n "$AGENT_MAILBOX" ] || fail "AGENT_MAILBOX not set (launched by scripts/ai-team?)"
TASKS="$AGENT_MAILBOX/tasks"
ACKS="$AGENT_MAILBOX/acks"
mkdir -p "$TASKS" "$ACKS" 2>/dev/null || fail "cannot write mailbox at $AGENT_MAILBOX"

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
next_id() { # atomic: find first unused T-<counter> directory
  local i=1 d
  while :; do
    d=$(printf 'T-%04d' "$i")
    if mkdir "$TASKS/$d" 2>/dev/null; then echo "$d"; return 0; fi
    i=$((i + 1))
    [ "$i" -gt 9999 ] && fail "task id space exhausted"
  done
}

cmd="${1:-}"; shift || true
case "$cmd" in
  new)
    to="${1:?usage: new <to-role> <title>}"; title="${2:?usage: new <to-role> <title>}"
    id="$(next_id)" || exit 1
    from="${AGENT_ROLE:-user}"
    { echo "id:      $id"
      echo "from:    $from"
      echo "to:      $to"
      echo "title:   $title"
      echo "created: $(now)"
      echo
      cat
    } > "$TASKS/$id/task.md"
    echo "assigned" > "$TASKS/$id/status"
    echo "$id"
    ;;
  take)
    id="${1:?usage: take <id>}"
    [ -d "$TASKS/$id" ] || fail "no such task: $id"
    [ "$(cat "$TASKS/$id/status" 2>/dev/null)" = "done" ] && fail "$id already done"
    echo "in-progress" > "$TASKS/$id/status"
    echo "$(now) taken by ${AGENT_ROLE:-?}" >> "$TASKS/$id/events"
    ;;
  done)
    id="${1:?usage: done <id>}"
    [ -d "$TASKS/$id" ] || fail "no such task: $id"
    cat > "$TASKS/$id/result.md"
    echo "done" > "$TASKS/$id/status"
    echo "$(now) done by ${AGENT_ROLE:-?}" >> "$TASKS/$id/events"
    ;;
  read)
    id="${1:?usage: read <id>}"
    [ -d "$TASKS/$id" ] || fail "no such task: $id"
    cat "$TASKS/$id/task.md"
    if [ -f "$TASKS/$id/result.md" ]; then
      echo; echo "----- result -----"; cat "$TASKS/$id/result.md"
    fi
    ;;
  status)
    id="${1:?usage: status <id>}"
    [ -d "$TASKS/$id" ] || fail "no such task: $id"
    cat "$TASKS/$id/status"
    ;;
  inbox)
    role="${1:?usage: inbox <role>}"
    for d in "$TASKS"/T-*; do
      [ -d "$d" ] || continue
      to=$(sed -n 's/^to:      //p' "$d/task.md" | head -1)
      [ "$to" = "$role" ] || continue
    printf '%s  %-12s %s\n' "$(basename "$d")" "$(cat "$d/status" 2>/dev/null)" \
      "$(sed -n 's/^title:   //p' "$d/task.md" | head -1)"
    done
    ;;
  list)
    for d in "$TASKS"/T-*; do
      [ -d "$d" ] || continue
    printf '%s  %-12s -> %-10s %s\n' "$(basename "$d")" "$(cat "$d/status" 2>/dev/null)" \
      "$(sed -n 's/^to:      //p' "$d/task.md" | head -1)" \
      "$(sed -n 's/^title:   //p' "$d/task.md" | head -1)"
    done
    ;;
  ack)
    role="${AGENT_ROLE:-unknown}"
    printf 'role: %s\nid: %s\ntime: %s\ncwd: %s\n' \
      "$role" "${AGENT_ID:-?}" "$(now)" "$PWD" > "$ACKS/$role.json"
    echo "acked: $role"
    ;;
  clean)
    days="${1:-7}"
    [ "$days" -gt 0 ] 2>/dev/null || fail "days must be a positive integer"
    cutoff=$(date -v-${days}d +%s 2>/dev/null || date -d "-${days} days" +%s)
    n=0
    for d in "$TASKS"/T-*; do
      [ -d "$d" ] || continue
      [ "$(cat "$d/status" 2>/dev/null)" = "done" ] || continue
      t=$(stat -f %m "$d" 2>/dev/null || stat -c %Y "$d" 2>/dev/null || echo 0)
      if [ "$t" -lt "$cutoff" ]; then rm -rf "$d" && n=$((n + 1)); fi
    done
    echo "pruned $n done task(s) older than $days day(s)"
    ;;
  *)
    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//' >&2
    fail "unknown command: ${cmd:-<none>}"
    ;;
esac
