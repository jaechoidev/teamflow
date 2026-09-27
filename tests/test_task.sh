#!/usr/bin/env bash
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/lib.sh"
TOOL="$(cd "$HERE/.." && pwd)"
TASK="$TOOL/.agents/lib/task.sh"

export AGENT_MAILBOX="$(mktemp -d)/ai-team"

id1=$(printf 'body one\nwith detail\n' | bash "$TASK" new dev-mid "Fix the widget")
id2=$(printf 'body two\n' | bash "$TASK" new researcher "Look up papers")
assert_eq "T-0001" "$id1" "first task id"
assert_eq "T-0002" "$id2" "second task id sequential"

assert_eq "assigned" "$(bash "$TASK" status "$id1")" "new task is assigned"
AGENT_ROLE=dev-mid bash "$TASK" take "$id1" >/dev/null
assert_eq "in-progress" "$(bash "$TASK" status "$id1")" "taken task is in-progress"

printf 'result: fixed in commit abc\n' | AGENT_ROLE=dev-mid bash "$TASK" done "$id1" >/dev/null
assert_eq "done" "$(bash "$TASK" status "$id1")" "done task is done"
assert_contains "$(bash "$TASK" read "$id1")" "result: fixed in commit abc" "read returns the result"
assert_contains "$(bash "$TASK" read "$id1")" "Fix the widget" "read returns the assignment"

inbox=$(bash "$TASK" inbox researcher)
assert_contains "$inbox" "T-0002" "inbox lists researcher task"
assert_eq "0" "$(bash "$TASK" inbox dev-mid | grep -c T-0002 || true)" "dev-mid inbox excludes other roles"

# ack uses AGENT_ROLE
AGENT_ROLE=researcher AGENT_ID=test-r bash "$TASK" ack >/dev/null
assert_contains "$(cat "$AGENT_MAILBOX/acks/researcher.json")" "role: researcher" "ack written per role"

# concurrent writers get distinct records (atomic mkdir)
export AGENT_ROLE=delegator
(p="$AGENT_MAILBOX"; for i in 1 2 3 4; do echo "c$i" | bash "$TASK" new dev-junior "concurrent $i" & done; wait) >/dev/null 2>&1
n=$(ls "$AGENT_MAILBOX/tasks" | wc -l | tr -d ' ')
assert_eq "6" "$n" "concurrent creates all distinct task dirs (2 serial + 4 parallel)"
assert_eq "T-0003 T-0004 T-0005 T-0006" "$(ls "$AGENT_MAILBOX/tasks" | tail -4 | tr '\n' ' ' | sed 's/ $//')" "parallel writers got distinct sequential ids"

# clean prunes only done tasks
mkdir -p "$AGENT_MAILBOX/tasks/T-9000"; echo "done" > "$AGENT_MAILBOX/tasks/T-9000/status"
touch -t 202001010000 "$AGENT_MAILBOX/tasks/T-9000"
out=$(bash "$TASK" clean 7)
assert_eq "1" "$(echo "$out" | sed 's/pruned \([0-9]*\) .*/\1/')" "clean prunes the old done task"
assert_eq "0" "$([ -d "$AGENT_MAILBOX/tasks/T-9000" ] && echo 1 || echo 0)" "old done task removed"
assert_eq "1" "$([ -d "$AGENT_MAILBOX/tasks/T-0001" ] && echo 1 || echo 0)" "recent done task kept (mtime now)"

rm -rf "$(dirname "$AGENT_MAILBOX")"
finish_tests
