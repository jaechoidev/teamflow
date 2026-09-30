---
name: teamflow-resume
description: Reopen the existing teamflow worker workspace for the current project. Use when the user asks to resume previously started workers, including after closing their tmux session. Requires an already initialized project.
---

# Resume teamflow workers

Work from the target project folder. Check that `git rev-parse --show-toplevel`
and `git rev-parse --verify HEAD` succeed and that `scripts/teamflow` and
`.agents/lib/delegator.sh` exist at the repository root. If any check fails,
stop and explain that this project has not completed teamflow setup. The first
run in a new folder uses the shell command `teamflow start` from the tool home.
Do not run `init` through this skill.

Read `AGENTS.md` and `.agents/roles/delegator.md`. From the repository root,
run `./scripts/teamflow start`. If this launcher predates `start`, use the
tool-home launcher only after confirming that this repository is already
initialized. Add `--config <file>` when the user named a config.

If workers are already running with the same config, `start` returns the
existing tmux session. If they were stopped, it recreates their panes and
resumes saved worker conversations when available. It does not reopen the
user's separate Delegator chat. Report the printed session name and check
`./scripts/teamflow --verify` for the pane layout. Inspect worker panes with
`bash .agents/lib/delegator.sh pane tail <role>` before dispatching tasks, and
handle any project trust prompts for this authorized workspace.

Do not stop or replace an active session unless the user asks. If `start`
reports a config or mode conflict, relay that result without killing it.
