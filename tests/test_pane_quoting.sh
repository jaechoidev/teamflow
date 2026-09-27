#!/usr/bin/env bash
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/lib.sh"
TOOL="$(cd "$HERE/.." && pwd)"
PANE="$TOOL/.agents/lib/pane.sh"
command -v tmux >/dev/null 2>&1 || { echo "(tmux missing — skipped)"; finish_tests; exit 0; }

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

tmux kill-session -t "=$SESS" 2>/dev/null
rm -rf "$(dirname "$MAILBOX")"
finish_tests
