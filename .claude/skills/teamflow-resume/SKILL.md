---
name: teamflow-resume
description: Bring back the teamflow workers of the last session for the current project, with their conversations. Use when the user asks to resume previously started workers, including after closing their tmux session. Requires an already initialized project.
---

# Resume teamflow workers

Work from the target project folder. Check that `git rev-parse --show-toplevel`
and `git rev-parse --verify HEAD` succeed and that `scripts/teamflow` and
`.agents/lib/delegator.sh` exist at the repository root. If any check fails,
stop and explain that this project has not completed teamflow setup. The first
run in a new folder uses the shell command `teamflow init` from the tool home.
Do not run `init` through this skill.

Read `AGENTS.md` and `.agents/roles/delegator.md`. From the repository root,
run `./scripts/teamflow start --last`. It starts the workers of the last
session, whether they came from the default team or were added on demand,
and resumes their saved conversations when available. Add `--config <file>`
when the user named a config. If there was no earlier session, relay the
message and offer the `teamflow` skill instead. It does not reopen the user's
separate Delegator chat.

If workers are already running, `start` returns the existing tmux session.
Report the printed session name and check `./scripts/teamflow list`. If
`start` printed "The last team stopped at ...", relay it, and ask the user
what to do with each unfinished task it names: resend, reassign, or drop. Inspect
workers with `bash .agents/lib/delegator.sh pane tail <id>` before
dispatching tasks, and handle any project trust prompts for this authorized
workspace. Then continue as the Delegator with the `teamflow` skill.

Do not stop or replace an active session unless the user asks. If `start`
reports a config or mode conflict, relay that result without killing it.
