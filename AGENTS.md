# teamflow tool home

This repo is the home of the `teamflow` tool: a portable tmux workspace launcher. See README.md for usage. Other projects adopt it with `teamflow init`.

# >>> teamflow >>>
# Managed by `teamflow init` — edit roles in .agents/roles/, coordination here.

## AI team workspace

In workers mode, the Delegator runs in the user's own agent session outside
tmux. Each worker runs in its own tmux window, named by its instance ID.
Workers join on demand, or the configured default team starts together.

`teamflow.conf` holds the worker catalog (`[type.*]`, listed by
`scripts/teamflow types` with each type's `use_for`) and an optional default
team (`[worker.*]`). `scripts/teamflow list` shows the running roster.

| Worker type | Default work |
| --- | --- |
| `researcher` | external research, citations, options |
| `reviewer` | review plans, architecture, and code |
| `developer-l`, `developer-m`, `developer-s` | one Developer role with different CLI, model, and effort settings |
| `notetaker` | one per team, only when notes are wanted: investigate evidence and maintain project notes |

Rules:
- Workers act only when the user or the delegator assigns a task. No
  self-started work; no startup chatter.
- All coordination goes through the mailbox (`$AGENT_MAILBOX`, under
  `.git/teamflow/`): `bash "$AGENT_LIB_DIR/task.sh" new|take|done|read|status|inbox|ack`.
- Results are read from task records, never assumed from window text.
- Nothing is ever typed into the Delegator session: `pane.sh send-to delegator`
  is rejected; completions surface via `task.sh inbox delegator`.
- `done` keeps results readable. After review and integration, the Delegator
  runs `task.sh release <id> [commit]`. The Notetaker investigates one
  released task at a time, then runs `task.sh noted <id> <summary>` to remove
  that task record and receive the next one.
- Developers work in separate worktrees (`.teamflow-worktrees/<instance-id>`,
  branches `teamflow/<instance-id>`). Avoid overlapping concurrent edits; coordinate scope.
- The Delegator integrates each task commit after Reviewer approval, or after
  its own review for a small, obvious change with local effects and a clear
  check. It inspects the full diff, verifies the change, and reports the
  evidence. Broader or unclear changes go to Reviewer. This review and
  integration flow has the user's approval; pushes still require approval.
- Task records are scratch. A notetaker in the team authorizes project notes.
  Promote other durable decisions/docs into tracked files only with user
  approval.
- Project notes belong to the Notetaker alone. When one is in the team,
  teamflow creates `docs/notes/`. Notes follow
  `.agents/doc-templates/README.md`. The Notetaker reads task results as
  triggers, then checks code, tests, discussions, decisions, and original
  sources for the relevant note types. The Delegator reads its note summary
  before reporting a slice. Without a Notetaker there are no notes, and
  released tasks wait for one. Other roles supply evidence in task results.
  Agents may propose memory questions. Only the user writes own-words
  explanations and answers.
- In workers mode, the team stops itself 10 minutes after the Delegator's
  agent session ends, once in-progress tasks finish. Unmerged worktrees are
  kept.
- Keep task logs concise; never put secrets in them.
# <<< teamflow <<<
