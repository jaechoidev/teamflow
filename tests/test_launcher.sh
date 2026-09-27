#!/usr/bin/env bash
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/lib.sh"
TOOL="$(cd "$HERE/.." && pwd)"
command -v tmux >/dev/null 2>&1 || { echo "(tmux missing — skipped)"; finish_tests; exit 0; }

# Runtime stub dir: stub CLIs + a tmux wrapper pinned to an isolated server
# socket, so the spawned server inherits this shell's PATH (stub CLIs first)
# and never touches the user's real tmux server.
REAL_TMUX=$(which -a tmux | head -1)
STUBS="$(mktemp -d)"
cp "$HERE/stubs/claude" "$HERE/stubs/codex" "$HERE/stubs/zai-env.sh" "$STUBS/"
printf '#!/usr/bin/env bash\nexec %q -L ai-team-test "$@"\n' "$REAL_TMUX" > "$STUBS/tmux"
chmod +x "$STUBS/tmux"
export PATH="$STUBS:$PATH"

# --- fixture: a fresh target repo ---------------------------------------------
TGT="$(mktemp -d)/proj"
mkdir -p "$TGT" && cd "$TGT" && TGT="$(pwd -P)"   # physical path: pane cwds resolve /var -> /private/var
git init -q -b main
git config user.email t@t && git config user.name t
echo hello > file.txt && git add . && git commit -qm "initial"

# --- init ----------------------------------------------------------------------
bash "$TOOL/scripts/ai-team" init 2>/dev/null
# Fixture determinism: init copies this checkout's ai-team.conf, so a locally
# configured delegator effort would leak into the empty-effort case below.
sed -i '' '/^\[pane\.delegator\]/,/^\[pane\./ s|^effort *=.*|effort =|' ai-team.conf
for f in ai-team.conf scripts/ai-team .agents/AGENTS-SECTION.md \
         .agents/lib/task.sh .agents/lib/pane.sh \
         .agents/roles/delegator.md .agents/roles/dev-junior.md; do
  [ -f "$f" ] && _PASS=$((_PASS+1)) || { echo "FAIL: init missing $f"; _FAIL=$((_FAIL+1)); }
done
assert_contains "$(cat AGENTS.md)" "# >>> ai-team >>>" "init wrote marked AGENTS.md section"
assert_contains "$(cat .git/info/exclude)" ".ai-team-worktrees/" "init excluded worktrees"

# init outside a git repo: one clear diagnostic, nonzero exit, zero side effects
NONGIT="$(mktemp -d)"
out="$( cd "$NONGIT" && bash "$TOOL/scripts/ai-team" init 2>&1 )"; rc=$?
assert_eq "1" "$rc" "init outside git exits nonzero"
assert_contains "$out" "not inside a git repository" "clear diagnostic printed"
assert_eq "1" "$(printf '%s\n' "$out" | grep -c '^ai-team: ')" "exactly one diagnostic line"
assert_eq "0" "$(printf '%s' "$out" | grep -c 'initialized in' || true)" "no success line after failure"
assert_eq "" "$(ls -A "$NONGIT")" "no scaffolding left in target dir"
rm -rf "$NONGIT"

# up outside a git repo fails with the real diagnostic, not a misleading one
NONGIT="$(mktemp -d)"
out="$( cd "$NONGIT" && bash "$TOOL/scripts/ai-team" up 2>&1 )"; rc=$?
assert_eq "1" "$rc" "up outside git exits nonzero"
assert_contains "$out" "not inside a git repository" "up diagnostic names the real problem"
assert_eq "0" "$(printf '%s' "$out" | grep -c 'no ai-team.conf' || true)" "up not misreported as missing conf"
rm -rf "$NONGIT"

# up in a zero-commit repo: die in preflight before any side effect
UNBORN="$(mktemp -d)/proj"; mkdir -p "$UNBORN"
( cd "$UNBORN" && git init -q -b main \
  && git config user.email t@t && git config user.name t \
  && bash "$TOOL/scripts/ai-team" init >/dev/null 2>&1 \
  && sed -i '' "s|^zai_env = .*|zai_env = $STUBS/zai-env.sh|" ai-team.conf )
out="$( cd "$UNBORN" && bash "$TOOL/scripts/ai-team" up 2>&1 )"; rc=$?
assert_eq "1" "$rc" "up with zero commits exits nonzero"
assert_contains "$out" "no commits yet" "actionable diagnostic"
assert_eq "1" "$(printf '%s\n' "$out" | grep -c '^ai-team: ')" "exactly one diagnostic line"
assert_eq "0" "$(ls "$UNBORN" | grep -c 'ai-team-worktrees' || true)" "no worktrees created"
assert_eq "0" "$(tmux list-sessions 2>/dev/null | wc -l | tr -d ' ')" "no tmux session created"

# a valid first commit unlocks up (full run with stub CLIs)
( cd "$UNBORN" && echo hi > file.txt && git add . && git commit -qm initial )
SESS_U="$( cd "$UNBORN" && bash "$TOOL/scripts/ai-team" up --no-attach 2>/dev/null )"
assert_eq "1" "$([ -n "$SESS_U" ] && echo 1)" "up proceeds after first commit ($SESS_U)"
tmux kill-session -t "=$SESS_U" 2>/dev/null
rm -rf "$(dirname "$UNBORN")"

# init is idempotent: section appears once, conf untouched after user edits
echo "# my local note" >> ai-team.conf
bash "$TOOL/scripts/ai-team" init 2>/dev/null
assert_eq "1" "$(grep -c '# >>> ai-team >>>' AGENTS.md)" "section not duplicated on re-init"
assert_contains "$(cat ai-team.conf)" "# my local note" "user conf edits preserved"

# pre-existing AGENTS.md content survives a re-init
printf '# Project docs\n\nExisting instructions here.\n' > AGENTS.md
bash "$TOOL/scripts/ai-team" init 2>/dev/null
assert_contains "$(cat AGENTS.md)" "Existing instructions here." "pre-existing content kept"
assert_contains "$(cat AGENTS.md)" "# >>> ai-team >>>" "section re-appended after reset"

# re-init never clobbers user-edited role/lib files; differing shipped copies
# are staged as <file>.new so updates stay visible without losing local edits
echo "my custom rule" >> .agents/roles/dev-junior.md
bash "$TOOL/scripts/ai-team" init 2>/dev/null
assert_contains "$(cat .agents/roles/dev-junior.md)" "my custom rule" "user role edits survive re-init"
[ -f .agents/roles/dev-junior.md.new ] && _PASS=$((_PASS+1)) || { echo "FAIL: shipped role copy staged as .new"; _FAIL=$((_FAIL+1)); }
cmp -s "$TOOL/.agents/roles/dev-junior.md" .agents/roles/dev-junior.md.new \
  && _PASS=$((_PASS+1)) || { echo "FAIL: .new is not the shipped copy"; _FAIL=$((_FAIL+1)); }
mv .agents/roles/dev-junior.md.new .agents/roles/dev-junior.md
bash "$TOOL/scripts/ai-team" init 2>/dev/null
[ ! -e .agents/roles/dev-junior.md.new ] && _PASS=$((_PASS+1)) || { echo "FAIL: .new recreated for identical file"; _FAIL=$((_FAIL+1)); }
echo "# local patch" >> .agents/lib/pane.sh
bash "$TOOL/scripts/ai-team" init 2>/dev/null
assert_contains "$(cat .agents/lib/pane.sh)" "# local patch" "user lib edits survive re-init"
mv .agents/lib/pane.sh.new .agents/lib/pane.sh

# --- up with stub CLIs ---------------------------------------------------------
sed -i '' "s|^zai_env = .*|zai_env = $STUBS/zai-env.sh|" ai-team.conf
export STUB_LOG="$TGT/stub.log"; : > "$STUB_LOG"
# isolated CLI state dirs: stubs emulate transcript/session storage here, so
# the suite never touches the real ~/.claude or ~/.codex
export CLAUDE_CONFIG_DIR="$STUBS/claude-config"
export CODEX_HOME="$STUBS/codex-home"

# inherited z.ai/Anthropic routing vars must not reach claude panes (neither
# via the shell nor via tmux-global inheritance); zai panes must see exactly
# their env file's vars, nothing inherited
export ANTHROPIC_BASE_URL="https://polluted.example/api"
export ANTHROPIC_AUTH_TOKEN="polluted-secret"
export ANTHROPIC_MODEL="polluted-model"
SESS=$(bash "$TGT/scripts/ai-team" up --no-attach 2>/dev/null)
unset ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN ANTHROPIC_MODEL
assert_eq "1" "$([ -n "$SESS" ] && echo 1)" "up printed a session name ($SESS)"

sleep 1  # let panes boot stubs
assert_eq "6" "$(tmux list-panes -t "=$SESS" -F '#{pane_id}' | wc -l | tr -d ' ')" "six panes created"

# geometry: reading order by (top,left): delegator researcher / reviewer dev-senior / dev-mid dev-junior
GEO=$(tmux list-panes -t "=$SESS" -F '#{pane_top} #{pane_left} #{pane_title}' | sort -k1,1n -k2,2n | sed 's/^[0-9]* [0-9]* //' | tr '\n' ' ')
assert_contains "$GEO" "Delegator (codex) Researcher" "row1 left-to-right"
assert_contains "$GEO" "Planner (claude fable) Dev Senior" "row2 order"
assert_contains "$GEO" "Dev Mid (claude opus) Dev Junior" "row3 order"

# stubs saw the right env + cwds
LOG=$(cat "$STUB_LOG")
assert_eq "0" "$(grep -c 'model_reasoning_effort' "$STUB_LOG" || true)" "empty codex effort adds no -c override"
for role in delegator researcher reviewer dev-senior dev-mid dev-junior; do
  assert_contains "$LOG" "AGENT_ROLE=$role" "stub env for $role"
done
assert_contains "$LOG" "cwd=$TGT/.ai-team-worktrees/dev-senior" "dev-senior cwd is its worktree"
assert_contains "$LOG" "cwd=$TGT/.ai-team-worktrees/dev-junior" "dev-junior cwd is its worktree"
assert_contains "$LOG" "cwd=$TGT" "a main-checkout pane runs in repo root"
assert_contains "$LOG" "--model glm-5.3" "zai panes pass the GLM model"
assert_contains "$LOG" "--append-system-prompt-file" "claude panes get the role file"

# routing isolation: claude panes native (routing vars unset), zai panes get
# exactly their env file
for role in reviewer dev-senior dev-mid; do
  R=$(grep -A6 "AGENT_ROLE=$role " "$STUB_LOG" | grep '^ROUTING ' | tail -1)
  assert_contains "$R" "BASE_URL=unset" "$role: no inherited endpoint"
done
for role in researcher dev-junior; do
  Z=$(grep -A6 "AGENT_ROLE=$role " "$STUB_LOG" | grep '^ROUTING ' | tail -1)
  assert_contains "$Z" "BASE_URL=https://zai.stub/api" "$role: zai env file sourced"
  assert_contains "$Z" "MODEL=stub-glm" "$role: model from env file"
done
assert_eq "0" "$(grep -c polluted "$STUB_LOG")" "no pane sees shell-exported routing vars"
# the server-global environment must be left exactly as found: unrelated
# sessions on this tmux server may depend on it (T-0022). Token values are
# never printed, only their presence.
tmux show-environment -g ANTHROPIC_BASE_URL >/dev/null 2>&1 \
  && _PASS=$((_PASS+1)) || { echo "FAIL: tmux global env lost ANTHROPIC_BASE_URL"; _FAIL=$((_FAIL+1)); }
[ "$(tmux show-environment -g ANTHROPIC_BASE_URL 2>/dev/null)" = "ANTHROPIC_BASE_URL=https://polluted.example/api" ] \
  && _PASS=$((_PASS+1)) || { echo "FAIL: tmux global ANTHROPIC_BASE_URL value changed"; _FAIL=$((_FAIL+1)); }
tmux show-environment -g ANTHROPIC_AUTH_TOKEN >/dev/null 2>&1 \
  && _PASS=$((_PASS+1)) || { echo "FAIL: tmux global env lost ANTHROPIC_AUTH_TOKEN"; _FAIL=$((_FAIL+1)); }
# our own session environment is cleaned for inspection; the in-pane unset is
# what actually guards claude
tmux show-environment -t "$SESS" ANTHROPIC_AUTH_TOKEN >/dev/null 2>&1 \
  && { echo "FAIL: session env still carries ANTHROPIC_AUTH_TOKEN"; _FAIL=$((_FAIL+1)); } \
  || _PASS=$((_PASS+1))

# delegator codex pane launches in Full access mode
assert_contains "$LOG" "--dangerously-bypass-approvals-and-sandbox" "codex pane runs Full access"

# conversation persistence: a fresh start requests and stores per-role ids
MS="$TGT/.git/ai-team/sessions"
[ -d "$MS" ] && _PASS=$((_PASS+1)) || { echo "FAIL: sessions registry missing"; _FAIL=$((_FAIL+1)); }
SID_R=$(sed -n 's/^SESSION_ID=//p' "$MS/researcher" 2>/dev/null)
SID_V=$(sed -n 's/^SESSION_ID=//p' "$MS/reviewer" 2>/dev/null)
SID_D=$(sed -n 's/^SESSION_ID=//p' "$MS/delegator" 2>/dev/null)
UUID_RE='^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
echo "$SID_R" | grep -qE "$UUID_RE" && _PASS=$((_PASS+1)) || { echo "FAIL: researcher session id stored [$SID_R]"; _FAIL=$((_FAIL+1)); }
echo "$SID_V" | grep -qE "$UUID_RE" && _PASS=$((_PASS+1)) || { echo "FAIL: reviewer session id stored [$SID_V]"; _FAIL=$((_FAIL+1)); }
echo "$SID_D" | grep -qE "$UUID_RE" && _PASS=$((_PASS+1)) || { echo "FAIL: codex session id discovered [$SID_D]"; _FAIL=$((_FAIL+1)); }
[ "$SID_R" != "$SID_V" ] && _PASS=$((_PASS+1)) || { echo "FAIL: researcher/reviewer share a session id"; _FAIL=$((_FAIL+1)); }
[ -f "$CLAUDE_CONFIG_DIR/projects/stub-project/$SID_R.jsonl" ] \
  && _PASS=$((_PASS+1)) || { echo "FAIL: researcher transcript not created"; _FAIL=$((_FAIL+1)); }
[ -f "$CLAUDE_CONFIG_DIR/projects/stub-project/$SID_V.jsonl" ] \
  && _PASS=$((_PASS+1)) || { echo "FAIL: reviewer transcript not created"; _FAIL=$((_FAIL+1)); }
assert_contains "$LOG" "--session-id" "fresh claude panes request a session id"
assert_contains "$LOG" "--session-id $SID_R" "researcher requested its stored id"
grep -q "token\|SECRET\|AUTH_TOKEN=" "$MS/researcher" && { echo "FAIL: secrets in registry"; _FAIL=$((_FAIL+1)); } \
  || _PASS=$((_PASS+1))

# worktrees + branches
for role in dev-senior dev-mid dev-junior; do
  [ -d "$TGT/.ai-team-worktrees/$role" ] && _PASS=$((_PASS+1)) || { echo "FAIL: worktree $role"; _FAIL=$((_FAIL+1)); }
  git show-ref --verify --quiet "refs/heads/ai-team/$role" && _PASS=$((_PASS+1)) || { echo "FAIL: branch $role"; _FAIL=$((_FAIL+1)); }
done

# mailbox wired
MB="$TGT/.git/ai-team"
assert_eq "6" "$(wc -l < "$MB/panes.tsv" | tr -d ' ')" "panes.tsv has 6 roles"

# reattach does not duplicate
bash "$TGT/scripts/ai-team" up --no-attach >/dev/null 2>&1
assert_eq "6" "$(tmux list-panes -t "=$SESS" -F '#{pane_id}' | wc -l | tr -d ' ')" "reattach keeps six panes"

# verify mode runs and names every role
V=$(bash "$TGT/scripts/ai-team" --verify 2>/dev/null)
for role in delegator researcher reviewer dev-senior dev-mid dev-junior; do
  assert_contains "$V" "$role" "verify lists $role"
done

# verify reads ack timestamps from JSON ack records (T-0003 format) and
# tolerates legacy text records without crashing
mkdir -p "$MB/acks"
printf '{"role": "researcher", "time": "2026-09-27T09:00:00Z"}\n' > "$MB/acks/researcher.json"
printf 'role: researcher\ntime: 2026-09-27T08:00:00Z\n' > "$MB/acks/dev-mid.json"
V=$(bash "$TGT/scripts/ai-team" --verify 2>/dev/null)
assert_contains "$V" "2026-09-27T09:00:00Z" "verify shows JSON ack timestamp"
assert_contains "$V" "dev-mid" "verify tolerates legacy ack record"
rm -f "$MB/acks/researcher.json" "$MB/acks/dev-mid.json"

# a task dispatched through the real mailbox round-trips
export AGENT_MAILBOX="$MB"
TID=$(echo "please test the thing" | bash "$TGT/.agents/lib/task.sh" new dev-junior "test task")
assert_eq "assigned" "$(bash "$TGT/.agents/lib/task.sh" status "$TID")" "task dispatched via mailbox"

# pane.sh delivers through the isolated server too
bash "$TGT/.agents/lib/pane.sh" send-to reviewer "hello reviewer"
assert_contains "$(bash "$TGT/.agents/lib/pane.sh" tail reviewer 20)" "hello reviewer" "send-to lands in worker pane"

# --check-models: zai panes probe through their env file, claude panes clean
LOG_LINES=$(wc -l < "$STUB_LOG" | tr -d ' ')
CM=$(bash "$TGT/scripts/ai-team" --check-models 2>/dev/null)
assert_contains "$CM" "not probed" "codex model not probed"
echo "$CM" | grep -Eq '^reviewer +claude +fable +OK$' \
  && _PASS=$((_PASS+1)) || { echo "FAIL: check-models reviewer"; echo "$CM"; _FAIL=$((_FAIL+1)); }
echo "$CM" | grep -Eq '^researcher +zai +glm-5\.3 +OK$' \
  && _PASS=$((_PASS+1)) || { echo "FAIL: check-models researcher (zai env sourced)"; echo "$CM"; _FAIL=$((_FAIL+1)); }
DELTA=$(tail -n +$((LOG_LINES + 1)) "$STUB_LOG")
assert_contains "$DELTA" "BASE_URL=https://zai.stub/api" "zai probe ran with env file sourced"
assert_eq "0" "$(printf '%s' "$DELTA" | grep -c polluted)" "claude probes ran with clean env"

# --check-models fails clearly when a zai env file is missing
cp ai-team.conf ai-team.conf.keep
sed -i '' "s|^zai_env = .*|zai_env = $STUBS/absent-env.sh|" ai-team.conf
CMRC=0; bash "$TGT/scripts/ai-team" --check-models >/dev/null 2>cm.err || CMRC=$?
assert_eq "1" "$CMRC" "check-models fails on missing zai env"
assert_contains "$(cat cm.err)" "env file not found" "check-models names the missing env file"
mv ai-team.conf.keep ai-team.conf; rm -f cm.err

# kill removes session but leaves worktrees
bash "$TGT/scripts/ai-team" --kill >/dev/null 2>&1
tmux has-session -t "=$SESS" 2>/dev/null && { echo "FAIL: session survived kill"; _FAIL=$((_FAIL+1)); } || _PASS=$((_PASS+1))
[ -d "$TGT/.ai-team-worktrees/dev-mid" ] && _PASS=$((_PASS+1)) || { echo "FAIL: worktree removed"; _FAIL=$((_FAIL+1)); }

# kill/relaunch: every role resumes its stored conversation id
# (delegator now carries a configured codex effort: medium per T-0042)
sed -i '' 's|^effort =$|effort = medium|' ai-team.conf
: > "$STUB_LOG"
SESS2=$(bash "$TGT/scripts/ai-team" up --no-attach 2>/dev/null)
sleep 1
assert_eq "$SESS" "$SESS2" "relaunch reuses the deterministic session name"
assert_eq "1" "$(tmux list-sessions -F '#{session_name}' 2>/dev/null | grep -c .)" "exactly one tmux session"
LOG2=$(cat "$STUB_LOG")
assert_contains "$LOG2" "--resume $SID_R" "researcher resumes its stored id"
assert_contains "$LOG2" "--resume $SID_V" "reviewer resumes its stored id"
assert_contains "$LOG2" "resume $SID_D" "codex resumes by explicit id"
assert_eq "0" "$(grep -c -- '--session-id' "$STUB_LOG")" "relaunch issues no fresh ids"
assert_contains "$LOG2" "--model glm-5.3" "resume re-passes the model"
assert_contains "$LOG2" "--append-system-prompt-file" "resume re-passes the role file"
assert_contains "$LOG2" "--dangerously-bypass-approvals-and-sandbox" "codex resume keeps Full access"
assert_contains "$LOG2" "-c model_reasoning_effort=medium" "codex resume honors configured effort"

# fresh codex boot with effort configured: same -c override, before the prompt
bash "$TGT/scripts/ai-team" --kill >/dev/null 2>&1
rm -f "$MS/delegator"
: > "$STUB_LOG"
SESS2B=$(bash "$TGT/scripts/ai-team" up --no-attach 2>/dev/null)
sleep 1
assert_eq "$SESS" "$SESS2B" "session name stays deterministic across effort change"
assert_eq "1" "$(grep -c '^=== codex --dangerously-bypass-approvals-and-sandbox -m .* -c model_reasoning_effort=medium' "$STUB_LOG" || true)" "fresh codex passes configured effort"

# missing transcript: that role rolls over to a fresh id, others unaffected
bash "$TGT/scripts/ai-team" --kill >/dev/null 2>&1
rm -f "$CLAUDE_CONFIG_DIR/projects/stub-project/$SID_R.jsonl"
: > "$STUB_LOG"
SESS3=$(bash "$TGT/scripts/ai-team" up --no-attach 2>/dev/null)
sleep 1
LOG3=$(cat "$STUB_LOG")
SID_R2=$(sed -n 's/^SESSION_ID=//p' "$MS/researcher" 2>/dev/null)
[ "$SID_R2" != "$SID_R" ] && _PASS=$((_PASS+1)) || { echo "FAIL: researcher id not rolled over"; _FAIL=$((_FAIL+1)); }
echo "$SID_R2" | grep -qE "$UUID_RE" && _PASS=$((_PASS+1)) || { echo "FAIL: rolled-over id invalid [$SID_R2]"; _FAIL=$((_FAIL+1)); }
assert_contains "$LOG3" "--session-id $SID_R2" "researcher starts fresh after transcript loss"
assert_contains "$LOG3" "--resume $SID_V" "reviewer unaffected by researcher rollover"
bash "$TGT/scripts/ai-team" --kill >/dev/null 2>&1

# codex discovery must bind to this workspace: a concurrent foreign codex
# session whose index entry is NEWER but whose rollout cwd differs must lose
bash "$TGT/scripts/ai-team" --kill >/dev/null 2>&1
rm -f "$MS/delegator"
export STUB_FOREIGN_CWD=/tmp/foreign-project
: > "$STUB_LOG"
SESS4=$(bash "$TGT/scripts/ai-team" up --no-attach 2>/dev/null)
unset STUB_FOREIGN_CWD
sleep 1
FOREIGN_ID=$(sed -n 's/^FOREIGN_ID=//p' "$STUB_LOG" | head -1)
SID_D2=$(sed -n 's/^SESSION_ID=//p' "$MS/delegator" 2>/dev/null)
echo "$FOREIGN_ID" | grep -qE "$UUID_RE" \
  && _PASS=$((_PASS+1)) || { echo "FAIL: foreign session not simulated [$FOREIGN_ID]"; _FAIL=$((_FAIL+1)); }
[ "$SID_D2" != "$FOREIGN_ID" ] \
  && _PASS=$((_PASS+1)) || { echo "FAIL: foreign codex id stored for delegator"; _FAIL=$((_FAIL+1)); }
echo "$SID_D2" | grep -qE "$UUID_RE" \
  && _PASS=$((_PASS+1)) || { echo "FAIL: delegator id missing after race [$SID_D2]"; _FAIL=$((_FAIL+1)); }
grep -q "\"$SID_D2\"" "$CODEX_HOME/session_index.jsonl" \
  && _PASS=$((_PASS+1)) || { echo "FAIL: stored delegator id not in codex index"; _FAIL=$((_FAIL+1)); }
grep -q "\"cwd\": \"$TGT\"" "$CODEX_HOME/sessions/stub/rollout-$SID_D2.jsonl" 2>/dev/null \
  && _PASS=$((_PASS+1)) || { echo "FAIL: stored id rollout cwd is not this workspace"; _FAIL=$((_FAIL+1)); }
bash "$TGT/scripts/ai-team" --kill >/dev/null 2>&1

# --verify backfill: pending registry + late-booting workspace session, with a
# NEWER foreign session in the index, must backfill the workspace id only
bash "$TGT/scripts/ai-team" --kill >/dev/null 2>&1
rm -f "$MS/delegator"
UFRC=0; STUB_FAIL=1 bash "$TGT/scripts/ai-team" up --no-attach >/dev/null 2>/dev/null || UFRC=$?
SID_D3=$(sed -n 's/^SESSION_ID=//p' "$MS/delegator" 2>/dev/null)
[ -z "$SID_D3" ] \
  && _PASS=$((_PASS+1)) || { echo "FAIL: pending registry got an id [$SID_D3]"; _FAIL=$((_FAIL+1)); }
W=$(python3 -c 'import uuid; print(uuid.uuid4())')
FR=$(python3 -c 'import uuid; print(uuid.uuid4())')
printf '{"type": "session_meta", "payload": {"id": "%s", "cwd": "%s"}}\n' "$W" "$TGT" \
  > "$CODEX_HOME/sessions/stub/rollout-$W.jsonl"
printf '{"id": "%s", "thread_name": "stub"}\n' "$W" >> "$CODEX_HOME/session_index.jsonl"
printf '{"type": "session_meta", "payload": {"id": "%s", "cwd": "/tmp/other"}}\n' "$FR" \
  > "$CODEX_HOME/sessions/stub/rollout-$FR.jsonl"
printf '{"id": "%s", "thread_name": "stub"}\n' "$FR" >> "$CODEX_HOME/session_index.jsonl"
bash "$TGT/scripts/ai-team" --verify >/dev/null 2>&1
SID_D3=$(sed -n 's/^SESSION_ID=//p' "$MS/delegator" 2>/dev/null)
assert_eq "$W" "$SID_D3" "verify backfills the workspace codex id, not the newer foreign one"
bash "$TGT/scripts/ai-team" --kill >/dev/null 2>&1

# a pane command that dies instantly gets a real diagnosis, not "this is a bug"
export STUB_FAIL=1
UFRC=0; bash "$TGT/scripts/ai-team" up --no-attach >/dev/null 2>boot.err || UFRC=$?
unset STUB_FAIL
assert_eq "1" "$UFRC" "instant pane death fails the launch"
assert_contains "$(cat boot.err)" "exited immediately" "instant failure names the cause"
rm -f boot.err

# non-git dir diagnostic
cd "$(mktemp -d)" && bash "$TOOL/scripts/ai-team" up 2>/dev/null && rc=0 || rc=1
assert_eq "1" "$rc" "up outside a git repo fails with diagnostic"

tmux kill-server 2>/dev/null
rm -rf "$TGT" "$STUBS"
finish_tests
