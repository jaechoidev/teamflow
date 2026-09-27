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
for f in ai-team.conf scripts/ai-team .agents/AGENTS-SECTION.md \
         .agents/lib/task.sh .agents/lib/pane.sh \
         .agents/roles/delegator.md .agents/roles/dev-junior.md; do
  [ -f "$f" ] && _PASS=$((_PASS+1)) || { echo "FAIL: init missing $f"; _FAIL=$((_FAIL+1)); }
done
assert_contains "$(cat AGENTS.md)" "# >>> ai-team >>>" "init wrote marked AGENTS.md section"
assert_contains "$(cat .git/info/exclude)" ".ai-team-worktrees/" "init excluded worktrees"

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
tmux show-environment -g ANTHROPIC_BASE_URL >/dev/null 2>&1 \
  && { echo "FAIL: tmux global env still carries ANTHROPIC_BASE_URL"; _FAIL=$((_FAIL+1)); } \
  || _PASS=$((_PASS+1))
tmux show-environment -t "$SESS" ANTHROPIC_AUTH_TOKEN >/dev/null 2>&1 \
  && { echo "FAIL: session env still carries ANTHROPIC_AUTH_TOKEN"; _FAIL=$((_FAIL+1)); } \
  || _PASS=$((_PASS+1))

# delegator codex pane launches in Full access mode
assert_contains "$LOG" "--dangerously-bypass-approvals-and-sandbox" "codex pane runs Full access"

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
