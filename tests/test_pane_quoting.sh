#!/usr/bin/env bash
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/lib.sh"
TOOL="$(cd "$HERE/.." && pwd)"
PANE="$TOOL/.agents/lib/pane.sh"
command -v tmux >/dev/null 2>&1 || { echo "(tmux missing — skipped)"; finish_tests; exit 0; }

# Hermetic: nothing inherited from an ai-team pane, and a tmux server of our
# own: pane.sh and its capture/send must never touch the user's real server.
unset TMUX TMUX_PANE AGENT_ROLE AGENT_ID AGENT_ROLE_FILE AGENT_LIB_DIR
REAL_TMUX=$(which -a tmux | head -1)
STUBS="$(mktemp -d)"
printf '#!/usr/bin/env bash\nexec %q -L ai-team-test-quote "$@"\n' "$REAL_TMUX" > "$STUBS/tmux"
chmod +x "$STUBS/tmux"
export PATH="$STUBS:$PATH"
tmux kill-server 2>/dev/null

SESS="at-quote-$$"
MAILBOX="$(mktemp -d)/ai-team"
export AGENT_MAILBOX="$MAILBOX"
mkdir -p "$MAILBOX"

# one pane running `cat` so everything typed is echoed by the tty
tmux new-session -d -s "$SESS" -x 200 -y 10 "cat"
sleep 0.3
PID=$(tmux list-panes -t "=$SESS" -F '#{pane_id}' | head -1)
printf 'echo\t%s\n' "$PID" > "$MAILBOX/panes.tsv"

# quoting gauntlet: spaces, single+double quotes, dollar, backslash, glob, multiline
TEXT='fix the "login bug" — see $HOME and `pwd` & *.md files'
bash "$PANE" send-to echo "$TEXT"
sleep 0.5
LINE2="second line with 'single quotes' and | pipe"
bash "$PANE" send-to echo "$LINE2"
sleep 0.5

CAPTURE=$(tmux capture-pane -p -t "$PID" -S -30)
assert_contains "$CAPTURE" 'fix the "login bug"' "double quotes arrive intact"
assert_contains "$CAPTURE" '$HOME' "dollar literal arrives unexpanded"
assert_contains "$CAPTURE" '*.md files' "glob literal arrives unexpanded"
assert_contains "$CAPTURE" "second line with 'single quotes'" "single quotes arrive intact"

# sends.log records role + first line, concisely
assert_contains "$(cat "$MAILBOX/sends.log")" "-> echo : fix the" "delivery logged with first line"
assert_eq "2" "$(wc -l < "$MAILBOX/sends.log" | tr -d ' ')" "two sends logged"

# tail subcommand prints pane content
assert_contains "$(bash "$PANE" tail echo 10)" "second line" "tail shows pane tail"

# unknown role -> error
bash "$PANE" send-to nobody hi 2>/dev/null && rc=0 || rc=1
assert_eq "1" "$rc" "unknown role fails"

# delegator pane is never addressable, even when registered
printf 'delegator\t%s\n' "$PID" >> "$MAILBOX/panes.tsv"
rc=0; out=$(bash "$PANE" send-to delegator "sneaky ping" 2>&1) || rc=$?
assert_eq "1" "$rc" "send-to delegator rejected"
assert_contains "$out" "task.sh done" "rejection message points at the mailbox"
assert_eq "0" "$(grep -c 'sneaky ping' "$MAILBOX/sends.log" || true)" "rejected send not logged"
sleep 0.3
assert_eq "0" "$(tmux capture-pane -p -t "$PID" -S -40 | grep -c 'sneaky ping' || true)" "nothing typed into delegator pane"

# worker panes stay addressable after the guard (delegator dispatch path)
bash "$PANE" send-to echo "still reachable"
sleep 0.3
assert_contains "$(tmux capture-pane -p -t "$PID" -S -10)" "still reachable" "send-to worker still works"

tmux kill-server 2>/dev/null
rm -rf "$(dirname "$MAILBOX")" "$STUBS"
finish_tests
