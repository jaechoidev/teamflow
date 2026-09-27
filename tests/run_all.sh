#!/usr/bin/env bash
# Run the whole ai-team test suite (stub CLIs, isolated tmux socket — no model quota).
HERE="$(cd "$(dirname "$0")" && pwd)"
rc=0
for t in "$HERE"/test_*.sh; do
  echo "=== $(basename "$t") ==="
  bash "$t" || rc=1
done
exit $rc
