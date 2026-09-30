# teamflow - tmux workspace

In workers mode, the Delegator plans with the user in an external terminal.
Configured workers and the optional Notetaker share one tmux window.
The launcher also supports a full-team mode with the Delegator on the left
and configured roles on the right. They share a task mailbox, and developers have
dedicated git worktrees.
Any git repo can adopt it.

```
cd /path/to/your-project
teamflow start
```

The `teamflow` shell alias points to this repo's `scripts/teamflow`. Run
`teamflow start` from the target folder. If the teamflow scaffold or Git history is
missing, it runs `init` first. You can run `init` separately to prepare the
project without launching workers. From this tool home, `init` creates a Git
repository when needed. It copies
`scripts/teamflow` and `.agents/` (roles, lib, doc templates,
AGENTS-SECTION.md), writes `teamflow.conf` only when absent, and updates
only the marked AGENTS.md section. If the repository has no commit, `init`
makes a first commit containing only the teamflow scaffold. Existing staged
or untracked project files stay outside that commit. Existing repositories
with a commit are never committed by `init`. The first commit gives the
launcher a HEAD for developer worktrees. `start` launches workers mode
without attaching to the tmux session.

For an existing project, run `init` again to receive the new role and queue
files. Init keeps your `teamflow.conf`, so add the `[pane.notetaker]` section
from this repo's config to enable a final Notetaker pane there.

## Pane map

In workers mode, the **Delegator and Planner** runs outside tmux. The
default config stacks these six panes from top to bottom:

1. **Researcher** - z.ai GLM (`glm-5.3`, max)
2. **Reviewer** - Claude (`fable`, max)
3. **Dev Senior** - Claude (`fable`, max)
4. **Dev Mid** - Claude (`opus`, max)
5. **Dev Junior** - z.ai GLM (`glm-5.3`, max)
6. **Notetaker** - Claude (`opus`, xhigh), maintaining `docs/notes/`

In full-team mode, the Delegator gets a full-height left pane. Configured
worker roles stack in the right column. The two columns have equal width.

Models, CLIs, effort levels, and pane count come from `teamflow.conf`
(one file, one `[pane.<role>]` section per pane, in layout order). The
launcher never silently substitutes a model — if something is missing it
says exactly what and where to fix it.

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
worker pane. `[pane.notetaker]` is optional and must be last. Without `--config`
everything behaves as before.

**Paths.** An absolute path is used as given, and a leading `~/` means
your home directory. A relative path resolves from the repo root, never
from the current directory. The same spelling therefore names the same
file from a subdirectory and from a team worktree under
`.ai-team-worktrees/`. When the file is missing, the error prints the
full path the launcher looked for.

**One team per repo.** The mailbox and the role worktrees belong to the
repo, not to a config, so two configs cannot run side by side. The
launcher records the running session and the config that started it in
`.git/ai-team/active` and checks it before it touches anything:

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
as `.git/ai-team/sessions/delegator.codex`, and the next launch with
`teamflow.conf` resumes it. Roles whose `cli` is the same in both configs
keep their conversations across the switch. The role table in AGENTS.md
lists the default CLIs. Roles and routing are the same in every config.

## Workers mode (external Delegator)

Run the Delegator yourself in any terminal, with Codex, Claude, or Claude
routed to z.ai, and let the launcher start the configured workers:

```
./scripts/teamflow start                    # prints the session name
tmux attach -t <session>                     # watch the workers (optional)
```

Configured workers and an optional Notetaker stack top to bottom in one full-width
column, with their usual models, worktrees, and conversation resume. `[pane.delegator]` is
ignored and may be left out. No Delegator pane or CLI starts, and nothing
is typed into your session: results arrive in the mailbox. `--config`,
`--verify`, and `--kill` work as for the full team. One team runs per
repo and the mode counts, so `up` and `up --workers` refuse each other
until `--kill`.

Your session has no `AGENT_*` variables. `.agents/lib/delegator.sh` sets
them from the repo it belongs to, then runs `task.sh` or `pane.sh`:

```
bash .agents/lib/delegator.sh task new dev-mid 'Title' <<'EOF'
...assignment...
EOF
bash .agents/lib/delegator.sh pane send-to dev-mid "Task T-0001: Title. Details: task.sh read T-0001"
bash .agents/lib/delegator.sh task inbox delegator
bash .agents/lib/delegator.sh task release T-0001 abc1234
cat .git/ai-team/note-queue/completed/T-0001.md
```

It refuses to run inside a worker pane. `init` copies the `teamflow`,
`teamflow-resume`, and `teamflow-kill` skills from the tool home into the project's `.agents/skills/`
for Codex and `.claude/skills/` for Claude Code. Re-running `init` preserves
local skill edits and stages changed shipped files as `.new` for review.

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
  turns the plan into tasks, dispatches them, and reports real results. In
  full-team mode, use the left Delegator pane.
- **Dispatch protocol**: tasks go through the mailbox — the Delegator does
  this for you via `task.sh new` + `pane.sh send-to`. Workers `take` →
  `done` (result in the task record). Nothing is ever typed into the
  Delegator pane (`send-to delegator` is rejected); it discovers
  completions via `task.sh inbox delegator`.
- **Check results yourself**: `bash .agents/lib/task.sh list`,
  `... read T-0003`, `... inbox <role>`
- **Send text to a pane**: `bash .agents/lib/pane.sh send-to dev-mid "..."`
  (`... tail dev-mid` shows what's on screen — a CLI may be busy; delivery
  ≠ completion, the mailbox is the source of truth)
- **Verify panes**: `./scripts/teamflow --verify` (liveness + role acks)
- **Stop**: `./scripts/teamflow --kill` (session only; worktrees and branches
  stay, nothing is merged/pushed/deleted)
- **Note handoff**: after reading a result and integrating any code, the
  Delegator runs `task.sh release <id> [integrated-commit]`. The Notetaker
  reads the result and underlying evidence, updates notes, and runs
  `task.sh noted <id> '<summary>'`. That removes the completed task record
  and sends the next released task. No timer runs while it writes a note.

## Integrating approved work

Concurrent work is isolated by construction: each developer role commits to
its own branch `ai-team/<role>` in its own worktree, one commit per
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
git -C .ai-team-worktrees/dev-mid log --oneline main..ai-team/dev-mid
git -C .ai-team-worktrees/dev-mid diff main...ai-team/dev-mid
git -C .ai-team-worktrees/dev-mid diff --name-only main...ai-team/dev-mid

# Integrate exactly one role after Reviewer approval or a documented
# Delegator self-review of a small, obvious change.
# The main checkout must be clean before starting.
git status --short                 # in the main checkout: no output = clean
git checkout main
git cherry-pick main..ai-team/dev-mid
# or squash into one commit. Both paths stop on conflict instead of
# overwriting:
# git -C .ai-team-worktrees/dev-mid diff main...ai-team/dev-mid | git apply --3way
# git commit -m "type(scope): subject for the whole task"

# Run the project's relevant checks before integrating the next role.

# Optional: refresh the other roles' worktrees so later work rebases onto
# the updated main and future patches apply cleanly. Rebase a role's
# worktree only when it is idle (its CLI not mid-task) and clean
# (git -C .ai-team-worktrees/<role> status shows nothing).
git -C .ai-team-worktrees/dev-senior rebase main
```

Because integration goes through `cherry-pick` or `git apply --3way`, a
collision with already-integrated work stops as a conflict you resolve
deliberately. No role's checkout ever overwrites another's files. Only
reviewed work enters main; nothing is pushed without your approval.

## Where things live

- `.agents/roles/*.md` — role instructions (static; injected at launch)
- `.git/ai-team/` — the mailbox: `tasks/<id>/{task.md,result.md,status}`,
  `acks/`, `panes.tsv`, `sends.log`, `sessions/<role>` (per-role conversation
  registry; runtime state; excluded from git; instantly visible to all
  worktrees because it sits in the git common dir)
- `.git/ai-team/active` - the running session and the config that
  started it. `.git/ai-team/sessions/<role>.<cli>` - a conversation
  parked while that role runs another CLI.
- `.ai-team-worktrees/<role>` + branches `ai-team/<role>` — developer panes
  (created only if absent, never reset)
- Repo-root `AGENTS.md` — shared coordination rules (marked section,
  `teamflow init` owns only the markers)
- `.agents/doc-templates/` - note templates and the project notes workflow
- `.agents/lib/delegator.sh` - runs `task.sh` and `pane.sh` as the
  Delegator from outside tmux (workers mode)
- `skills/teamflow/`, `skills/teamflow-resume/`, and `skills/teamflow-kill/` (tool home) - source skills
- `.agents/skills/` and `.claude/skills/` (initialized projects) - repo-scoped copies of those skills

## Project notes

`init` copies five plain Markdown templates into `.agents/doc-templates/`:
source, concept, code map, decision, and experiment. They share one
frontmatter convention with a review status, and the folder's README.md
is the workflow. `teamflow up` creates `docs/notes/` and adopts the workflow.
The Notetaker keeps notes current with each completed
implementation slice, using worker results to find the relevant code,
tests, discussion, and sources. The user writes
own-words explanations and memory answers in concept notes. Notes link to
Superpowers specs and plans rather than copying them. Obsidian is
optional: open `docs/` as a vault to browse notes, specs, and plans
together. Init does not create a vault or `docs/`. Up creates the notes
folder without configuring Obsidian.

## Conversation persistence

Each pane's CLI conversation survives `--kill` and relaunch. On a fresh `up`
(read: the tmux session is gone) the launcher consults the per-role registry
`.git/ai-team/sessions/<role>` and never touches a live session (reattach
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
  `sessions/<role>.<cli>` and starts a fresh conversation, or restores
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
  text queues as input. Check `pane.sh tail <role>`, and rely on task
  records for outcomes.
- **Conversation persistence**: claude transcripts auto-expire per your
  claude retention settings (default 30 days) — expect a conversation
  rollover then. Codex id discovery only accepts sessions whose rollout
  records this workspace's cwd (`session_meta.payload.cwd`), so unrelated
  codex sessions elsewhere never win; only a same-directory codex session
  started in the same seconds could still race discovery (delete
  `.git/ai-team/sessions/delegator` to reset, or let `--verify` backfill).
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
