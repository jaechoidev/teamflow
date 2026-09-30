#!/usr/bin/env bash
# Workers mode (up --workers): five worker panes in one column and no
# Delegator pane, an external Delegator dispatching through
# .agents/lib/delegator.sh, verify/kill/resume, one team per repo across
# modes, and the portable skill. Stub CLIs, isolated tmux socket.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/lib.sh"
TOOL="$(cd "$HERE/.." && pwd)"

# --- the skill: plain frontmatter every CLI reads ---------------------------------
SK="$TOOL/skills/ai-team/SKILL.md"
fm="$(awk 'NR == 1 { if ($0 != "---") exit; next } $0 == "---" { exit } { print }' "$SK" 2>/dev/null)"
assert_eq "name description" "$(printf '%s\n' "$fm" | sed -n 's/^\([a-z_-]*\):.*/\1/p' | tr '\n' ' ' | sed 's/ $//')" "skill frontmatter holds only name and description"
assert_contains "$fm" "name: ai-team" "skill is named ai-team"
assert_contains "$(cat "$SK" 2>/dev/null)" "up --workers --no-attach" "skill starts the workers detached"
assert_contains "$(cat "$SK" 2>/dev/null)" "bash .agents/lib/delegator.sh task new" "skill dispatches through the helper"
# an older launcher would start or attach the full team on `up --workers`:
# the skill checks first, and the check passes on this launcher
assert_contains "$(cat "$SK" 2>/dev/null)" "./scripts/ai-team --help | grep -q -- --workers" "skill checks the launcher knows workers mode"
assert_contains "$(cat "$SK" 2>/dev/null)" "accept \`scripts/ai-team.new\`" "skill says how to get a launcher with workers mode"
bash "$TOOL/scripts/ai-team" --help 2>/dev/null | grep -q -- --workers; ok "the skill's launcher check passes here"
assert_contains "$(cat "$SK" 2>/dev/null)" "same Delegator outside tmux" "skill reconciles the full-team role wording"
assert_eq "0" "$(cat "$SK" "$TOOL/.agents/lib/delegator.sh" 2>/dev/null | grep -cF "$(printf '\342\200\224')" || true)" "no em dashes in the skill or helper"

command -v tmux >/dev/null 2>&1 || { echo "(tmux missing, skipped)"; finish_tests; exit 0; }

# Hermetic from here: nothing inherited from an ai-team pane, stub CLI state
# in scratch dirs before any launch, and a tmux server of our own.
unset TMUX TMUX_PANE AGENT_ROLE AGENT_ID AGENT_ROLE_FILE AGENT_MAILBOX AGENT_LIB_DIR
unset STUB_FAIL STUB_FOREIGN_CWD
REAL_TMUX=$(which -a tmux | head -1)
STUBS="$(mktemp -d)"
cp "$HERE/stubs/claude" "$HERE/stubs/codex" "$HERE/stubs/zai-env.sh" "$STUBS/"
printf '#!/usr/bin/env bash\nexec %q -L ai-team-test-workers "$@"\n' "$REAL_TMUX" > "$STUBS/tmux"
chmod +x "$STUBS/tmux"
export PATH="$STUBS:$PATH"
export STUB_LOG="$STUBS/stub.log"; : > "$STUB_LOG"
export CLAUDE_CONFIG_DIR="$STUBS/claude-config"
export CODEX_HOME="$STUBS/codex-home"
tmux kill-server 2>/dev/null

run() { out="$("$@" 2>&1)"; rc=$?; }   # combined output in $out, status in $rc
ok() { if [ $? -eq 0 ]; then _PASS=$((_PASS+1)); else _FAIL=$((_FAIL+1)); echo "FAIL: $1"; fi; }

write_conf() { # <file> <delegator cli, or none for no Delegator section>
  { printf '[workspace]\nsession_prefix = at\nzai_env = %s\ntask_ttl_days = 7\n' "$STUBS/zai-env.sh"
    [ "$2" = none ] || printf '\n[pane.delegator]\nlabel = Delegator\ncli = %s\nmodel = gpt-fixture\neffort = medium\nworktree = no\n' "$2"
    printf '\n[pane.researcher]\nlabel = Researcher\ncli = zai\nmodel = glm-5.3\neffort = max\nworktree = no\n'
    printf '\n[pane.reviewer]\nlabel = Reviewer\ncli = claude\nmodel = fable\neffort = max\nworktree = no\n'
    printf '\n[pane.dev-senior]\nlabel = Dev Senior\ncli = claude\nmodel = fable\neffort = max\nworktree = yes\n'
    printf '\n[pane.dev-mid]\nlabel = Dev Mid\ncli = claude\nmodel = opus\neffort = max\nworktree = yes\n'
    printf '\n[pane.dev-junior]\nlabel = Dev Junior\ncli = zai\nmodel = glm-5.3\neffort = max\nworktree = yes\n'
  } > "$1"
}

TGT="$(mktemp -d)/proj"
mkdir -p "$TGT" && cd "$TGT" && TGT="$(pwd -P)"
git init -q -b main && git config user.email t@t && git config user.name t
echo hello > file.txt && git add . && git commit -qm initial
AT="$TOOL/scripts/ai-team"
bash "$AT" init 2>/dev/null
mkdir -p variants
write_conf ai-team.conf codex
write_conf variants/workers.conf none
write_conf variants/bogus.conf bogus

MB="$TGT/.git/ai-team"
H="$TGT/.agents/lib/delegator.sh"
WORKERS="researcher reviewer dev-senior dev-mid dev-junior"
UUID_RE='^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
reg() { sed -n "s/^$2=//p" "$MB/sessions/$1" 2>/dev/null; }   # role key -> registry value
panes() { tmux list-panes -t "=$1" -F '#{pane_id}' 2>/dev/null | grep -c . || true; }
sessions() { tmux list-sessions -F '#{session_name}' 2>/dev/null | grep -c . || true; }
pane_line() { grep "^=== .*roles/$1\.md" "$STUB_LOG" | tail -1; }   # a role's CLI invocation
snapshot() { cat "$MB/panes.tsv" "$MB"/sessions/* 2>/dev/null; wc -l < "$STUB_LOG"; }

# --- argument parsing ----------------------------------------------------------------
run bash "$AT" --verify --workers
assert_eq "1" "$rc" "--workers with another mode fails"
assert_contains "$out" "--workers applies to up only" "misplaced --workers is named"
run bash "$AT" --help
assert_contains "$out" "--workers" "help documents --workers"

# --- five workers in one column, no Delegator ----------------------------------------
SESS="$(bash "$AT" up --workers --no-attach 2>"$STUBS/up.err")"; rc=$?
sleep 1
assert_eq "0" "$rc" "up --workers launches"
assert_eq "5" "$(panes "$SESS")" "five panes"
assert_eq "$WORKERS" "$(cut -f1 "$MB/panes.tsv" | tr '\n' ' ' | sed 's/ $//')" "pane registry lists the five workers in config order"
prev=-1 HS=""
for r in $WORKERS; do
  read -r L T W HT WW <<EOF
$(tmux display-message -p -t "$(awk -F '\t' -v r="$r" '$1 == r {print $2}' "$MB/panes.tsv" 2>/dev/null)" \
  '#{pane_left} #{pane_top} #{pane_width} #{pane_height} #{window_width}' 2>/dev/null)
EOF
  [ "$L" = 0 ] && [ -n "$W" ] && [ "$W" = "$WW" ]; ok "$r spans the full window width"
  [ -n "$T" ] && [ "$T" -gt "$prev" ]; ok "$r sits below the worker before it"
  prev="${T:-$prev}" HS="$HS $HT"
done
HMIN="$(printf '%s\n' $HS | sort -n | head -1)" HMAX="$(printf '%s\n' $HS | sort -n | tail -1)"
[ "$(printf '%s\n' $HS | grep -c .)" = 5 ] && [ $((HMAX - HMIN)) -le 1 ]; ok "equal heights ($HS)"
assert_eq "0" "$(grep -c '^=== codex' "$STUB_LOG" || true)" "no Delegator CLI starts"
assert_eq "0" "$(grep -c 'You are the team Delegator' "$STUB_LOG" || true)" "no startup message is sent"
for r in $WORKERS; do
  assert_eq "$TGT/.agents/roles/$r.md" "$(sed -n "s/^LASTARG\[$r\]=//p" "$STUB_LOG" | tail -1)" "$r boots with its role file only"
  reg "$r" SESSION_ID | grep -qE "$UUID_RE"; ok "$r conversation id recorded"
done
[ ! -e "$MB/sessions/delegator" ]; ok "no Delegator registry entry"
assert_eq "workers" "$(sed -n 's/^MODE=//p' "$MB/active")" "launch record names workers mode"
for r in dev-senior dev-mid dev-junior; do [ -d "$TGT/.ai-team-worktrees/$r" ]; ok "$r worktree"; done
assert_contains "$(cat "$STUBS/up.err")" "no Delegator pane" "startup log explains workers mode"

# --- the external Delegator: a plain terminal without AGENT_* variables --------------
TID="$(printf 'Fix the thing.\n' | bash "$H" task new dev-mid 'dispatch test')"
assert_contains "$(cat "$MB/tasks/$TID/task.md" 2>/dev/null)" "from:    delegator" "helper files tasks as the Delegator"
run bash "$H" pane send-to dev-mid "Task $TID: dispatch test"
assert_eq "0" "$rc" "helper delivers to a worker pane"
sleep 0.3
assert_contains "$(bash "$H" pane tail dev-mid 20)" "Task $TID: dispatch test" "the text lands in the dev-mid pane"
run bash "$H" pane send-to delegator "hello"
assert_eq "1" "$rc" "nothing is ever sent to the Delegator"
# the worker side, with the variables its pane carries
AGENT_ROLE=dev-mid AGENT_MAILBOX="$MB" bash "$TGT/.agents/lib/task.sh" take "$TID"
printf 'result: fixed\n' | AGENT_ROLE=dev-mid AGENT_MAILBOX="$MB" bash "$TGT/.agents/lib/task.sh" done "$TID"
# the helper finds the mailbox from its own location: another directory and
# a stray AGENT_MAILBOX change nothing
out="$( cd / && AGENT_MAILBOX=/nonexistent bash "$H" task inbox delegator 2>&1 )"
assert_contains "$out" "$TID  done" "the completed dispatch reaches the Delegator inbox from any directory"
assert_contains "$(bash "$H" task read "$TID")" "result: fixed" "the Delegator reads the result"
bash "$H" task ack >/dev/null
[ -f "$MB/acks/delegator.json" ]; ok "the external Delegator acknowledges"
run env AGENT_ROLE=dev-mid bash "$H" task clean 7
assert_eq "1" "$rc" "a worker pane cannot use the Delegator helper"
assert_contains "$out" "dev-mid" "the refusal names the role"
run bash "$H" task clean 7
assert_eq "0" "$rc" "the Delegator can clean"

V="$(bash "$AT" --verify 2>/dev/null)"
assert_contains "$V" "session: $SESS (up)" "verify reports the workers team up"
assert_contains "$V" "mode:    workers only" "verify names workers mode"
printf '%s\n' "$V" | grep -Eq '^delegator +external +ack=yes'; ok "verify shows the Delegator as external and acknowledged"
printf '%s\n' "$V" | grep -Eq '^dev-junior +pane %[0-9]+'; ok "verify shows the worker panes"

# --- one team per repo, and the mode counts ---------------------------------------------
BEFORE="$(snapshot)"
run bash "$AT" up --workers --no-attach
assert_eq "0" "$rc" "the running config in workers mode reattaches"
assert_eq "$SESS" "$(printf '%s\n' "$out" | tail -1)" "reattach reports the running session"
run bash "$AT" up --no-attach
assert_eq "1" "$rc" "the full team is refused while workers run"
assert_contains "$out" "running with another mode" "refusal names the mode as the difference"
assert_contains "$out" "$TGT/ai-team.conf (workers only)" "refusal tags the running team"
assert_contains "$out" "scripts/ai-team up --workers" "refusal says how to go back to the running team"
run bash "$AT" up --workers --no-attach --config variants/workers.conf
assert_eq "1" "$rc" "another config in workers mode is refused"
assert_eq "$BEFORE" "$(snapshot)" "reattach and refusals touch nothing and boot no CLI"
assert_eq "5" "$(panes "$SESS")" "the workers team keeps five panes"

# --- kill, then resume ------------------------------------------------------------------
for r in $WORKERS; do printf '%s %s\n' "$r" "$(reg "$r" SESSION_ID)"; done > "$STUBS/ids"
bash "$AT" --kill >/dev/null 2>&1
assert_eq "0" "$(sessions)" "--kill stops the workers team"
[ ! -e "$MB/active" ]; ok "--kill drops the launch record"
: > "$STUB_LOG"
assert_eq "$SESS" "$(bash "$AT" up --workers --no-attach 2>/dev/null)" "relaunch reuses the session name"
sleep 1
while read -r r id; do
  assert_contains "$(pane_line "$r")" "--resume $id" "$r resumes its conversation"
done < "$STUBS/ids"
assert_eq "0" "$(grep -c -- '--session-id' "$STUB_LOG" || true)" "relaunch starts no fresh conversation"
bash "$AT" --kill >/dev/null 2>&1

# --- the full team is unchanged, and conversations cross modes ----------------------
: > "$STUB_LOG"
SESS3="$(bash "$AT" up --no-attach 2>/dev/null)"
sleep 1
assert_eq "6" "$(panes "$SESS3")" "the full team still has six panes"
assert_eq "delegator $WORKERS" "$(cut -f1 "$MB/panes.tsv" | tr '\n' ' ' | sed 's/ $//')" "the full team registers the Delegator pane first"
assert_contains "$(grep '^=== codex' "$STUB_LOG" | tail -1)" "You are the team Delegator" "the full team's Delegator gets its handshake"
assert_contains "$(pane_line dev-mid)" "--resume $(sed -n 's/^dev-mid //p' "$STUBS/ids")" "workers keep their conversations in the full team"
run bash "$AT" up --workers --no-attach
assert_eq "1" "$rc" "workers mode is refused while the full team runs"
assert_contains "$out" "requested: $TGT/ai-team.conf (workers only)" "refusal names the requested mode"
bash "$AT" --kill >/dev/null 2>&1

# --- verify never binds the external Delegator to the pane registry -------------------
# An earlier full run can leave the Delegator pending: its codex id never
# discovered. In workers mode the user's own Codex session runs in this same
# directory, which is all codex discovery goes by, so --verify must leave
# the entry alone rather than record that session for the next full run.
bash "$AT" up --workers --no-attach >/dev/null 2>&1
sleep 1
printf 'ROLE=delegator\nCLI=codex\nSESSION_ID=\nCWD=%s\nMODEL=gpt-fixture\nSTATUS=pending\nSNAPSHOT=%s\n' \
  "$TGT" "$(wc -l < "$CODEX_HOME/session_index.jsonl" | tr -d ' ')" > "$MB/sessions/delegator"
EXT="$(python3 -c 'import uuid; print(uuid.uuid4())')"
mkdir -p "$CODEX_HOME/sessions/stub"
printf '{"type": "session_meta", "payload": {"id": "%s", "cwd": "%s"}}\n' "$EXT" "$TGT" \
  > "$CODEX_HOME/sessions/stub/rollout-$EXT.jsonl"
printf '{"id": "%s", "thread_name": "external"}\n' "$EXT" >> "$CODEX_HOME/session_index.jsonl"
bash "$AT" --verify >/dev/null 2>&1
assert_eq "" "$(reg delegator SESSION_ID)" "verify in workers mode never records an external Codex session"
assert_eq "pending" "$(reg delegator STATUS)" "the pending Delegator entry waits for the next full run"
bash "$AT" --kill >/dev/null 2>&1

# --- the Delegator section is optional and ignored in workers mode -------------------
run bash "$AT" up --workers --no-attach --config variants/workers.conf
assert_eq "0" "$rc" "a config without a Delegator section runs workers mode"
bash "$AT" --kill >/dev/null 2>&1
run bash "$AT" up --no-attach --config variants/workers.conf
assert_eq "1" "$rc" "the full team still needs six sections"
assert_contains "$out" "expected 6 [pane.*] sections" "the full team names the missing section count"
run bash "$AT" up --workers --no-attach --config variants/bogus.conf
assert_eq "0" "$rc" "workers mode never checks the Delegator section"
bash "$AT" --kill >/dev/null 2>&1
run bash "$AT" up --no-attach --config variants/bogus.conf
assert_eq "1" "$rc" "the full team still checks the Delegator section"
assert_contains "$out" "unknown cli 'bogus'" "the full team names the bad Delegator cli"
assert_eq "0" "$(sessions)" "no session is left behind"

tmux kill-server 2>/dev/null
rm -rf "$(dirname "$TGT")" "$STUBS"
finish_tests
