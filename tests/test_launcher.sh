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

# --- up with stub CLIs ---------------------------------------------------------
sed -i '' "s|^zai_env = .*|zai_env = $STUBS/zai-env.sh|" ai-team.conf
export STUB_LOG="$TGT/stub.log"; : > "$STUB_LOG"
SESS=$(bash "$TGT/scripts/ai-team" up --no-attach 2>/dev/null)
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

# kill removes session but leaves worktrees
bash "$TGT/scripts/ai-team" --kill >/dev/null 2>&1
tmux has-session -t "=$SESS" 2>/dev/null && { echo "FAIL: session survived kill"; _FAIL=$((_FAIL+1)); } || _PASS=$((_PASS+1))
[ -d "$TGT/.ai-team-worktrees/dev-mid" ] && _PASS=$((_PASS+1)) || { echo "FAIL: worktree removed"; _FAIL=$((_FAIL+1)); }

# non-git dir diagnostic
cd "$(mktemp -d)" && bash "$TOOL/scripts/ai-team" up 2>/dev/null && rc=0 || rc=1
assert_eq "1" "$rc" "up outside a git repo fails with diagnostic"

tmux kill-server 2>/dev/null
rm -rf "$TGT" "$STUBS"
finish_tests
