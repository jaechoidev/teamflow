# teamflow

Cross-vendor subagents for any coding agent.

Your agent session (Codex, Claude Code, or another) becomes the Delegator.
It plans with you, then hands tasks to worker agents from other vendors:
Claude, Codex, or GLM through z.ai, each with its own model and effort. Each
worker runs in its own tmux window that you can watch and step into, keeps
its conversation across sessions, and gets its own git worktree when it
writes code. Tasks and results travel through a mailbox in `.git/teamflow/`,
so nothing is ever typed into your session.

Compared with built-in subagents, workers can come from any vendor, persist,
and stay visible. The cost is weight: teamflow needs tmux, each vendor's CLI
and login, and results arrive asynchronously.

## Quickstart

```
cd /path/to/your-project
teamflow init
```

`init` prepares the project and launches nothing. Then open your agent in the
project and use the `teamflow` skill in one of two ways:

- **`teamflow`**: the session becomes the Delegator, and no workers start.
  After planning, the Delegator picks worker types from the catalog and adds
  just the workers the plan needs with `teamflow add <type>`. The first add
  starts a detached tmux session.
- **`teamflow config [file]`**: starts the default team from `teamflow.conf`
  (or the named config) first, then plans and delegates to it.

Watch the workers with `tmux attach -t <session>`, one window per worker.
A bare `teamflow` in the shell only prints help.

The `teamflow` shell alias points to this repo's `scripts/teamflow`.
`teamflow start` from the target folder starts the default team from the
shell, running `init` first when the scaffold or Git history is missing.
From this tool home, `init` creates a Git repository when needed. It copies
`scripts/teamflow`, `scripts/teamflow_workers.py`, `scripts/teamflow_scaffold.py`,
and `.agents/` (roles, lib, doc templates, AGENTS-SECTION.md), writes
`teamflow.conf` only when absent, and updates only the marked AGENTS.md
section. If the repository has no commit, `init`
makes a first commit containing only the teamflow scaffold. Existing staged
or untracked project files stay outside that commit. Existing repositories
with a commit are never committed by `init`. The first commit gives the
launcher a HEAD for developer worktrees.

## Updates and upgrades

One version of teamflow drives a team. When the tool home runs `init`,
`start`, `up`, or `workers` for a project, it first refreshes the project's
copies of the shipped files. A copy is replaced only while it is unmodified:
it matches the version recorded in `.agents/teamflow-manifest` or any
version committed in the tool home. A locally edited copy is kept, and the
shipped version lands next to it as `<file>.new` for review. The tool home
also records itself in `.git/teamflow/tool-home`. From then on the project's
own `scripts/teamflow`, which the skills call, hands every command to that
tool home while it is unmodified. Refreshed files show up in `git status`.
Review and commit them like any other change.

Versions before the rename kept their state under the name ai-team. The
first `init`, `start`, `up`, or `workers` command moves it:
`.git/ai-team/` becomes `.git/teamflow/`, worktrees move from
`.ai-team-worktrees/` to `.teamflow-worktrees/`, `ai-team/<id>` branches
become `teamflow/<id>`, and the AGENTS.md markers are renamed. A team still
running from the old state is refused until `--kill`. Developer
conversations restart after the move, because Claude files a transcript
under the directory it ran in.

## Worker catalog

`teamflow.conf` has two parts. `[type.<name>]` sections are the catalog:
each sets a CLI, model, effort, worktree policy, role file, and `use_for`,
which tells the Delegator when to pick that type. `teamflow types` lists
them. The shipped catalog:

| Type | Agent | Use for |
| --- | --- | --- |
| `researcher` | z.ai GLM (`glm-5.3`, max) | external research, sources and citations, comparing options |
| `reviewer` | Claude (`fable`, xhigh) | reviewing plans, architecture, and diffs |
| `developer-l` | Claude (`fable`, max) | large or risky changes, cross-cutting refactors, hard bugs |
| `developer-m` | Claude (`opus`, max) | typical features and fixes with a clear scope |
| `developer-s` | z.ai GLM (`glm-5.3`, max) | small, well-defined edits, docs, mechanical changes |
| `notetaker` | Claude (`opus`, xhigh) | project notes in `docs/notes/`, only when you want notes |

`[worker.<instance-id>]` sections are the optional default team that
`teamflow config` and `teamflow start` launch. The shipped default team is
`researcher-1`, `reviewer-1`, `developer-l-1`, and the `notetaker`. A config
with no worker sections is a pure catalog, and workers only join on demand.
The three developer types share `.agents/roles/developer.md`. The type
selects the CLI, model, and effort, and the Delegator defines task scope in
each assignment. The Notetaker has the singleton ID `notetaker` and must be
last in a default team. The launcher never silently substitutes a model. If
something is missing it says exactly what and where to fix it.

## Managing workers

```
teamflow types                      # the worker catalog
teamflow list                       # the running roster
teamflow add developer-m            # add a worker. With no team running, start one
teamflow add developer-l-1          # bring back a default-team worker
teamflow add researcher --save      # also add it to the default team
teamflow remove developer-m-1
teamflow sync                       # repair the running windows
teamflow trim                       # remove idle workers
teamflow start --only notetaker     # a team with just one worker
teamflow start --last               # the last session's workers again
```

`add`, `remove`, `list`, and `sync` are short for `teamflow workers ...`.
Each worker runs in its own tmux window, named by its instance ID. While a
team runs, `add` and `remove` change that session only: adding opens a
window, removing closes one, and other workers keep running. The session
roster lives in `.git/teamflow/running.conf`, so `teamflow.conf` and
`git status` stay unchanged. Add `--save` to also write the change to the
default team in `teamflow.conf`. `teamflow list` marks workers that exist in
this session only.

With no team running, `add` starts one with just that worker, so a team can
grow from zero as the Delegator needs workers. `add <type>` first brings back
a default-team instance of that type that is missing from the session, then
creates a new numbered one. `add <id>` brings back that default-team
instance. A plain `remove` with no team running refuses, because there is no
session to change. With `--save` and no team running, `add` and `remove` edit
only the default team.

`trim` removes every worker without an assigned or in-progress task. The
Notetaker stays while it writes a note or released tasks wait for it. When
every worker is idle, `trim` stops the team like `--kill`. Removed workers
keep their conversations and branches, and `add` brings them back.

`start --only <type-or-id>` launches a team with a single worker, for
example only the Notetaker to catch up on waiting notes. `start --last`
brings back the last session's workers with the current type settings. In
both cases `teamflow.conf` stays the default team. While a smaller team runs,
`start` reattaches and names the default workers the session lacks.

Instances have stable numbered IDs for tasks, windows, conversations,
worktrees, and branches. A number is never reused, including numbers from
earlier sessions. Removal keeps the worker's history, and its worktree stays
until `--kill` cleans it up (see Stopping and worktrees). It refuses an
unfinished assigned task or a note in progress, and it cannot remove the last
worker (stop the team with `--kill` instead). `sync` makes the running
windows match the session roster again, for example after a worker died or an
interrupted change. Edits to `teamflow.conf` while a team runs apply at the
next start. Changes to settings of running workers (model, effort, CLI, or
role file) require a restart. Use `--config <file>` to manage a variant.

## Choosing a config

`teamflow.conf` is the default. Every mode that reads a config takes
another file with `--config`:

```
./scripts/teamflow up --config <file>              # launch or reattach
./scripts/teamflow up --no-attach --config <file>  # same, detached
./scripts/teamflow --verify --config <file>
./scripts/teamflow --check-models --config <file>
./scripts/teamflow --kill --config <file>
```

Mode and flags go in any order, and `--config=<file>` works too. `init`
takes no config. A variant has a `[workspace]` section and at least one
worker instance. `[worker.notetaker]` is optional and must be last. Without `--config`
everything behaves as before.

**Paths.** An absolute path is used as given, and a leading `~/` means
your home directory. A relative path resolves from the repo root, never
from the current directory. The same spelling therefore names the same
file from a subdirectory and from any linked worktree, such as a team
worktree under
`.teamflow-worktrees/`. When the file is missing, the error prints the
full path the launcher looked for.

**One team per repo.** The mailbox and the role worktrees belong to the
repo, not to a config, so two configs cannot run side by side. The
launcher records the running session and the config that started it in
`.git/teamflow/active` and checks it before it touches anything:

- `up` with the config that started the running team reattaches.
- `up` with any other config stops, names both configs, and changes
  nothing. Plain `up` while a variant runs is such a case.
- `--verify` and `--kill` without `--config` act on the running team,
  whichever config started it. With `--config`, the file must be the
  one the running team was started with.
- `--check-models` only reads the config. It works while another
  config runs, so use it before a switch.

Switching is always stop, then start:

```
./scripts/teamflow --kill
./scripts/teamflow up --config teamflow.claude.conf
```

### Example: a Claude Delegator

```
cp teamflow.conf teamflow.claude.conf
```

Edit the Delegator section of the copy and leave the rest alone:

```
[pane.delegator]
label = Delegator (claude fable)
cli = claude
model = fable
effort = medium
worktree = no
```

Then check the models, stop the running team, and start the variant:

```
./scripts/teamflow --check-models --config teamflow.claude.conf
./scripts/teamflow --kill
./scripts/teamflow up --config teamflow.claude.conf
./scripts/teamflow --verify        # reports the running team and its config
```

A fresh Claude or z.ai Delegator gets the same handshake as the Codex
one: a first message that tells it to read AGENTS.md, write its
acknowledgement, and reply READY. Its role file arrives through the
system prompt, as for every Claude pane. Workers still get no startup
message.

Conversations follow the CLI. The Claude Delegator starts its own
conversation and never resumes the Codex id. The Codex entry is parked
as `.git/teamflow/sessions/delegator.codex`, and the next launch with
`teamflow.conf` resumes it. Instances with the same ID and CLI in both
configs keep their conversations across the switch. Each variant may choose
its own roster and type settings.

## Workers mode (external Delegator)

Run the Delegator yourself in any terminal, with Codex, Claude, or Claude
routed to z.ai. Workers join on demand with `teamflow add <type>`, or the
default team starts together:

```
./scripts/teamflow start                    # the default team; prints the session name
tmux attach -t <session>                     # watch the workers (optional)
```

Each worker runs in its own window, named by its instance ID, with its
model, worktree, and conversation resume. `[pane.delegator]` is
ignored and may be left out. No Delegator pane or CLI starts, and nothing
is typed into your session: results arrive in the mailbox. `--config`,
`--verify`, and `--kill` work as for the full team. One team runs per
repo and the mode counts, so `up` and `up --workers` refuse each other
until `--kill`.

Your session has no `AGENT_*` variables. `.agents/lib/delegator.sh` sets
them from the repo it belongs to, then runs `task.sh` or `pane.sh`:

```
bash .agents/lib/delegator.sh task new developer-l-1 'Title' <<'EOF'
...assignment...
EOF
bash .agents/lib/delegator.sh pane send-to developer-l-1 "Task T-0001: Title. Details: task.sh read T-0001"
bash .agents/lib/delegator.sh task inbox delegator
bash .agents/lib/delegator.sh task release T-0001 abc1234
cat .git/teamflow/note-queue/completed/T-0001.md
```

It refuses to run inside a worker window. `init` copies the `teamflow`,
`teamflow-resume`, `teamflow-kill`, `teamflow-workers`, and `teamflow-trim`
skills from the tool home into the project's `.agents/skills/`
for Codex and `.claude/skills/` for Claude Code. Later runs refresh
unmodified copies and stage `.new` files for local edits (see Updates and
upgrades).

## Prerequisites

- macOS or Linux; `tmux`, `python3`, and `git` ≥ 2.31 (the launcher uses
  `git rev-parse --path-format=absolute`, introduced in Git 2.31.0, to
  locate the shared git dir that hosts the mailbox)
- `codex` CLI on PATH (Delegator)
- `claude` CLI on PATH (Claude panes; logged in)
- z.ai panes: an env file with the backend URL + token — default
  `~/.zai/env.sh`, changeable via `[workspace] zai_env`. Keys never live in
  this repo.

## Daily use

- **Launch/reattach full team**: `./scripts/teamflow up` (from anywhere inside the repo)
- **Another config**: `./scripts/teamflow up --config <file>`, one team at
  a time (see Choosing a config)
- **Workers only**: `./scripts/teamflow start`, with the Delegator in
  your own terminal (see Workers mode)
- **Talk**: in workers mode, plan with the Delegator in your terminal. It
  turns the plan into tasks, adds the workers it needs, dispatches the
  tasks, and reports real results. In full-team mode, use the `delegator`
  window.
- **Dispatch protocol**: tasks go through the mailbox — the Delegator does
  this for you via `task.sh new` + `pane.sh send-to`. Workers `take` →
  `done` (result in the task record). Nothing is ever typed into the
  Delegator pane (`send-to delegator` is rejected); it discovers
  completions via `task.sh inbox delegator`.
- **Check results yourself**: `bash .agents/lib/task.sh list`,
  `... read T-0003`, `... inbox <instance-id>`
- **Send text to a pane**: `bash .agents/lib/pane.sh send-to developer-l-1 "..."`
  (`... tail developer-l-1` shows what's on screen — a CLI may be busy; delivery
  ≠ completion, the mailbox is the source of truth)
- **Verify panes**: `./scripts/teamflow --verify` (liveness + role acks)
- **Trim**: `./scripts/teamflow trim` (removes idle workers; stops the team
  when every worker is idle)
- **Stop**: `./scripts/teamflow --kill` (stops the session, removes worktrees
  whose work is already merged, and lists the rest; see Stopping and
  worktrees)
- **Note handoff**: after reading a result and integrating any code, the
  Delegator runs `task.sh release <id> [integrated-commit]`. The Notetaker
  reads the result and underlying evidence, updates notes, and runs
  `task.sh noted <id> '<summary>'`. That removes the completed task record
  and sends the next released task. No timer runs while it writes a note.
  Without a Notetaker, `release` reports that the task waits for one.

## Integrating approved work

Concurrent work is isolated by construction: each developer instance commits to
its own branch `teamflow/<instance-id>` in its own worktree, one commit per
completed task, and task scopes are assigned not to overlap. The role
finishes and reports its commit. The Delegator inspects the full diff and
integrates a small, obvious change after a relevant check. Broader or
unclear changes go to the Reviewer first. This review and integration flow
has your approval; nothing is pushed without your explicit go-ahead.
`main` below means your default branch.

```
# Inspect one role's work (read-only, no checkout needed). The Delegator
# or Reviewer uses these commands. An empty log means the role has
# nothing to integrate: skip it.
git -C .teamflow-worktrees/developer-l-1 log --oneline main..teamflow/developer-l-1
git -C .teamflow-worktrees/developer-l-1 diff main...teamflow/developer-l-1
git -C .teamflow-worktrees/developer-l-1 diff --name-only main...teamflow/developer-l-1

# Integrate exactly one role after Reviewer approval or a documented
# Delegator self-review of a small, obvious change.
# The main checkout must be clean before starting.
git status --short                 # in the main checkout: no output = clean
git checkout main
git cherry-pick main..teamflow/developer-l-1
# or squash into one commit. Both paths stop on conflict instead of
# overwriting:
# git -C .teamflow-worktrees/developer-l-1 diff main...teamflow/developer-l-1 | git apply --3way
# git commit -m "type(scope): subject for the whole task"

# Run the project's relevant checks before integrating the next role.

# Optional: refresh the other roles' worktrees so later work rebases onto
# the updated main and future patches apply cleanly. Rebase a role's
# worktree only when it is idle (its CLI not mid-task) and clean
# (git -C .teamflow-worktrees/<instance-id> status shows nothing).
git -C .teamflow-worktrees/developer-l-1 rebase main
```

Because integration goes through `cherry-pick` or `git apply --3way`, a
collision with already-integrated work stops as a conflict you resolve
deliberately. No role's checkout ever overwrites another's files. Only
reviewed work enters main; nothing is pushed without your approval.

## Stopping and worktrees

`--kill` stops the tmux session, then looks at each stopped worker's
worktree. When the worktree has no uncommitted changes and every commit on
its branch is already in the main checkout's current branch (cherry-picked
or squash-merged), the worktree and branch are removed. The others are
listed with what they hold. Decide each one:

```
./scripts/teamflow worktrees list
./scripts/teamflow worktrees merge developer-l-1    # commit leftovers, cherry-pick onto the current branch
./scripts/teamflow worktrees discard developer-l-1  # delete the worktree, branch, and changes
```

`merge` needs a main checkout without uncommitted changes to tracked files.
It commits the worktree's uncommitted changes, cherry-picks the branch's
commits that are not merged yet, then removes the worktree and branch. On a
conflict it aborts, merges nothing, and keeps the worktree. `discard` cannot
be undone. Both refuse a worker that is still running. The `teamflow-kill`
skill asks you which one to use for each kept worktree.

The launcher creates a developer's worktree right before its window starts,
at `start` or `workers add`. A new worktree gets a fresh branch from the main
checkout's current commit. A kept branch is reused, so its work continues.

## Where things live

- `.agents/roles/*.md` — role instructions (static; injected at launch)
- `.git/teamflow/` — the mailbox: `tasks/<id>/{task.md,result.md,status}`,
  `acks/`, `panes.tsv`, `sends.log`, `sessions/<instance-id>` (per-role conversation
  registry; runtime state; excluded from git; instantly visible to all
  worktrees because it sits in the git common dir)
- `.git/teamflow/active` - the running session and the config that
  started it. `.git/teamflow/sessions/<instance-id>.<cli>` - a conversation
  parked while that role runs another CLI. `.git/teamflow/tool-home` - the
  tool home that last refreshed this project (see Updates and upgrades).
- `.agents/teamflow-manifest` - blob ids of the shipped files as installed,
  so a later refresh can tell unmodified copies from local edits
- `scripts/teamflow_scaffold.py` - installs, refreshes, and migrates the scaffold
- `.teamflow-worktrees/<instance-id>` + branches `teamflow/<instance-id>` - developer panes
  (created when the worker starts, removed by `--kill` once their work is merged)
- Repo-root `AGENTS.md` — shared coordination rules (marked section,
  `teamflow init` owns only the markers)
- `.agents/doc-templates/` - note templates and the project notes workflow
- `.agents/lib/delegator.sh` - runs `task.sh` and `pane.sh` as the
  Delegator from outside tmux (workers mode)
- `skills/teamflow/`, `skills/teamflow-resume/`, `skills/teamflow-kill/`, `skills/teamflow-workers/`, and `skills/teamflow-trim/` (tool home) - source skills
- `.agents/skills/` and `.claude/skills/` (initialized projects) - repo-scoped copies of those skills

## Project notes

`init` copies five plain Markdown templates into `.agents/doc-templates/`:
source, concept, code map, decision, and experiment. They share one
frontmatter convention with a review status, and the folder's README.md
is the workflow. Notes belong to the Notetaker alone. Teamflow creates
`docs/notes/` when a Notetaker joins the team, at start or through
`workers add notetaker`, and never otherwise. The Delegator never writes
notes. Without a Notetaker, released tasks wait in the mailbox, and a
Notetaker added later works through them one at a time.
The Notetaker keeps notes current with each completed
implementation slice, using worker results to find the relevant code,
tests, discussion, and sources. The user writes
own-words explanations and memory answers in concept notes. Notes link to
Superpowers specs and plans rather than copying them. Obsidian is
optional: open `docs/` as a vault to browse notes, specs, and plans
together. Init does not create a vault or `docs/`, and teamflow creates the
notes folder without configuring Obsidian.

## Conversation persistence

Each pane's CLI conversation survives `--kill` and relaunch. On a fresh `up`
(read: the tmux session is gone) the launcher consults the per-role registry
`.git/teamflow/sessions/<instance-id>` and never touches a live session (reattach
still wins):

- **Claude / z.ai panes** boot with an explicit `--session-id` (a UUID the
  launcher generates) and later boot with `--resume <id>`, always re-passing
  `--model`, `--effort`, the role file and, for z.ai, the env file. The id
  is reused only while its transcript still exists under
  `$CLAUDE_CONFIG_DIR` (default `~/.claude`)/`projects/`; when it is gone
  (expired or deleted) the role starts a new conversation under a fresh id.
  The old transcript is never touched.
- **Delegator (Codex)** mints its own id at first boot; the launcher
  discovers it from `~/.codex/session_index.jsonl` (newest entry whose
  rollout records this workspace's cwd) and later resumes it with
  `codex resume <id>`, re-pinning model, working directory, and full-access
  mode. If discovery is slow (codex still booting), the registry stays
  `pending` and `--verify` backfills the id.
- Roles never share ids, and `--continue`/`--last` are never used: panes
  share a cwd, so "most recent in directory" could resume another role's
  conversation. The registry stores ids and metadata only — no tokens, no
  transcript content.
- **A changed `cli`** (another config, or an edit) never resumes the old
  id: an id belongs to the CLI that minted it, and `claude` and `zai`
  count as different CLIs. The launcher parks the old entry as
  `sessions/<instance-id>.<cli>` and starts a fresh conversation, or restores
  the one parked for the configured CLI. Switching back resumes where
  that CLI left off.

## Voice, models, and other honest limitations

- **Voice**: the Codex CLI has no voice mode (verified on codex-cli 0.156.1).
  The Delegator is keyboard-driven; Codex.app (desktop) has voice if you
  need it — separate from this workspace.
- **Model availability**: `--check-models` probes each configured model with
  one tiny request (costs a little quota) and reports OK/UNAVAILABLE per
  pane. Codex has no cheap probe; its model is verified at first use.
- **First run in a new folder**: Codex shows a one-time "Trust this folder?"
  prompt — accept it once (per folder).
- **Startup cost**: the Delegator gets a role handshake. The Notetaker gets
  a queue recovery handshake. The other worker panes load their role via
  `--append-system-prompt-file` (Claude/z.ai) and act when first addressed.
  The note queue uses no model calls while idle or while a note is in progress.
- **Busy CLIs**: `send-to` types into a pane; if that CLI is mid-turn the
  text queues as input. Check `pane.sh tail <instance-id>`, and rely on task
  records for outcomes.
- **Conversation persistence**: claude transcripts auto-expire per your
  claude retention settings (default 30 days) — expect a conversation
  rollover then. Codex id discovery only accepts sessions whose rollout
  records this workspace's cwd (`session_meta.payload.cwd`), so unrelated
  codex sessions elsewhere never win; only a same-directory codex session
  started in the same seconds could still race discovery (delete
  `.git/teamflow/sessions/delegator` to reset, or let `--verify` backfill).
  Two concurrent `up` runs in different terminals could resume the same id
  twice — run one at a time.
- **Config selection**: a config is identified by its file path. Editing
  a config while its team runs goes unnoticed, and the edit applies at
  the next launch. The one-team check sees the tmux server the launcher
  talks to, so a team on another tmux socket is not detected. A session
  without a launch record (started by an older copy of the launcher) is
  attributed to the default config. Codex support is built for the
  Delegator pane: its startup prompt is worded for the Delegator, and id
  discovery goes by working directory. A variant that runs Codex in a
  worker role, or in two panes that share a directory, is untested.
