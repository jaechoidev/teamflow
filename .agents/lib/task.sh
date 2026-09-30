#!/usr/bin/env bash
# task.sh — shared task mailbox for the teamflow workspace.
#
# Lives at .agents/lib/task.sh inside the project (copied by `teamflow init`).
# State lives under the git COMMON dir (<repo>/.git/teamflow/) so every
# worktree sees it immediately, without commits and without git-status noise.
#
# Commands:
#   new <to-role> <title>     create a task; body from stdin; prints task id
#   take <id>                 claim: status assigned -> in-progress;
#                             exclusive (atomic mkdir of <id>/claim) and only
#                             for the role the task is addressed to
#   done <id>                 complete: result from stdin, status -> done;
#                             only the addressee/claimant, exactly once
#   read <id>                 print task + result (if any)
#   status <id>               print status word
#   inbox <role>              list tasks addressed to <role>; for delegator,
#                             also completed tasks it dispatched
#   list                      list all tasks (one line each)
#   ack                       record that this role loaded its instructions
#   release <id> [commit]      delegator confirms result was read and any code
#                             change integrated; queue the note pass, which
#                             waits until a notetaker is in the team
#   noted <id> <summary>      notetaker finishes the note pass and removes the
#                             completed task, then receives the next one
#   wake                      internal: wake the team's watcher (watch.sh)
#
# Concurrency: one directory per task; creation is an atomic mkdir race;
# claims likewise: `take` and `done` both gate on the task's claim/
# directory (atomic mkdir or owner match), so take/done races serialize.
# Completion is exactly-once via an atomic mkdir of claim/completed, and
# result.md lands through a rename so readers never see partial output.
# No shared mutable research.md/review.md — ever.
set -u

fail() { echo "task.sh: $*" >&2; exit 1; }

AGENT_MAILBOX="${AGENT_MAILBOX:-}"
[ -n "$AGENT_MAILBOX" ] || fail "AGENT_MAILBOX not set (launched by scripts/teamflow?)"
TASKS="$AGENT_MAILBOX/tasks"
ACKS="$AGENT_MAILBOX/acks"
mkdir -p "$TASKS" "$ACKS" 2>/dev/null || fail "cannot write mailbox at $AGENT_MAILBOX"

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
json_str() { # render $1 as a JSON string literal (escape \ and ")
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  printf '"%s"' "$s"
}
wake_watcher() { # a finished task lets the watcher trim its worker at once
  local pid rest
  read -r pid rest 2>/dev/null < "$AGENT_MAILBOX/watcher" || return 0
  ps -o command= -p "$pid" 2>/dev/null | grep -q 'watch\.sh' && kill -USR1 "$pid" 2>/dev/null
  return 0
}

next_id() { # monotonic even after the notetaker removes completed records
  local i=0 d tries=0 tmp
  until mkdir "$AGENT_MAILBOX/id-lock" 2>/dev/null; do
    tries=$((tries + 1))
    [ "$tries" -lt 100 ] || fail "task id lock is busy"
    sleep 0.1
  done
  if [ -f "$AGENT_MAILBOX/next-id" ]; then
    i=$(cat "$AGENT_MAILBOX/next-id")
  else
    for d in "$TASKS"/T-*; do
      [ -d "$d" ] || continue
      d="${d##*/}"; d="${d#T-}"
      [ $((10#$d)) -gt "$i" ] && i=$((10#$d))
    done
    i=$((i + 1))
  fi
  [ "$i" -le 9999 ] || { rmdir "$AGENT_MAILBOX/id-lock"; fail "task id space exhausted"; }
  d=$(printf 'T-%04d' "$i")
  mkdir "$TASKS/$d" || { rmdir "$AGENT_MAILBOX/id-lock"; fail "cannot create $d"; }
  tmp="$AGENT_MAILBOX/.next-id.$$"
  echo $((i + 1)) > "$tmp" && mv "$tmp" "$AGENT_MAILBOX/next-id"
  rmdir "$AGENT_MAILBOX/id-lock"
  echo "$d"
}

cmd="${1:-}"; shift || true
case "$cmd" in
  new)
    to="${1:?usage: new <to-role> <title>}"; title="${2:?usage: new <to-role> <title>}"
    # The pane registry holds the team's current roster. A typo or a removed
    # worker would otherwise get a task that nobody ever takes.
    if [ "$to" != delegator ] && [ -f "$AGENT_MAILBOX/panes.tsv" ] \
       && ! awk -F '\t' -v r="$to" '$1 == r { found = 1 } END { exit !found }' "$AGENT_MAILBOX/panes.tsv"; then
      fail "no worker '$to' in the team roster ($(cut -f1 "$AGENT_MAILBOX/panes.tsv" | grep -vx delegator | paste -sd ' ' -)). Check: scripts/teamflow workers list"
    fi
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
    role="${AGENT_ROLE:-}"
    [ -n "$role" ] || fail "take requires AGENT_ROLE (run from a teamflow pane)"
    to=$(sed -n 's/^to:      //p' "$TASKS/$id/task.md" | head -1)
    [ "$to" = "$role" ] || fail "$id is addressed to ${to:-unknown}, not $role"
    [ "$(cat "$TASKS/$id/status" 2>/dev/null)" = "done" ] && fail "$id already done"
    # exclusive claim: only one mkdir of claim/ can ever succeed
    if ! mkdir "$TASKS/$id/claim" 2>/dev/null; then
      fail "$id already claimed by $(cat "$TASKS/$id/claim/owner" 2>/dev/null || echo 'another role')"
    fi
    echo "$role" > "$TASKS/$id/claim/owner"
    echo "in-progress" > "$TASKS/$id/status"
    echo "$(now) taken by $role" >> "$TASKS/$id/events"
    ;;
  done)
    id="${1:?usage: done <id>}"
    [ -d "$TASKS/$id" ] || fail "no such task: $id"
    role="${AGENT_ROLE:-}"
    [ -n "$role" ] || fail "done requires AGENT_ROLE (run from a teamflow pane)"
    to=$(sed -n 's/^to:      //p' "$TASKS/$id/task.md" | head -1)
    [ "$to" = "$role" ] || fail "$id is addressed to ${to:-unknown}, not $role"
    [ "$(cat "$TASKS/$id/status" 2>/dev/null)" = "done" ] && fail "$id is already done — results are immutable"
    # Hold the claim: win its mkdir (recording an auto-claim event) or be
    # its recorded owner. take races serialize here for the same reason.
    if ! mkdir "$TASKS/$id/claim" 2>/dev/null; then
      owner="$(cat "$TASKS/$id/claim/owner" 2>/dev/null)"
      [ "$owner" = "$role" ] || fail "$id is claimed by ${owner:-another role}"
    else
      echo "$role" > "$TASKS/$id/claim/owner"
      echo "$(now) taken by $role" >> "$TASKS/$id/events"
    fi
    # Exactly-once completion: only the first mkdir of claim/completed wins;
    # the result is staged then renamed so result.md is never partial.
    tmp="$TASKS/$id/.result.$$"
    cat > "$tmp"
    if ! mkdir "$TASKS/$id/claim/completed" 2>/dev/null; then
      rm -f "$tmp"
      fail "$id already completed by $(cat "$TASKS/$id/claim/completed/by" 2>/dev/null || echo another run)"
    fi
    echo "$role" > "$TASKS/$id/claim/completed/by"
    mv "$tmp" "$TASKS/$id/result.md"
    echo "done" > "$TASKS/$id/status"
    echo "$(now) done by $role" >> "$TASKS/$id/events"
    wake_watcher
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
    # Nothing is ever typed into the delegator pane (pane.sh rejects it), so
    # the delegator discovers worker completions here: every task it
    # dispatched that has reached `done`.
    if [ "$role" = "delegator" ]; then
      echo "-- completed dispatches --"
      for d in "$TASKS"/T-*; do
        [ -d "$d" ] || continue
        from=$(sed -n 's/^from:    //p' "$d/task.md" | head -1)
        [ "$from" = "delegator" ] || continue
        [ "$(cat "$d/status" 2>/dev/null)" = "done" ] || continue
        printf '%s  %-12s -> %-10s %s\n' "$(basename "$d")" "$(cat "$d/status" 2>/dev/null)" \
          "$(sed -n 's/^to:      //p' "$d/task.md" | head -1)" \
          "$(sed -n 's/^title:   //p' "$d/task.md" | head -1)"
      done
    fi
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
    printf '{\n  "role": %s,\n  "id": %s,\n  "time": %s,\n  "cwd": %s\n}\n' \
      "$(json_str "$role")" "$(json_str "${AGENT_ID:-?}")" \
      "$(json_str "$(now)")" "$(json_str "$PWD")" > "$ACKS/$role.json"
    echo "acked: $role"
    ;;
  release)
    id="${1:?usage: release <id> [integrated-commit]}"
    [[ "$id" =~ ^T-[0-9]{4}$ ]] || fail "invalid task id: $id"
    [ "${AGENT_ROLE:-}" = delegator ] || fail "release is restricted to the delegator"
    [ "$(cat "$TASKS/$id/status" 2>/dev/null)" = done ] || fail "$id is not done"
    [ ! -f "$TASKS/$id/released" ] || fail "$id is already released"
    printf 'released: %s\nintegrated_commit: %s\n' "$(now)" "${2:-}" > "$TASKS/$id/released"
    bash "$(dirname "$0")/note-queue.sh" poke
    # Notes belong to the notetaker. Without one, the task waits for one.
    if ! awk -F '\t' '$1 == "notetaker" { found = 1 } END { exit !found }' "$AGENT_MAILBOX/panes.tsv" 2>/dev/null; then
      echo "no notetaker in the team: $id waits for one (scripts/teamflow workers add notetaker)"
    fi
    ;;
  wake)
    wake_watcher
    ;;
  noted)
    id="${1:?usage: noted <id> <summary>}"
    summary="${2:?usage: noted <id> <summary>}"
    bash "$(dirname "$0")/note-queue.sh" finish "$id" "$summary"
    ;;
  *)
    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//' >&2
    fail "unknown command: ${cmd:-<none>}"
    ;;
esac
