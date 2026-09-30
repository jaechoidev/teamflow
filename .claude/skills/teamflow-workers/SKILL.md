---
name: teamflow-workers
description: List, add, or remove teamflow worker instances in the current project. Use when the user asks to change the team roster or inspect configured workers, not for assigning tasks to workers.
---

# Manage teamflow workers

Work from the target project's Git repository or any linked worktree of it. Use `./scripts/teamflow workers` for roster changes. A named config uses `--config <file>`, with paths resolved from the repository root.

```
./scripts/teamflow workers list
./scripts/teamflow workers add <type> [--save]
./scripts/teamflow workers remove <instance-id> [--save]
./scripts/teamflow workers sync
```

Types define CLI, model, effort, worktree policy, and role instructions in `teamflow.conf`. Each numbered instance has its own mailbox destination, conversation, and optional worktree. Numbers are never reused. The Notetaker is a singleton.

While a team runs, add and remove change this session only. Adding launches the pane. Removing closes it and refuses a worker with unfinished tasks. `teamflow.conf` stays unchanged, and the next start begins from it again. Add `--save` only when the user wants the change kept for later starts. When the team is stopped, add and remove edit `teamflow.conf`. `workers sync` makes the running panes match the session roster again, for example after a pane died. Changing an existing worker's model, effort, CLI, or role file requires a restart. Do not remove branches or worktrees as part of removal.

After adding a worker, run `bash .agents/lib/delegator.sh pane tail <instance-id>` before giving it a task. A new worktree can open with a Claude Code project trust prompt, and a task sent into that prompt can select `No, exit`. For a workspace the user has authorized, select the displayed trust option in tmux, then check that the CLI is ready.

After a change, run `workers list` and report the instance IDs, including any marked `session only`. If the user also asked to start or resume workers, follow the `teamflow` or `teamflow-resume` skill.
