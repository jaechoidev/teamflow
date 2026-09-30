---
name: teamflow-kill
description: Stop the running teamflow tmux workspace for the current git repository when the user asks to close its workers or invokes $teamflow-kill. Cleans up worker worktrees and asks the user whether to merge or discard unmerged work.
---

# Stop teamflow workers

Use this skill only when the user explicitly asks to stop the team. When the
Delegator's agent session ends, the team also stops by itself about a minute
later, once its workers are idle, but that stop cannot ask about unmerged
worktrees. It keeps them, and this skill decides about them later.

1. Find the current repository root with `git rev-parse --show-toplevel` and
   work from there. Check that `teamflow.conf` exists.
2. Run `teamflow --verify` to identify the running session. If it is
   already down, for example stopped by the watcher after the Delegator left,
   say so and continue with step 4 to clean up worktrees.
3. Run `teamflow --kill`. With no `--config`, the launcher targets the
   active team for this repository, even if it used a custom config. Do not
   kill the tmux server or unrelated sessions.
4. `--kill` removes each stopped worker's worktree and branch when all of its
   work is already in the main checkout's branch. It lists the worktrees it
   kept. `teamflow worktrees list` shows them again.
5. For each kept worktree, show the user what it holds:
   `git log --oneline HEAD..teamflow/<id>` for commits and
   `git -C .teamflow-worktrees/<id> status --short` for uncommitted files.
   Also mention any related task that is done but not yet integrated. Then ask
   the user to choose for each worktree: merge into the current branch, or
   discard. Do not choose for them, and do not finish this skill before every
   kept worktree has an answer.
6. Apply each answer:
   - merge: `teamflow worktrees merge <id>`. It commits leftover
     changes, cherry-picks the unmerged commits, and removes the worktree.
     If it reports that the main checkout has uncommitted changes, or stops
     on a conflict, relay the message and ask the user how to proceed.
     It merges nothing in that case.
   - discard: `teamflow worktrees discard <id>`. This deletes the
     worktree, its branch, and its changes, and cannot be undone.
   Merging adds commits to the current branch. Never push.
7. Run `teamflow --verify` and `teamflow worktrees list`,
   then report whether the session is down and what was merged, discarded,
   or kept. The mailbox and worker conversations stay for the next start.
