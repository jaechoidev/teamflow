---
name: teamflow-workers
description: List, add, or remove teamflow worker instances in the current project. Use when the user asks to change the team roster or inspect configured workers, not for assigning tasks to workers.
---

# Manage teamflow workers

Work from the target project's Git repository or any linked worktree of it. A named config uses `--config <file>`, with paths resolved from the repository root.

```
./scripts/teamflow types
./scripts/teamflow list
./scripts/teamflow add <type-or-id> [--save]
./scripts/teamflow remove <instance-id> [--save]
./scripts/teamflow sync
```

These are short for `./scripts/teamflow workers list|add|remove|sync`.

`teamflow.conf` holds the worker catalog and an optional default team. `types` lists the catalog: CLI, model, effort, worktree policy, and `use_for`, which says when to pick each type. Each numbered instance has its own mailbox destination, conversation, and optional worktree. Numbers are never reused. The Notetaker is a singleton, and project notes exist only while it is in the team.

While a team runs, add and remove change this session only. Each worker runs in its own tmux window, named by its ID. Adding opens the window. `add <type>` reuses a worker of that type that is not running before it creates a new number: first a default-team instance, then the most recently used one, including workers removed by trim or remove. A reused worker keeps its ID, conversation, and branch. A new number appears only when every known worker of that type is running. `add <id>` brings back that exact worker. Removing closes the window and refuses a worker with unfinished tasks or a note in progress. `teamflow.conf` stays unchanged, and the next start begins from it again. Add `--save` only when the user asks to change the default team. `sync` makes the running windows match the session roster again, for example after a worker died. Changing an existing worker's model, effort, CLI, or role file requires a restart. Do not remove branches or worktrees as part of removal.

When no team is running, `add` starts a team with just that worker, and later adds join it. A plain `remove` refuses, because there is no session to change. `--save` with no team running edits only the default team in `teamflow.conf` and starts nothing. To clear idle workers, use the `teamflow-trim` skill.

After adding a worker, run `bash .agents/lib/delegator.sh pane tail <instance-id>` before giving it a task. A new worktree can open with a Claude Code project trust prompt, and a task sent into that prompt can select `No, exit`. For a workspace the user has authorized, select the displayed trust option in tmux, then check that the CLI is ready.

After a change, run `list` and report the instance IDs, including any marked `session only`. If the user also asked to start or resume workers, follow the `teamflow` or `teamflow-resume` skill.
