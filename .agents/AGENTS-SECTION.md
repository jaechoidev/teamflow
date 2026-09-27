# >>> ai-team >>>
# Managed by `ai-team init` — edit roles in .agents/roles/, coordination here.

## AI team workspace

This repository runs a six-agent tmux workspace (`scripts/ai-team`).

| Pane | Role ID | CLI | Default work |
| --- | --- | --- | --- |
| left-top | `delegator` | Codex | user interface; dispatch + summarize |
| left-bottom | `researcher` | z.ai GLM | external research, citations, options |
| right-1 | `reviewer` | Claude | plans, architecture, reviews |
| right-2 | `dev-senior` | Claude | complex/perf/architecture-sensitive code |
| right-3 | `dev-mid` | Claude | ordinary features/fixes/tests |
| right-4 | `dev-junior` | z.ai GLM | bounded tasks, tests, docs |

Rules:
- Workers act only when the user or the delegator assigns a task. No
  self-started work; no startup chatter.
- All coordination goes through the mailbox (`$AGENT_MAILBOX`, under
  `.git/ai-team/`): `bash "$AGENT_LIB_DIR/task.sh" new|take|done|read|status|inbox|ack`.
- Results are read from task records, never assumed from pane text.
- Nothing is ever typed into the Delegator pane: `pane.sh send-to delegator`
  is rejected; completions surface via `task.sh inbox delegator`.
- Only the Delegator deletes task records (`task.sh clean`); `done` keeps
  results readable.
- Developers work in separate worktrees (`.ai-team-worktrees/<role>`,
  branches `ai-team/<role>`). No merges, commits-to-main, or pushes without
  the user's approval. Avoid overlapping concurrent edits; coordinate scope.
- Task records are scratch. Promote durable decisions/docs into tracked
  files only with user approval.
- Keep task logs concise; never put secrets in them.
# <<< ai-team <<<
