# ai-team — six-agent tmux workspace

One command opens a tmux session with six persistent CLI agents in a fixed
2×3 layout, each with its own role, a shared task mailbox, and (for the
developers) dedicated git worktrees. Portable: any git repo can adopt it.

```
./scripts/ai-team init     # once per project (scaffolds this tool into it)
./scripts/ai-team up       # launch or reattach
```

## Pane map

| | Left | Right |
| --- | --- | --- |
| **Row 1** | Delegator — Codex (`gpt-6-sol`), your voice/interface | Researcher — z.ai GLM (`glm-5.3`) |
| **Row 2** | Reviewer & Planner — Claude (`fable`, xhigh) | Dev Senior — Claude (`fable`, xhigh) |
| **Row 3** | Dev Mid — Claude (`opus`, xhigh) | Dev Junior — z.ai GLM (`glm-5.3`) |

Models, CLIs, and effort levels are config, not code: edit `ai-team.conf`
(one file, one `[pane.<role>]` section per pane, in layout order). The
launcher never silently substitutes a model — if something is missing it
says exactly what and where to fix it.

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

- **Launch/reattach**: `./scripts/ai-team up` (from anywhere inside the repo)
- **Talk**: type to the Delegator (top-left); it dispatches to workers and
  reports real results. You can also type directly into any worker pane.
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
- **Verify panes**: `./scripts/ai-team --verify` (liveness + role acks)
- **Stop**: `./scripts/ai-team --kill` (session only; worktrees and branches
  stay, nothing is merged/pushed/deleted)
- **Old task records**: `bash .agents/lib/task.sh clean [days]` (Delegator
  only; prunes done tasks older than the given days)

## Integrating approved work

Concurrent work is isolated by construction: each developer role commits to
its own branch `ai-team/<role>` in its own worktree, and task scopes are
assigned not to overlap. Integration is always your call (AGENTS.md: no
merges, commits-to-main, or pushes without approval) and happens one role at
a time in the main checkout. `main` below means your default branch.

```
# Inspect one role's work (read-only, no checkout needed). The reviewer
# uses the same commands before recommending integration.
git -C .ai-team-worktrees/dev-mid log --oneline main..ai-team/dev-mid
git -C .ai-team-worktrees/dev-mid diff main...ai-team/dev-mid
git -C .ai-team-worktrees/dev-mid diff --name-only main...ai-team/dev-mid

# With approval, integrate exactly one role in the main checkout.
git checkout main
git cherry-pick main..ai-team/dev-mid
# or squash into a patch. Both stop on conflict instead of overwriting:
# git -C .ai-team-worktrees/dev-mid diff main...ai-team/dev-mid | git apply --3way

# Verify before integrating the next role.
bash tests/run_all.sh

# Optional: refresh the other roles' worktrees so later work rebases onto
# the updated main and future patches apply cleanly.
git -C .ai-team-worktrees/dev-senior rebase main
```

Because integration goes through `cherry-pick` or `git apply --3way`, a
collision with already-integrated work stops as a conflict you resolve
deliberately. No role's checkout ever overwrites another's files, and
neither the launcher nor the roles merge, commit to main, or push on their
own.

## Where things live

- `.agents/roles/*.md` — role instructions (static; injected at launch)
- `.git/ai-team/` — the mailbox: `tasks/<id>/{task.md,result.md,status}`,
  `acks/`, `panes.tsv`, `sends.log` (runtime state; excluded from git;
  instantly visible to all worktrees because it sits in the git common dir)
- `.ai-team-worktrees/<role>` + branches `ai-team/<role>` — developer panes
  (created only if absent, never reset)
- Repo-root `AGENTS.md` — shared coordination rules (marked section,
  `ai-team init` owns only the markers)

## Voice, models, and other honest limitations

- **Voice**: the Codex CLI has no voice mode (verified on codex-cli 0.156.1).
  The Delegator is keyboard-driven; Codex.app (desktop) has voice if you
  need it — separate from this workspace.
- **Model availability**: `--check-models` probes each configured model with
  one tiny request (costs a little quota) and reports OK/UNAVAILABLE per
  pane. Codex has no cheap probe; its model is verified at first use.
- **First run in a new folder**: Codex shows a one-time "Trust this folder?"
  prompt — accept it once (per folder).
- **Startup cost**: only the Delegator gets a startup message (its role
  handshake). The five worker panes load their role via
  `--append-system-prompt-file` (Claude/z.ai) — zero startup chat; a worker
  acts only when first addressed. Acknowledgements appear in `--verify`
  after each agent's first action.
- **Busy CLIs**: `send-to` types into a pane; if that CLI is mid-turn the
  text queues as input. Check `pane.sh tail <role>`, and rely on task
  records for outcomes.

## Tests

`bash tests/run_all.sh` — 121 assertions: layout order, role routing, env
handoff, worktrees, mailbox concurrency, send-to quoting (incl. the
delegator-pane guard), task claim/completion exclusivity, init
idempotence, reattach, diagnostics. Uses stub CLIs and an isolated tmux
socket; spends no model quota.
