# teamflow tool home

This repo is the home of the `teamflow` tool: a portable tmux workspace launcher. See README.md for usage. Other projects adopt it with `teamflow init`.

# >>> ai-team >>>
# Managed by `teamflow init` — edit roles in .agents/roles/, coordination here.

## AI team workspace

In workers mode, the Delegator runs in the user's Codex terminal outside
tmux. Configured workers and the optional Notetaker share one tmux window.
The Notetaker is last when present.

The table shows the default configuration. `teamflow.conf` controls which
worker roles run and their pane order.

| Location | Role ID | CLI | Default work |
| --- | --- | --- | --- |
| External terminal | `delegator` | Codex | plan with user; dispatch + summarize |
| Workers window, pane 1 | `researcher` | z.ai GLM | external research, citations, options |
| Workers window, pane 2 | `reviewer` | Claude | review plans, architecture, and code |
| Workers window, pane 3 | `dev-senior` | Claude | complex/perf/architecture-sensitive code |
| Workers window, pane 4 | `dev-mid` | Claude | ordinary features/fixes/tests |
| Workers window, pane 5 | `dev-junior` | z.ai GLM | bounded tasks, tests, docs |
| Workers window, pane 6 | `notetaker` | Claude | investigate evidence and maintain project notes |

Rules:
- Workers act only when the user or the delegator assigns a task. No
  self-started work; no startup chatter.
- All coordination goes through the mailbox (`$AGENT_MAILBOX`, under
  `.git/ai-team/`): `bash "$AGENT_LIB_DIR/task.sh" new|take|done|read|status|inbox|ack`.
- Results are read from task records, never assumed from pane text.
- Nothing is ever typed into the Delegator session: `pane.sh send-to delegator`
  is rejected; completions surface via `task.sh inbox delegator`.
- `done` keeps results readable. After review and integration, the Delegator
  runs `task.sh release <id> [commit]`. The Notetaker investigates one
  released task at a time, then runs `task.sh noted <id> <summary>` to remove
  that task record and receive the next one.
- Developers work in separate worktrees (`.ai-team-worktrees/<role>`,
  branches `ai-team/<role>`). Avoid overlapping concurrent edits; coordinate scope.
- The Delegator integrates each task commit after Reviewer approval, or after
  its own review for a small, obvious change with local effects and a clear
  check. It inspects the full diff, verifies the change, and reports the
  evidence. Broader or unclear changes go to Reviewer. This review and
  integration flow has the user's approval; pushes still require approval.
- Task records are scratch. Starting teamflow authorizes project notes.
  Promote other durable decisions/docs into tracked files only with user
  approval.
- `teamflow start` creates `docs/notes/` and adopts project notes. They follow
  `.agents/doc-templates/README.md`. The Notetaker reads task results as
  triggers, then checks code, tests, discussions, decisions, and original
  sources for the relevant note types. The Delegator reads its note summary
  before reporting a slice. Other roles supply evidence in task results.
  Agents may propose memory questions. Only the user writes own-words
  explanations and answers.
- Keep task logs concise; never put secrets in them.
# <<< ai-team <<<
