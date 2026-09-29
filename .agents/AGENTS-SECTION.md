# >>> ai-team >>>
# Managed by `ai-team init` — edit roles in .agents/roles/, coordination here.

## AI team workspace

This repository runs a six-agent tmux workspace (`scripts/ai-team`).

| Pane | Role ID | CLI | Default work |
| --- | --- | --- | --- |
| left | `delegator` | Codex | user interface; dispatch + summarize |
| right-1 | `researcher` | z.ai GLM | external research, citations, options |
| right-2 | `reviewer` | Claude | plans, architecture, reviews |
| right-3 | `dev-senior` | Claude | complex/perf/architecture-sensitive code |
| right-4 | `dev-mid` | Claude | ordinary features/fixes/tests |
| right-5 | `dev-junior` | z.ai GLM | bounded tasks, tests, docs |

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
  branches `ai-team/<role>`). Avoid overlapping concurrent edits; coordinate scope.
- The Delegator integrates each task commit after Reviewer approval, or after
  its own review for a small, obvious change with local effects and a clear
  check. It inspects the full diff, verifies the change, and reports the
  evidence. Broader or unclear changes go to Reviewer. This review and
  integration flow has the user's approval; pushes still require approval.
- Task records are scratch. Promote durable decisions/docs into tracked
  files only with user approval.
- Project notes are opt-in. Once the user adopts them, they follow
  `.agents/doc-templates/README.md` (default folder `docs/notes/`). The
  Delegator keeps them current with each slice. Other roles supply evidence
  in task results and edit notes only when assigned. Agents may propose
  memory questions. Only the user writes own-words explanations and answers.
- Keep task logs concise; never put secrets in them.
# <<< ai-team <<<
