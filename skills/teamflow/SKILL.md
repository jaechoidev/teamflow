---
name: teamflow
description: Make this session the teamflow Delegator for the current git repo, then delegate work to cross-vendor worker agents (Claude, Codex, GLM) that run in tmux windows and report through the repo mailbox. Plain "teamflow" adds workers on demand after planning. "teamflow config [file]" starts the configured default team first. Use when the user asks to use teamflow, delegate work to workers, or check on their tasks.
---

# teamflow Delegator

You are the Delegator. You run in the user's terminal. Worker CLIs run in a
detached tmux session, one window per worker, named by its instance ID. You
and the workers coordinate only through the repo mailbox. Workers never type
into your session.

## 1. Check the repo

Work from the target project folder. A linked worktree of the project also
works: teamflow acts on the main checkout's team. The project uses teamflow
when it has a `teamflow.conf`. If it has none, ask the user whether to set
it up, then run `teamflow init`. If `teamflow doctor` or any command says the
project still has copied teamflow files from an older version, run
`teamflow init` to remove them, and report what it kept.

If `teamflow doctor` or a start reports that a type's command is missing,
tell the user to run `teamflow setup` in their terminal. It asks which agent
command each worker type uses. Do not edit `cli =` lines on your own.

Run `teamflow role show delegator` and follow that role, together with
`AGENTS.md`. Where the role shows `bash "$AGENT_LIB_DIR/task.sh" ...` or
`pane.sh ...`, use `teamflow task ...` and `teamflow pane ...` instead.

## 2. Choose how the team starts

Look at how the user invoked this skill.

- **Plain `teamflow`** (the default): do not start any workers yet. If a team
  is already running, `teamflow list` shows it, and you use it.
  Otherwise plan first (step 3) and add workers only when the plan needs them.
- **`teamflow config`, or `teamflow config <file>`**: start the configured
  default team now with `teamflow start`, adding `--config <file>`
  when the user named one. Then plan and delegate to that team, and still add
  workers on demand if the plan needs a type the team lacks.

`start` prints the tmux session name. If a team already runs with the same
config, it reattaches and names any default workers the session lacks.
Otherwise it refuses and changes nothing: relay its message, and stop a
running team only when the user says so.

## 3. Plan, then add the workers the plan needs

Plan the work with the user first, as the Delegator role describes. When the
plan is ready to execute, decide which workers it needs:

```
teamflow types     # the catalog: CLI, model, effort, use_for
teamflow list      # who is in the session now
teamflow add <type>
```

- Pick each type by its `use_for` and the task: a large or risky change
  needs a stronger developer type than a mechanical edit.
- Add the fewest workers the plan needs. Reuse an idle worker of a suitable
  type before adding another. Add parallel workers only for independent,
  non-overlapping tasks.
- The first `add` starts the tmux session. Later adds open one window each.
  `add <type>` first reuses a worker of that type that is not running, with
  its conversation, and creates a new number only when all of them are busy.
  Never pass `--save` unless the user asks to change the default team.
- Add a Notetaker only when the user wants project notes. Project notes
  belong to the Notetaker. Never write notes yourself or create note tasks.

`teamflow list` prints a `tmux attach -t <session>:<id>` command for
each running worker. Give the user the command for the worker they want to
watch. Inside tmux, `tmux switch-client -t` takes the same target. To watch
several workers side by side, `teamflow view <id> <id>...` (or no
IDs for all) gathers them into one tiled window and prints its command, and
`teamflow view --close` puts them back. Check `tmux show-options -g prefix` first,
because user configurations can change the default `Ctrl-B` prefix.

Before the first task to a new worker, inspect it with
`teamflow pane tail <id>`. A fresh Claude Code worktree
may show a project trust prompt. Sending a task while that prompt is open can
select `No, exit`, leaving the task assigned and the window dead. For a
workspace the user has authorized, select the displayed trust option in tmux,
then check that the CLI is ready. If a worker already died, restart it with
`tmux respawn-pane -t <pane-id>`, handle the prompt, and resend the existing
task ID. `ack=no` alone does not mean a worker is unready: workers
acknowledge when they act on their first message.

## 4. Dispatch and collect

Your shell has no `AGENT_*` variables. Run every mailbox and pane command
through `teamflow task` and `teamflow pane`, which act as the Delegator on
this repo's mailbox:

```
teamflow task ack
teamflow task new developer-l-1 'Short title' <<'EOF'
Concrete assignment and the expected output.
EOF
teamflow pane send-to developer-l-1 "Task T-0001: Short title. Details: task.sh read T-0001"
teamflow task inbox delegator
teamflow task status T-0001
teamflow task read T-0001
teamflow pane tail developer-l-1
teamflow task release T-0001 abc1234
cat .git/teamflow/note-queue/completed/T-0001.md
```

`task new` prints the task id to use in the `send-to` line, and it refuses an
ID that is not in the session. Results arrive only in the mailbox: while a
worker is busy, check `task status` or `task inbox delegator` about every 100
seconds, and read the result before you report. Never say a task is done
before its status is `done`.

After review and integration, release every completed task with
`task release`. With a Notetaker in the team, the mailbox result starts its
investigation, and it checks code, discussion, tests, decisions, and sources
for the relevant note types. Read its completion summary before reporting the
slice. The next task is delivered only after it finishes the current note
pass. Check the Notetaker's window for a trust prompt before releasing the
first task. Without a Notetaker, `release` says the task waits for one.
Report the slice without notes.

## 5. Trim and stop

A minute after this session ends, the team trims itself: idle workers stop,
and busy ones stop when their tasks finish (up to 2 hours). Every teamflow
command you run marks this session as the Delegator, so a restarted session
takes over, and `teamflow start --last` brings stopped workers back.

When `list` or `start` prints "The last team stopped at ...", relay it to the
user. For each unfinished task it names, read the task with
`task read <id>` and ask the user whether to resend it, give it to another
worker, or drop it.
When workers sit idle and the user wants them gone, follow the
`teamflow-trim` skill. When the user asks to stop the team, follow the
`teamflow-kill` skill. It stops the tmux session, removes worktrees whose work
is already merged, and asks the user to merge or discard the rest. The mailbox
and each worker's conversation stay. To bring the same workers back later,
use the `teamflow-resume` skill.
