---
name: teamflow-workers
description: List, add, or remove teamflow worker instances in the current project. Use when the user asks to change the team roster or inspect configured workers, not for assigning tasks to workers.
---

# Manage teamflow workers

Work from the target project's Git repository or any linked worktree of it. Use `./scripts/teamflow workers` for roster changes. A named config uses `--config <file>`, with paths resolved from the repository root.

```
./scripts/teamflow workers list
./scripts/teamflow workers add <type-or-id> [--save]
./scripts/teamflow workers remove <instance-id> [--save]
./scripts/teamflow workers sync
```

`teamflow.conf` is the default team. Types define CLI, model, effort, worktree policy, and role instructions. Each numbered instance has its own mailbox destination, conversation, and optional worktree. Numbers are never reused. The Notetaker is a singleton, and project notes exist only while it is in the team.

While a team runs, add and remove change this session only. Adding launches the pane. `add <type>` first brings back a default-team instance of that type that is missing from the session, then creates a new numbered one. `add <id>` brings back that default-team instance. Removing closes the pane and refuses a worker with unfinished tasks or a note in progress. `teamflow.conf` stays unchanged, and the next start begins from it again. Add `--save` only when the user asks to change the default team. `workers sync` makes the running panes match the session roster again, for example after a pane died. Changing an existing worker's model, effort, CLI, or role file requires a restart. Do not remove branches or worktrees as part of removal.

When no team is running, a plain add or remove refuses and explains why. Do not start anything on your own. Ask the user:

- to start the default team, through the `teamflow` skill. When the worker is already in the default team, this launches it.
- to start a team with only this worker: `./scripts/teamflow start --only <type-or-id>`. Then follow the `teamflow` skill as the Delegator.
- or, only if they want the default team changed, to rerun the command with `--save`.

After adding a worker, run `bash .agents/lib/delegator.sh pane tail <instance-id>` before giving it a task. A new worktree can open with a Claude Code project trust prompt, and a task sent into that prompt can select `No, exit`. For a workspace the user has authorized, select the displayed trust option in tmux, then check that the CLI is ready.

After a change, run `workers list` and report the instance IDs, including any marked `session only`. If the user also asked to start or resume workers, follow the `teamflow` or `teamflow-resume` skill.
