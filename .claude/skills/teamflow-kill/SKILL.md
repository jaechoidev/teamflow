---
name: teamflow-kill
description: Stop the running teamflow tmux workspace for the current git repository when the user asks to close its workers or invokes $teamflow-kill.
---

# Stop teamflow workers

Use this skill only when the user explicitly asks to stop the team. Closing the
current Codex session does not stop the tmux workers.

1. Find the current repository root with `git rev-parse --show-toplevel` and
   work from there. Check that `scripts/teamflow` exists.
2. Run `./scripts/teamflow --verify` to identify the running session. If it is
   already down, report that and stop.
3. Run `./scripts/teamflow --kill`. With no `--config`, the launcher targets the
   active team for this repository, even if it used a custom config. Do not
   kill the tmux server or unrelated sessions.
4. Run `./scripts/teamflow --verify` again and report whether the session is
   down. The launcher leaves worktrees, branches, and the mailbox in place.
