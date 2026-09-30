---
name: teamflow-workers
description: List, add, or remove teamflow worker instances in the current project. Use when the user asks to change the team roster or inspect configured workers, not for assigning tasks to workers.
---

# Manage teamflow workers

Work from the target project's Git repository. Use `./scripts/teamflow workers` for roster changes. A named config uses `--config <file>`, with paths resolved from the repository root.

```
./scripts/teamflow workers list
./scripts/teamflow workers add <type>
./scripts/teamflow workers remove <instance-id>
./scripts/teamflow workers sync
```

Types define CLI, model, effort, worktree policy, and role instructions in `teamflow.conf`. Each numbered instance has its own mailbox destination, conversation, and optional worktree. The Notetaker is a singleton. Add and remove update the config and apply the roster to a running team immediately. Adding launches its pane. Removing closes its pane and refuses unfinished tasks. When the team is stopped, changes apply at the next start. Use `workers sync` to apply roster edits made directly in the config. Changing settings of an existing running worker requires a team restart. Do not remove branches or worktrees as part of removal.

After a change, run `workers list` and report the instance IDs. If the user also asked to start or resume workers, follow the `teamflow` or `teamflow-resume` skill.
