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

# ack uses AGENT_ROLE; the .json file holds valid JSON
AGENT_ROLE=researcher AGENT_ID=test-r bash "$TASK" ack >/dev/null
assert_contains "$(cat "$AGENT_MAILBOX/acks/researcher.json")" '"role": "researcher"' "ack written per role"
if command -v python3 >/dev/null 2>&1; then
  ok=$(python3 -m json.tool "$AGENT_MAILBOX/acks/researcher.json" >/dev/null 2>&1 && echo yes || echo no)
  assert_eq "yes" "$ok" "ack file parses as JSON"
else
  assert_eq "{" "$(sed -n '1p' "$AGENT_MAILBOX/acks/researcher.json")" "ack JSON opens"
  assert_eq "}" "$(tail -1 "$AGENT_MAILBOX/acks/researcher.json")" "ack JSON closes"
fi

# concurrent writers get distinct records (atomic mkdir)
export AGENT_ROLE=delegator
(p="$AGENT_MAILBOX"; for i in 1 2 3 4; do echo "c$i" | bash "$TASK" new dev-junior "concurrent $i" & done; wait) >/dev/null 2>&1
n=$(ls "$AGENT_MAILBOX/tasks" | wc -l | tr -d ' ')
assert_eq "6" "$n" "concurrent creates all distinct task dirs (2 serial + 4 parallel)"
assert_eq "T-0003 T-0004 T-0005 T-0006" "$(ls "$AGENT_MAILBOX/tasks" | tail -4 | tr '\n' ' ' | sed 's/ $//')" "parallel writers got distinct sequential ids"

# take: only the addressed role may claim
idt=$(printf 'claim rules\n' | bash "$TASK" new researcher "Take semantics")
rc=0; out=$(AGENT_ROLE=dev-mid bash "$TASK" take "$idt" 2>&1) || rc=$?
assert_eq "1" "$rc" "wrong-role take exits nonzero"
assert_contains "$out" "addressed to researcher, not dev-mid" "wrong-role take message"
assert_eq "assigned" "$(bash "$TASK" status "$idt")" "rejected take leaves task assigned"

rc=0; out=$(AGENT_ROLE= bash "$TASK" take "$idt" 2>&1) || rc=$?
assert_eq "1" "$rc" "take without AGENT_ROLE exits nonzero"
assert_contains "$out" "AGENT_ROLE" "roleless take message"

AGENT_ROLE=researcher bash "$TASK" take "$idt" >/dev/null
assert_eq "in-progress" "$(bash "$TASK" status "$idt")" "rightful take succeeds"
assert_eq "researcher" "$(cat "$AGENT_MAILBOX/tasks/$idt/claim/owner")" "claim owner recorded"

rc=0; out=$(AGENT_ROLE=researcher bash "$TASK" take "$idt" 2>&1) || rc=$?
assert_eq "1" "$rc" "duplicate take exits nonzero"
assert_contains "$out" "already claimed by researcher" "duplicate take names the owner"
assert_eq "1" "$(grep -c 'taken by' "$AGENT_MAILBOX/tasks/$idt/events" || true)" "single claim event"

# take: parallel claims by the same role - exactly one wins
idr=$(printf 'race\n' | bash "$TASK" new researcher "Parallel claim race")
p="$(dirname "$AGENT_MAILBOX")"
for i in 1 2 3 4 5 6; do
  ( AGENT_ROLE=researcher bash "$TASK" take "$idr" >/dev/null 2>&1 && touch "$p/win_$i" ) &
done
wait
assert_eq "1" "$(ls "$p" | grep -c '^win_' || true)" "parallel takes: exactly one winner"
assert_eq "in-progress" "$(bash "$TASK" status "$idr")" "raced task claimed once, in-progress"
rm -f "$p"/win_*

# take: done tasks stay untakeable
printf 'done body\n' | AGENT_ROLE=researcher bash "$TASK" done "$idt" >/dev/null
rc=0; out=$(AGENT_ROLE=researcher bash "$TASK" take "$idt" 2>&1) || rc=$?
assert_eq "1" "$rc" "take after done exits nonzero"
assert_contains "$out" "already done" "take after done message"

# inbox delegator surfaces completed dispatches (mailbox replaces pings)
idd=$(printf 'dispatch body\n' | AGENT_ROLE=delegator bash "$TASK" new researcher "Completed dispatch")
idp=$(printf 'dispatch body\n' | AGENT_ROLE=delegator bash "$TASK" new researcher "Open dispatch")
AGENT_ROLE=researcher bash "$TASK" take "$idd" >/dev/null
printf 'found it\n' | AGENT_ROLE=researcher bash "$TASK" done "$idd" >/dev/null
out=$(bash "$TASK" inbox delegator)
assert_contains "$out" "-- completed dispatches --" "delegator inbox has completed section"
assert_contains "$out" "$idd  done" "completed dispatch listed with id and status"
assert_contains "$out" "Completed dispatch" "completed dispatch title shown"
assert_eq "0" "$(printf '%s' "$out" | grep -c "$idp" || true)" "open dispatch not in completed section"
assert_eq "0" "$(bash "$TASK" inbox researcher | grep -c 'completed dispatches' || true)" "worker inbox has no completed section"

# done: only the addressee/claimant, exactly once
idd2=$(printf 'completion rules\n' | AGENT_ROLE=delegator bash "$TASK" new dev-junior "Done semantics")
rc=0; out=$(printf 'evil\n' | AGENT_ROLE=dev-mid bash "$TASK" done "$idd2" 2>&1) || rc=$?
assert_eq "1" "$rc" "wrong-role done exits nonzero"
assert_contains "$out" "addressed to dev-junior, not dev-mid" "wrong-role done message"
assert_eq "assigned" "$(bash "$TASK" status "$idd2")" "rejected done leaves status assigned"
assert_eq "0" "$([ -f "$AGENT_MAILBOX/tasks/$idd2/result.md" ] && echo 1 || echo 0)" "rejected done writes no result"

rc=0; out=$(printf 'x\n' | AGENT_ROLE= bash "$TASK" done "$idd2" 2>&1) || rc=$?
assert_eq "1" "$rc" "roleless done exits nonzero"
assert_contains "$out" "AGENT_ROLE" "roleless done message"

# addressee may complete without a prior take (auto-claim)
printf 'auto-claim result\n' | AGENT_ROLE=dev-junior bash "$TASK" done "$idd2" >/dev/null
assert_eq "done" "$(bash "$TASK" status "$idd2")" "addressee done succeeds"
assert_contains "$(bash "$TASK" read "$idd2")" "auto-claim result" "result recorded"
assert_contains "$(cat "$AGENT_MAILBOX/tasks/$idd2/events")" "taken by dev-junior" "auto-claim recorded"

# repeated completion rejected; first result survives
rc=0; out=$(printf 'second\n' | AGENT_ROLE=dev-junior bash "$TASK" done "$idd2" 2>&1) || rc=$?
assert_eq "1" "$rc" "repeat done exits nonzero"
assert_contains "$out" "already" "repeat done message"
assert_contains "$(cat "$AGENT_MAILBOX/tasks/$idd2/result.md")" "auto-claim result" "first result survives"
assert_eq "1" "$(grep -c 'done by' "$AGENT_MAILBOX/tasks/$idd2/events" || true)" "single done event"

# claim owner mismatch rejected (defensive: handoff or tampered claim)
idt2=$(printf 'handoff\n' | AGENT_ROLE=delegator bash "$TASK" new researcher "Owner guard")
AGENT_ROLE=researcher bash "$TASK" take "$idt2" >/dev/null
echo "reviewer" > "$AGENT_MAILBOX/tasks/$idt2/claim/owner"
rc=0; out=$(printf 'r\n' | AGENT_ROLE=researcher bash "$TASK" done "$idt2" 2>&1) || rc=$?
assert_eq "1" "$rc" "non-owner done exits nonzero"
assert_contains "$out" "claimed by reviewer" "non-owner done message"

# race: parallel done by the addressee - exactly one result, one done event
idr2=$(printf 'race\n' | AGENT_ROLE=delegator bash "$TASK" new dev-junior "Parallel done race")
AGENT_ROLE=dev-junior bash "$TASK" take "$idr2" >/dev/null
p="$(dirname "$AGENT_MAILBOX")"
for i in 1 2 3 4 5 6; do
  ( printf 'race result %s\n' "$i" | AGENT_ROLE=dev-junior bash "$TASK" done "$idr2" >/dev/null 2>&1 && touch "$p/dwin_$i" ) &
done
wait
assert_eq "1" "$(ls "$p" | grep -c '^dwin_' || true)" "parallel done: exactly one winner"
assert_eq "1" "$(grep -c 'done by' "$AGENT_MAILBOX/tasks/$idr2/events" || true)" "single done event after race"
assert_eq "1" "$(wc -l < "$AGENT_MAILBOX/tasks/$idr2/result.md" | tr -d ' ')" "race result is a single intact line"
rm -f "$p"/dwin_*

# clean is delegator-only
rc=0; out=$(AGENT_ROLE=dev-mid bash "$TASK" clean 7 2>&1) || rc=$?
assert_eq "1" "$rc" "worker clean exits nonzero"
assert_contains "$out" "delegator" "worker clean message names the delegator"
rc=0; out=$(AGENT_ROLE= bash "$TASK" clean 7 2>&1) || rc=$?
assert_eq "1" "$rc" "roleless clean exits nonzero"

# clean prunes only done tasks (delegator)
mkdir -p "$AGENT_MAILBOX/tasks/T-9000"; echo "done" > "$AGENT_MAILBOX/tasks/T-9000/status"
touch -t 202001010000 "$AGENT_MAILBOX/tasks/T-9000"
out=$(AGENT_ROLE=delegator bash "$TASK" clean 7)
assert_eq "1" "$(echo "$out" | sed 's/pruned \([0-9]*\) .*/\1/')" "clean prunes the old done task"
assert_eq "0" "$([ -d "$AGENT_MAILBOX/tasks/T-9000" ] && echo 1 || echo 0)" "old done task removed"
assert_eq "1" "$([ -d "$AGENT_MAILBOX/tasks/T-0001" ] && echo 1 || echo 0)" "recent done task kept (mtime now)"

rm -rf "$(dirname "$AGENT_MAILBOX")"
finish_tests
