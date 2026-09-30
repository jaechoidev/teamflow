---
name: teamflow
description: Act as the teamflow Delegator from this terminal. Starts the worker panes and dedicated Notetaker in tmux for the current git repo, dispatches tasks through the repo mailbox, and reports their results. Use when the user asks to start teamflow workers, delegate work to them, or check on their tasks.
---

# teamflow Delegator

You are the Delegator. You run in the user's terminal, and worker CLIs
run in tmux panes. The Notetaker is the last configured pane, possibly in an overflow window. You and the workers coordinate only through the repo
mailbox. Workers never type into your session.

## 1. Check the repo

Work from the target project folder. A linked worktree of the project also
works: the launcher and the mailbox act on the main checkout's team. The
project has adopted teamflow when both
`scripts/teamflow` and `.agents/lib/delegator.sh` exist. If they do not, ask
the user for the teamflow tool home, then run
`bash <tool-home>/scripts/teamflow start`. This initializes Git and the
scaffold, makes the first scaffold commit if needed, and starts the workers.

Then check that the launcher knows workers mode:

```
./scripts/teamflow --help | grep -q "scripts/teamflow start"
```

If this fails, the project launcher is older and lacks `start`. Use
`bash <tool-home>/scripts/teamflow start` from the project folder instead.
That run refreshes the project's unmodified teamflow files, and later
`./scripts/teamflow` calls hand off to the tool home. Report any `.new` files
it stages for locally edited copies.

Read `.agents/roles/delegator.md` and `AGENTS.md` and follow them.
Run `./scripts/teamflow workers list` to see configured instance IDs.
In workers mode, you are the Delegator outside tmux. The configured worker panes and Notetaker
use as many windows as the roster needs. Where the
role instructions show `bash "$AGENT_LIB_DIR/task.sh" ...` or `pane.sh ...`,
use the helper in step 3 instead.

## 2. Start the workers

If step 1 started the team, use its printed session name. Otherwise run:

```
./scripts/teamflow start
```

Project notes belong to the Notetaker. When it is in the team, starting
creates `docs/notes/`. Never write notes yourself or create note tasks.

To start a smaller team, `./scripts/teamflow start --only <type-or-id>`
launches just that worker, for example when the user wants only the
Notetaker to catch up on waiting notes. Offer this only when the user asks
for one worker, and add others later with the `teamflow-workers` skill.

Add `--config <file>` when the user names a config. The command prints the
tmux session name. Tell the user they can watch with
`tmux attach -t <session>`. `start` stays detached so it does not take over your terminal.

If giving detach keys, check `tmux show-options -g prefix` and the
`detach-client` binding in `tmux list-keys -T prefix` first. User tmux
configurations can change the default `Ctrl-B` prefix.

If a team already runs, `start` returns the same session when it uses the
same config in workers mode. It names any default workers missing from the
session, and the `teamflow-workers` skill can add them. Otherwise it refuses and changes nothing: relay its
message, and stop a running team with `./scripts/teamflow --kill` only when
the user says so. `./scripts/teamflow --verify` lists the panes and their
acknowledgements.

Before the first dispatch, inspect each worker with
`bash .agents/lib/delegator.sh pane tail <role>`. Fresh Claude Code worktrees
may show a project trust prompt. Sending a task while that prompt is open can
select `No, exit`, leaving the task assigned and the pane dead. For a workspace
the user has authorized, select the displayed trust option in tmux, then check
that the CLI is ready before sending the task. If a pane already died, restart
it with `tmux respawn-pane -t <pane-id>`, handle the prompt, and resend the
existing task ID. `ack=no` alone does not mean a worker is unready: workers
acknowledge when they act on their first message.

## 3. Dispatch and collect

Your shell has no `AGENT_*` variables. Run every mailbox and pane command
through the helper, which sets `AGENT_ROLE=delegator` and this repo's
mailbox:

```
bash .agents/lib/delegator.sh task ack
bash .agents/lib/delegator.sh task new developer-l-1 'Short title' <<'EOF'
Concrete assignment and the expected output.
EOF
bash .agents/lib/delegator.sh pane send-to developer-l-1 "Task T-0001: Short title. Details: task.sh read T-0001"
bash .agents/lib/delegator.sh task inbox delegator
bash .agents/lib/delegator.sh task status T-0001
bash .agents/lib/delegator.sh task read T-0001
bash .agents/lib/delegator.sh pane tail developer-l-1
bash .agents/lib/delegator.sh task release T-0001 abc1234
cat .git/teamflow/note-queue/completed/T-0001.md
```

`task new` prints the task id to use in the `send-to` line. Route to a
configured instance by type and availability. Results arrive only in the mailbox: while a worker
is busy, check `task status` or `task inbox delegator` about every 100
seconds, and read the result before you report. Never say a task is done
before its status is `done`.

After review and integration, release every completed task with
`task release`. With a Notetaker in the team, the mailbox result starts its
investigation, and it checks code, discussion, tests, decisions, and sources
for the relevant note types. Read its completion summary before reporting the
slice. The next task is delivered only after it finishes the current note
pass. Check the Notetaker's pane for a trust prompt before releasing the
first task. Without a Notetaker, `release` says the task waits for one.
Report the slice without notes.

## 4. Stop

When the user asks to stop the team, follow the `teamflow-kill` skill. It
stops the tmux session, removes worktrees whose work is already merged, and
asks the user to merge or discard the rest. The mailbox and each worker's
conversation stay, and the next `start` resumes them. For an explicit resume
request, use the `teamflow-resume` skill.
