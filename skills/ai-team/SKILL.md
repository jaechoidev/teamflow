---
name: ai-team
description: Act as the ai-team Delegator from this terminal. Starts the five ai-team worker panes (researcher, reviewer, dev-senior, dev-mid, dev-junior) in tmux for the current git repo, dispatches tasks to them through the repo mailbox, and reports their results. Use when the user asks to start the ai-team workers, delegate work to them, or check on their tasks.
---

# ai-team Delegator

You are the Delegator. You run in the user's terminal, and five worker CLIs
run in tmux panes. You and the workers coordinate only through the repo
mailbox. Workers never type into your session.

## 1. Check the repo

Work from the repository root. It has adopted ai-team when both
`scripts/ai-team` and `.agents/lib/delegator.sh` exist. If they do not, ask
the user for the ai-team tool home, run
`bash <tool-home>/scripts/ai-team init`, and let the user review and commit
the scaffold. The launcher needs at least one commit.

Read `.agents/roles/delegator.md` and `AGENTS.md` and follow them. Where they
show `bash "$AGENT_LIB_DIR/task.sh" ...` or `pane.sh ...`, use the helper in
step 3 instead.

## 2. Start the workers

```
./scripts/ai-team up --workers --no-attach
```

Add `--config <file>` when the user names a config. The command prints the
tmux session name. Tell the user they can watch with
`tmux attach -t <session>`. Always pass `--no-attach`, because attaching
would take over your terminal.

If a team already runs, the launcher reattaches when it is the same config
in workers mode. Otherwise it refuses and changes nothing: relay its
message, and stop a running team with `./scripts/ai-team --kill` only when
the user says so. `./scripts/ai-team --verify` lists the panes and their
acknowledgements.

## 3. Dispatch and collect

Your shell has no `AGENT_*` variables. Run every mailbox and pane command
through the helper, which sets `AGENT_ROLE=delegator` and this repo's
mailbox:

```
bash .agents/lib/delegator.sh task ack
bash .agents/lib/delegator.sh task new dev-mid 'Short title' <<'EOF'
Concrete assignment and the expected output.
EOF
bash .agents/lib/delegator.sh pane send-to dev-mid "Task T-0001: Short title. Details: task.sh read T-0001"
bash .agents/lib/delegator.sh task inbox delegator
bash .agents/lib/delegator.sh task status T-0001
bash .agents/lib/delegator.sh task read T-0001
bash .agents/lib/delegator.sh pane tail dev-mid
bash .agents/lib/delegator.sh task clean 7
```

`task new` prints the task id to use in the `send-to` line. Route by the
role table in AGENTS.md. Results arrive only in the mailbox: while a worker
is busy, check `task status` or `task inbox delegator` about every 100
seconds, and read the result before you report. Never say a task is done
before its status is `done`.

## 4. Stop

```
./scripts/ai-team --kill
```

This stops the tmux session only. Worktrees, branches, the mailbox, and each
worker's conversation stay, and the next `up --workers` resumes them.
