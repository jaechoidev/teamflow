#!/usr/bin/env bash
# Doc templates: shared frontmatter convention, placeholders, concept
# learning sections, and prose style. No tmux needed.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/lib.sh"
DT="$(cd "$HERE/.." && pwd)/.agents/doc-templates"

assert_eq "README.md code-map.md concept.md decision.md experiment.md source.md" \
  "$(ls "$DT" 2>/dev/null | LC_ALL=C sort | tr '\n' ' ' | sed 's/ $//')" "guide plus five templates"

SHARED="type status created reviewed_by reviewed_on reviewed_commit superseded_by code_paths"
for t in source concept code-map decision experiment; do
  f="$DT/$t.md"
  fm="$(awk 'NR == 1 { if ($0 != "---") exit; next } $0 == "---" { exit } { print }' "$f" 2>/dev/null)"
  keys="$(printf '%s\n' "$fm" | sed -n 's/^\([a-z_]*\):.*/\1/p' | head -8 | tr '\n' ' ' | sed 's/ $//')"
  assert_eq "$SHARED" "$keys" "$t: shared frontmatter keys, in order"
  assert_contains "$fm" "type: $t" "$t: type matches file name"
  assert_contains "$fm" "status: draft" "$t: new notes start as draft"
  # the README's sed command fills every placeholder
  left="$(sed -e 's/{{title}}/T/g' -e 's/{{date}}/2026-01-01/g' "$f" 2>/dev/null | grep -c '{{' || true)"
  assert_eq "0" "$left" "$t: only {{title}} and {{date}} placeholders"
done

# the learning part lives in concept notes
c="$(cat "$DT/concept.md" 2>/dev/null)"
assert_contains "$c" "## In my own words" "concept: own-words section"
assert_contains "$c" "## Memory questions" "concept: memory questions section"

g="$(cat "$DT/README.md" 2>/dev/null)"
for s in draft revised reviewed superseded; do
  assert_contains "$g" "\`$s\`" "README defines status $s"
done
assert_contains "$g" "docs/superpowers/specs/" "README separates notes from Superpowers specs"

EM="$(printf '\342\200\224')"; EN="$(printf '\342\200\223')"   # UTF-8 em and en dash
assert_eq "0" "$(cat "$DT"/*.md 2>/dev/null | grep -cF -e "$EM" -e "$EN" || true)" "no em or en dashes"

finish_tests
