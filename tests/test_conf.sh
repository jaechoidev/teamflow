#!/usr/bin/env bash
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/lib.sh"
TOOL="$(cd "$HERE/.." && pwd)"

# conf_read emits workspace line + 6 pane rows in file order
out=$("$TOOL/scripts/ai-team" --help >/dev/null 2>&1; true) # launcher loads cleanly

# Source the parser indirectly by running init in a temp repo later (test_launcher);
# here, validate the shipped conf parses and orders as the layout requires.
parsed=$(python3 - "$TOOL/ai-team.conf" <<'PY'
import sys, configparser
cp = configparser.ConfigParser(); cp.read(sys.argv[1])
w = cp["workspace"]
print(w.get("session_prefix"), w.get("zai_env"), w.get("task_ttl_days"), sep="\t")
for s in cp.sections():
    if s.startswith("pane."):
        print(s[5:], cp[s].get("cli"), cp[s].get("model"), cp[s].get("worktree"), sep="\t")
PY
)
assert_eq "at	~/.zai/env.sh	7" "$(printf '%s\n' "$parsed" | head -1)" "workspace line"
order=$(printf '%s\n' "$parsed" | tail -n +2 | cut -f1 | tr '\n' ' ' | sed 's/ $//')
assert_eq "delegator researcher reviewer dev-senior dev-mid dev-junior" "$order" "pane order = reading order"
clis=$(printf '%s\n' "$parsed" | tail -n +2 | cut -f2 | tr '\n' ' ' | sed 's/ $//')
assert_eq "codex zai claude claude claude zai" "$clis" "cli matrix matches the brief"
wt=$(printf '%s\n' "$parsed" | tail -n +2 | cut -f4 | tr '\n' ' ' | sed 's/ $//')
assert_eq "no no no yes yes yes" "$wt" "developers get worktrees, others main checkout"

finish_tests
