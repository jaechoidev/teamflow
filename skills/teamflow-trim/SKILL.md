---
name: teamflow-trim
description: Remove idle teamflow workers from the running session in the current git repository, keeping workers that still have tasks. Use when the user asks to trim, clear, or dismiss idle workers, or invokes $teamflow-trim.
---

# Trim idle teamflow workers

Use this skill only when the user asks to clear idle workers.

1. Work from the repository root. Check that `scripts/teamflow` exists.
2. Run `./scripts/teamflow list` and show the user the session's workers.
3. Run `./scripts/teamflow trim`. It removes every worker without an assigned
   or in-progress task. The Notetaker stays while it writes a note or
   released tasks wait for it. Removed workers keep their conversations and
   branches. A later `teamflow add <type>` reuses them before it creates a
   new worker, so their conversations resume.
4. If every worker was idle, `trim` stops the whole team like `--kill`. In
   that case, continue with steps 4 to 7 of the `teamflow-kill` skill to
   decide about kept worktrees.
5. Otherwise `trim` removes the worktrees of removed workers whose work is
   already merged, and lists the rest. Those hold work you may still review
   and integrate, so leave them unless the user asks to merge or discard one.
   Use `./scripts/teamflow worktrees merge <id>` or `discard <id>` then.
6. Report which workers were removed, which are still working, and any kept
   worktrees.
