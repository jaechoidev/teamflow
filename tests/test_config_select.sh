#!/usr/bin/env bash
# Config selection (--config): argument parsing, path resolution from the
# repo root, one team at a time per repo, the per-CLI conversation registry,
# and the Claude Delegator startup handshake. Stub CLIs, isolated tmux socket.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/lib.sh"
TOOL="$(cd "$HERE/.." && pwd)"
command -v tmux >/dev/null 2>&1 || { echo "(tmux missing, skipped)"; finish_tests; exit 0; }

# Hermetic from the first line: nothing inherited from an ai-team pane, stub
# CLI state in scratch dirs before any launch, and a tmux server of our own.
unset TMUX TMUX_PANE AGENT_ROLE AGENT_ID AGENT_ROLE_FILE AGENT_MAILBOX AGENT_LIB_DIR
unset STUB_FAIL STUB_FOREIGN_CWD
REAL_TMUX=$(which -a tmux | head -1)
STUBS="$(mktemp -d)"
cp "$HERE/stubs/claude" "$HERE/stubs/codex" "$HERE/stubs/zai-env.sh" "$STUBS/"
printf '#!/usr/bin/env bash\nexec %q -L ai-team-test-config "$@"\n' "$REAL_TMUX" > "$STUBS/tmux"
chmod +x "$STUBS/tmux"
export PATH="$STUBS:$PATH"
export STUB_LOG="$STUBS/stub.log"; : > "$STUB_LOG"
export CLAUDE_CONFIG_DIR="$STUBS/claude-config"
export CODEX_HOME="$STUBS/codex-home"
tmux kill-server 2>/dev/null   # a leftover server would carry a stale environment

run() { out="$("$@" 2>&1)"; rc=$?; }   # combined output in $out, status in $rc
ok() { # $1 message; passes when the previous command succeeded
  if [ $? -eq 0 ]; then _PASS=$((_PASS+1)); else _FAIL=$((_FAIL+1)); echo "FAIL: $1"; fi
}

# write_conf <file> <session prefix> <delegator cli> <delegator model> <delegator label>
#            [reviewer cli, default claude] [reviewer model, default fable]
write_conf() {
  cat > "$1" <<EOF
[workspace]
session_prefix = $2
zai_env = $STUBS/zai-env.sh
task_ttl_days = 7

[pane.delegator]
label = $5
cli = $3
model = $4
effort = medium
worktree = no

[pane.researcher]
label = Researcher (zai glm-5.3)
cli = zai
model = glm-5.3
effort = max
worktree = no

[pane.reviewer]
label = Reviewer & Planner
cli = ${6:-claude}
model = ${7:-fable}
effort = max
worktree = no

[pane.dev-senior]
label = Dev Senior (claude fable)
cli = claude
model = fable
effort = max
worktree = yes

[pane.dev-mid]
label = Dev Mid (claude opus)
cli = claude
model = opus
effort = max
worktree = yes

[pane.dev-junior]
label = Dev Junior (zai glm-5.3)
cli = zai
model = glm-5.3
effort = max
worktree = yes
EOF
}

# --- fixture: an adopted repo with a default config and two variants -------------
TGT="$(mktemp -d)/proj"
mkdir -p "$TGT" && cd "$TGT" && TGT="$(pwd -P)"
git init -q -b main
git config user.email t@t && git config user.name t
echo hello > file.txt && git add . && git commit -qm "initial"
# The launcher under test runs from the tool home, so TOOL_HOME differs from
# the project and no fallback to tool-home files can mask a wrong lookup.
AT="$TOOL/scripts/ai-team"
bash "$AT" init 2>/dev/null
mkdir -p variants sub/dir
write_conf ai-team.conf at codex gpt-fixture "Delegator (codex)"
write_conf variants/claude.conf at claude fable "Delegator (claude fable)"
write_conf variants/alt-prefix.conf alt claude fable "Delegator (claude fable)"
write_conf variants/zai-reviewer.conf at codex gpt-fixture "Delegator (codex)" zai glm-5.3
write_conf variants/zai-delegator.conf at zai glm-5.3 "Delegator (zai glm-5.3)"
printf 'this is not an ini file\n' > variants/broken.conf

# --- argument parsing --------------------------------------------------------------
run bash "$AT" --check-models --config
assert_eq "1" "$rc" "--config without a value fails"
assert_contains "$out" "--config requires a file path" "missing value is named"

run bash "$AT" --config --check-models
assert_eq "1" "$rc" "--config followed by a flag fails"
assert_contains "$out" "--config requires a file path" "a flag is not taken as the path"

run bash "$AT" --check-models --config=
assert_eq "1" "$rc" "--config= with an empty value fails"

run bash "$AT" --check-models --config variants/claude.conf --config ai-team.conf
assert_eq "1" "$rc" "--config twice fails"
assert_contains "$out" "more than once" "duplicate --config is named"

run bash "$AT" --check-models --kill
assert_eq "1" "$rc" "two modes fail"
assert_contains "$out" "--check-models" "mode conflict names the first mode"
assert_contains "$out" "--kill" "mode conflict names the second mode"

run bash "$AT" --check-models --no-attach
assert_eq "1" "$rc" "--no-attach with a non-up mode fails"
assert_contains "$out" "--no-attach" "misplaced --no-attach is named"

run bash "$AT" up --bogus
assert_eq "1" "$rc" "unknown argument after up fails instead of launching"
assert_contains "$out" "unknown argument: --bogus" "unknown argument is named"

run bash "$AT" --help
assert_eq "0" "$rc" "--help succeeds"
assert_contains "$out" "--config <file>" "help documents --config"
assert_eq "0" "$(printf '%s' "$out" | grep -c 'Design notes' || true)" "help stops before the design notes"

# init takes no config: refuse before scaffolding anything
FRESH="$(mktemp -d)/fresh"; mkdir -p "$FRESH"
( cd "$FRESH" && git init -q -b main )
out="$( cd "$FRESH" && bash "$AT" init --config x.conf 2>&1 )"; rc=$?
assert_eq "1" "$rc" "init --config fails"
assert_contains "$out" "init" "init refusal names the mode"
assert_eq ".git" "$(ls -A "$FRESH" | tr '\n' ' ' | sed 's/ $//')" "refused init scaffolds nothing"
rm -rf "$(dirname "$FRESH")"

# --- config path resolution ----------------------------------------------------------
# default (no flag): the project's ai-team.conf, exactly as before
CM="$(bash "$AT" --check-models 2>/dev/null)"
printf '%s\n' "$CM" | grep -Eq '^delegator +codex +gpt-fixture +not probed'
ok "no flag probes the default config"

# relative paths resolve from the repo root, wherever the command runs
CM="$( cd "$TGT/sub/dir" && bash "$AT" --check-models --config variants/claude.conf 2>/dev/null )"
printf '%s\n' "$CM" | grep -Eq '^delegator +claude +fable +OK$'
ok "relative --config resolves from the repo root (run from a subdirectory)"

CM="$( cd "$TGT/sub/dir" && bash "$AT" --config=variants/claude.conf --check-models 2>/dev/null )"
printf '%s\n' "$CM" | grep -Eq '^delegator +claude +fable +OK$'
ok "--config=<file> form, flag before the mode"

CM="$( cd "$TGT/sub/dir" && bash "$AT" --check-models --config "$TGT/variants/claude.conf" 2>/dev/null )"
printf '%s\n' "$CM" | grep -Eq '^delegator +claude +fable +OK$'
ok "absolute --config path"

# a quoted or =-joined tilde never reaches the shell's own expansion
CM="$( HOME="$TGT/variants" bash "$AT" --check-models --config='~/claude.conf' 2>/dev/null )"
printf '%s\n' "$CM" | grep -Eq '^delegator +claude +fable +OK$'
ok "a leading ~/ resolves from the home directory"

# a cwd-relative spelling is not guessed at: the error names the resolved path
out="$( cd "$TGT/variants" && bash "$AT" --check-models --config claude.conf 2>&1 )"; rc=$?
assert_eq "1" "$rc" "cwd-relative spelling is refused"
assert_contains "$out" "config file not found: $TGT/claude.conf" "error names the path it resolved"
assert_contains "$out" "variants/claude.conf" "error points at the repo-root spelling"

run bash "$AT" --check-models --config variants/absent.conf
assert_eq "1" "$rc" "missing config fails"
assert_contains "$out" "config file not found: $TGT/variants/absent.conf" "missing config is named in full"

run bash "$AT" --check-models --config variants
assert_eq "1" "$rc" "a directory is not a config"

run bash "$AT" --check-models --config variants/broken.conf
assert_eq "1" "$rc" "unparsable config fails"
assert_contains "$out" "cannot parse $TGT/variants/broken.conf" "unparsable config is named"

# from a team worktree the same relative path names the same file: the
# variant exists only in the main checkout, so a worktree-relative lookup
# would not find it
git worktree add -q -b ai-team/dev-senior "$TGT/.ai-team-worktrees/dev-senior"
CM="$( cd "$TGT/.ai-team-worktrees/dev-senior" && bash "$AT" --check-models --config variants/claude.conf 2>/dev/null )"
printf '%s\n' "$CM" | grep -Eq '^delegator +claude +fable +OK$'
ok "relative --config from a team worktree resolves from the repo root"
CM="$( cd "$TGT/.ai-team-worktrees/dev-senior" && bash "$AT" --check-models 2>/dev/null )"
printf '%s\n' "$CM" | grep -Eq '^delegator +codex +gpt-fixture +not probed'
ok "default config from a team worktree is the repo root's"

# --- one team at a time ---------------------------------------------------------------
# The mailbox and the role worktrees belong to the repo, so a second config
# must never attach to, or launch beside, a team another config started.
MB="$TGT/.git/ai-team"
sessions() { tmux list-sessions -F '#{session_name}' 2>/dev/null | grep -c . || true; }
panes() { tmux list-panes -t "=$1" -F '#{pane_id}' 2>/dev/null | grep -c . || true; }
# everything a second launch would overwrite, plus proof that no CLI booted
snapshot() { cat "$MB/panes.tsv" "$MB"/sessions/* 2>/dev/null; wc -l < "$STUB_LOG"; }
WT="$TGT/.ai-team-worktrees/dev-senior"
UUID_RE='^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
reg() { sed -n "s/^$2=//p" "$MB/sessions/$1" 2>/dev/null; }   # role key -> registry value
# the CLI invocation of a role's pane, found by its role file: panes boot
# side by side, so neighbouring log lines may belong to another pane
pane_line() { grep "^=== .*roles/$1\.md" "$STUB_LOG" | tail -1; }
last_arg() { sed -n "s/^LASTARG\[$1\]=//p" "$STUB_LOG" | tail -1; }   # claude/zai panes

SESS="$(bash "$AT" up --no-attach 2>"$STUBS/up-default.err")"
sleep 1
assert_eq "1" "$([ -n "$SESS" ] && echo 1)" "default config launches ($SESS)"
assert_eq "6" "$(panes "$SESS")" "six panes under the default config"
SID_D="$(reg delegator SESSION_ID)"   # minted by codex
SID_V="$(reg reviewer SESSION_ID)"    # requested from claude
echo "$SID_D" | grep -qE "$UUID_RE"; ok "codex delegator id recorded [$SID_D]"
echo "$SID_V" | grep -qE "$UUID_RE"; ok "claude reviewer id recorded [$SID_V]"
BEFORE="$(snapshot)"

run bash "$AT" up --no-attach --config variants/claude.conf
assert_eq "1" "$rc" "another config is refused while a team runs"
assert_contains "$out" "$SESS" "refusal names the running session"
assert_contains "$out" "$TGT/ai-team.conf" "refusal names the running config"
assert_contains "$out" "$TGT/variants/claude.conf" "refusal names the requested config"
assert_contains "$out" "scripts/ai-team --kill" "refusal says how to stop the running team"
assert_eq "$BEFORE" "$(snapshot)" "refused launch leaves the mailbox untouched and boots no CLI"
assert_eq "1" "$(sessions)" "refused launch creates no second session"
assert_eq "6" "$(panes "$SESS")" "running team keeps its six panes"

# another session prefix means another session name: the running team must
# still be seen, because the mailbox is per repo and not per session name
run bash "$AT" up --no-attach --config variants/alt-prefix.conf
assert_eq "1" "$rc" "another config with another session prefix is refused"
assert_eq "$BEFORE" "$(snapshot)" "refused launch (other prefix) leaves the mailbox untouched"
assert_eq "1" "$(sessions)" "no second session under the other prefix"

# the config that started the team reattaches, named or not
run bash "$AT" up --no-attach --config ai-team.conf
assert_eq "0" "$rc" "the running config reattaches when named"
assert_contains "$out" "$SESS" "reattach reports the running session"
assert_eq "$BEFORE" "$(snapshot)" "reattach boots nothing"
assert_eq "6" "$(panes "$SESS")" "reattach adds no panes"

run bash "$AT" --verify --config variants/claude.conf
assert_eq "1" "$rc" "--verify with a config that is not running is refused"
assert_contains "$out" "$TGT/ai-team.conf" "verify refusal names the running config"
run bash "$AT" --kill --config variants/claude.conf
assert_eq "1" "$rc" "--kill with a config that is not running is refused"
assert_eq "1" "$(sessions)" "refused kill leaves the team running"

V="$(bash "$AT" --verify --config ai-team.conf 2>/dev/null)"
assert_contains "$V" "session: $SESS (up)" "verify with the running config reports it up"
assert_contains "$V" "$TGT/ai-team.conf" "verify names the config in effect"

# a team worktree is part of the team: same session, same refusal, and no
# second team rooted inside the worktree
out="$( cd "$WT" && bash "$AT" --verify 2>/dev/null )"
assert_contains "$out" "session: $SESS (up)" "verify from a team worktree finds the running team"
out="$( cd "$WT" && bash "$AT" up --no-attach --config variants/claude.conf 2>&1 )"; rc=$?
assert_eq "1" "$rc" "switching from a team worktree is refused too"
out="$( cd "$WT" && bash "$AT" up --no-attach 2>/dev/null )"; rc=$?
assert_eq "0" "$rc" "up from a team worktree succeeds"
assert_eq "$SESS" "$out" "up from a team worktree reattaches to the team"
assert_eq "1" "$(sessions)" "no second team from a team worktree"
[ ! -e "$WT/.ai-team-worktrees" ]; ok "no worktrees nested inside a team worktree"
assert_eq "$BEFORE" "$(snapshot)" "worktree invocations leave the mailbox untouched"

bash "$AT" --kill >/dev/null 2>&1
assert_eq "0" "$(sessions)" "plain --kill stops the running team"

# --- switching: stop, then start the other config -----------------------------------------
# A conversation id belongs to the CLI that minted it. Worst case for the
# launcher: an id of that name also resolves in the other CLI's store. Plant
# exactly that, so only the CLI recorded in the registry can tell them apart.
mkdir -p "$CLAUDE_CONFIG_DIR/projects/stub-project"
: > "$CLAUDE_CONFIG_DIR/projects/stub-project/$SID_D.jsonl"
: > "$STUB_LOG"
SESS_C="$(bash "$AT" up --no-attach --config variants/claude.conf 2>"$STUBS/up.err")"; rc=$?
sleep 1
assert_eq "0" "$rc" "the variant launches once the team is stopped"
assert_eq "6" "$(panes "$SESS_C")" "six panes under the variant"
D_LINE="$(pane_line delegator)"
SID_C="$(reg delegator SESSION_ID)"
assert_contains "$D_LINE" "=== claude " "the variant's delegator pane runs claude"
echo "$SID_C" | grep -qE "$UUID_RE"; ok "claude delegator id recorded [$SID_C]"
[ "$SID_C" != "$SID_D" ]; ok "the claude delegator does not take over the codex id"
assert_contains "$D_LINE" "--session-id $SID_C" "the claude delegator starts a fresh conversation"
assert_eq "0" "$(grep -c -- "$SID_D" "$STUB_LOG" || true)" "the codex id is never handed to claude"
assert_eq "claude" "$(reg delegator CLI)" "registry records the delegator's new CLI"
assert_contains "$(pane_line reviewer)" "--resume $SID_V" "a role whose CLI did not change keeps its conversation"

# --- startup handshake --------------------------------------------------------------------
# The Delegator is the user's interface: whatever CLI runs it, a fresh one
# acknowledges and reports READY by itself. Workers stay silent.
HELLO="$(last_arg delegator)"
case "$HELLO" in "You are the team Delegator."*) true ;; *) false ;; esac
ok "a fresh claude delegator gets a first message, as one final argument [$HELLO]"
assert_contains "$HELLO" "bash '$TGT/.agents/lib'/task.sh ack" "the first message asks for the role acknowledgement"
assert_contains "$HELLO" "AGENTS.md in '$TGT'" "the first message points at the project AGENTS.md"
case "$HELLO" in *"reply READY only.") true ;; *) false ;; esac
ok "the first message ends with the READY instruction"
assert_contains "$D_LINE" "--append-system-prompt-file $TGT/.agents/roles/delegator.md" "the claude delegator still gets its role file"
for role in researcher reviewer dev-senior dev-mid dev-junior; do
  assert_eq "$TGT/.agents/roles/$role.md" "$(last_arg "$role")" "worker $role gets its role file and no first message"
done

# the startup log describes the team that was started, not the default one
UPERR="$(cat "$STUBS/up.err")"
printf '%s\n' "$UPERR" | grep -Eq 'delegator = pane %[0-9]+ \(claude\)'
ok "startup log names the CLI the delegator runs"
assert_eq "0" "$(printf '%s\n' "$UPERR" | grep -c 'voice' || true)" "no Codex voice note for a claude delegator"
assert_contains "$UPERR" "config: $TGT/variants/claude.conf" "startup log names the config in effect"
UPDEF="$(cat "$STUBS/up-default.err")"
printf '%s\n' "$UPDEF" | grep -Eq 'delegator = pane %[0-9]+ \(codex\)'
ok "default startup log names codex"
assert_contains "$UPDEF" "the Codex CLI has no voice mode" "default startup log keeps the Codex voice note"
assert_contains "$UPDEF" "config: $TGT/ai-team.conf" "default startup log names the default config"

run bash "$AT" up --no-attach
assert_eq "1" "$rc" "plain up does not reattach to a team another config started"
assert_contains "$out" "$TGT/variants/claude.conf" "refusal names the running variant"
assert_contains "$out" "scripts/ai-team up --config variants/claude.conf" "refusal says how to reattach to the running team"
assert_eq "6" "$(panes "$SESS_C")" "refused plain up adds no panes"

# without --config, --verify and --kill follow the running team
V="$(bash "$AT" --verify 2>/dev/null)"
assert_contains "$V" "session: $SESS_C (up)" "plain verify follows the running team"
assert_contains "$V" "Delegator (claude fable)" "plain verify reads the running team's config"
assert_contains "$V" "$TGT/variants/claude.conf" "plain verify names the running team's config"
run bash "$AT" --verify --config ai-team.conf
assert_eq "1" "$rc" "--verify naming the default config is refused while the variant runs"
bash "$AT" --kill >/dev/null 2>&1
assert_eq "0" "$(sessions)" "plain --kill stops a team another config started"

# a team under another session prefix is found through its launch record
SESS_A="$(bash "$AT" up --no-attach --config variants/alt-prefix.conf 2>/dev/null)"
sleep 1
assert_contains "$SESS_A" "alt-proj-" "the variant's session prefix names the session"
V="$(bash "$AT" --verify 2>/dev/null)"
assert_contains "$V" "session: $SESS_A (up)" "plain verify finds a team under another prefix"
run bash "$AT" up --no-attach
assert_eq "1" "$rc" "plain up is refused while a team runs under another prefix"
assert_eq "1" "$(sessions)" "no second team beside the other prefix"
# the running team's config file goes away: stopping the team must not depend on it
mv variants/alt-prefix.conf variants/alt-prefix.conf.moved
run bash "$AT" --verify
assert_eq "1" "$rc" "verify cannot describe a team whose config is gone"
assert_contains "$out" "config file not found: $TGT/variants/alt-prefix.conf" "verify names the missing config"
assert_contains "$out" "$SESS_A" "verify names the session that was started with it"
bash "$AT" --kill >/dev/null 2>&1
assert_eq "0" "$(sessions)" "plain --kill stops a team under another prefix, config file or not"
mv variants/alt-prefix.conf.moved variants/alt-prefix.conf

# --- conversations survive a round trip between configs ---------------------------------------
: > "$STUB_LOG"
bash "$AT" up --no-attach >/dev/null 2>&1
sleep 1
assert_contains "$(cat "$STUB_LOG")" "=== codex resume $SID_D " "back on the default config, codex resumes its own conversation"
assert_eq "$SID_D" "$(reg delegator SESSION_ID)" "registry holds the codex id again"
assert_eq "codex" "$(reg delegator CLI)" "registry records codex again"
assert_eq "0" "$(grep -c -- "$SID_C" "$STUB_LOG" || true)" "the claude id is never handed to codex"
bash "$AT" --kill >/dev/null 2>&1

: > "$STUB_LOG"
bash "$AT" up --no-attach --config variants/claude.conf >/dev/null 2>&1
sleep 1
assert_contains "$(pane_line delegator)" "--resume $SID_C" "back on the variant, claude resumes its own conversation"
assert_eq "$TGT/.agents/roles/delegator.md" "$(last_arg delegator)" "a resumed delegator gets no second handshake"
assert_eq "0" "$(grep -c -- "$SID_D" "$STUB_LOG" || true)" "the codex id stays out of the variant"
bash "$AT" --kill >/dev/null 2>&1

# claude and z.ai share one transcript store, so a stored claude id always
# resolves there: a role moved to z.ai must still start its own conversation
: > "$STUB_LOG"
bash "$AT" up --no-attach --config variants/zai-reviewer.conf >/dev/null 2>&1
sleep 1
R_LINE="$(pane_line reviewer)"
SID_Z="$(reg reviewer SESSION_ID)"
echo "$SID_Z" | grep -qE "$UUID_RE"; ok "z.ai reviewer id recorded [$SID_Z]"
[ "$SID_Z" != "$SID_V" ]; ok "a reviewer moved to z.ai does not take over the claude conversation"
assert_contains "$R_LINE" "--session-id $SID_Z" "the z.ai reviewer starts a fresh conversation"
assert_eq "0" "$(grep -c -- "$SID_V" "$STUB_LOG" || true)" "the claude id is never resumed under z.ai"
assert_contains "$(cat "$STUB_LOG")" "=== codex resume $SID_D " "the delegator, unchanged in this variant, resumes"
bash "$AT" --kill >/dev/null 2>&1
: > "$STUB_LOG"
bash "$AT" up --no-attach >/dev/null 2>&1
sleep 1
assert_contains "$(pane_line reviewer)" "--resume $SID_V" "back on claude, the reviewer resumes its own conversation"
bash "$AT" --kill >/dev/null 2>&1

# a z.ai Delegator: its own conversation, the same first message, its env file
: > "$STUB_LOG"
bash "$AT" up --no-attach --config variants/zai-delegator.conf >/dev/null 2>&1
sleep 1
SID_ZD="$(reg delegator SESSION_ID)"
[ "$SID_ZD" != "$SID_C" ] && [ "$SID_ZD" != "$SID_D" ]; ok "a z.ai delegator takes over neither the claude nor the codex id"
assert_contains "$(pane_line delegator)" "--session-id $SID_ZD" "the z.ai delegator starts a fresh conversation"
case "$(last_arg delegator)" in "You are the team Delegator."*"reply READY only.") true ;; *) false ;; esac
ok "a fresh z.ai delegator gets the first message"
assert_contains "$(grep -A4 '^AGENT_ROLE=delegator ' "$STUB_LOG" | grep '^ROUTING ' | tail -1)" \
  "BASE_URL=https://zai.stub/api" "the z.ai delegator runs through its env file"
bash "$AT" --kill >/dev/null 2>&1
assert_eq "0" "$(sessions)" "round trips leave no session behind"

# --- launch records that no longer match reality ---------------------------------------------
# a session that died without --kill must not block the next config
SESS="$(bash "$AT" up --no-attach 2>/dev/null)"
tmux kill-session -t "=$SESS"
run bash "$AT" up --no-attach --config variants/claude.conf
assert_eq "0" "$rc" "a dead session's record does not block another config"
sleep 1.1
# the record vouches for one session only: a same-named session that
# replaced it (what an older launcher leaves behind) counts as unrecorded,
# and only the default config can have started it
tmux kill-session -t "=$SESS_C"
tmux new-session -d -s "$SESS_C" 'sleep 600'
run bash "$AT" up --no-attach --config variants/claude.conf
assert_eq "1" "$rc" "a record does not vouch for a session that replaced the recorded one"
run bash "$AT" up --no-attach
assert_eq "0" "$rc" "the replacing session reattaches under the default config"
bash "$AT" --kill >/dev/null 2>&1
assert_eq "0" "$(sessions)" "plain --kill stops an unrecorded session"

# a running session with no record at all (older launcher)
SESS="$(bash "$AT" up --no-attach 2>/dev/null)"
rm -f "$MB/active"
run bash "$AT" up --no-attach --config variants/claude.conf
assert_eq "1" "$rc" "an unrecorded session blocks another config"
assert_contains "$out" "$TGT/ai-team.conf" "an unrecorded session is attributed to the default config"
run bash "$AT" up --no-attach
assert_eq "0" "$rc" "an unrecorded session reattaches under the default config"
bash "$AT" --kill >/dev/null 2>&1
assert_eq "0" "$(sessions)" "kill stops the unrecorded session"

tmux kill-server 2>/dev/null
rm -rf "$(dirname "$TGT")" "$STUBS"
finish_tests
