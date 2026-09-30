# Worker types and instances

## Intent

Let a team contain any number of researchers, reviewers, and developers without repeating their CLI and model settings. Give each worker a stable identity for tasks, panes, conversations, and worktrees. Keep one Notetaker for the whole team. Provide a skill that can list, add, and remove workers through deterministic launcher commands.

## Configuration

`teamflow.conf` defines reusable worker types and ordered instances. Type names and instance IDs use lowercase letters, digits, and hyphens. The displayed developer sizes remain L, M, and S.

```ini
[type.researcher]
label = Researcher
cli = zai
model = glm-5.3
effort = max
worktree = no
role_file = .agents/roles/researcher.md
next_id = 2

[type.reviewer]
label = Reviewer
cli = claude
model = fable
effort = xhigh
worktree = no
role_file = .agents/roles/reviewer.md
next_id = 2

[type.developer-m]
label = Developer M
cli = claude
model = opus
effort = max
worktree = yes
role_file = .agents/roles/developer.md
next_id = 2

[type.notetaker]
label = Notetaker
cli = claude
model = opus
effort = xhigh
worktree = no
role_file = .agents/roles/notetaker.md

[worker.researcher-1]
type = researcher

[worker.reviewer-1]
type = reviewer

[worker.developer-m-1]
type = developer-m

[worker.notetaker]
type = notetaker
```

Type sections define settings and point to role instructions. The developer L, M, and S profiles share `.agents/roles/developer.md`. Worker sections contain only a type reference. Their order determines pane order. A type may have zero instances. `[pane.delegator]` remains the optional full mode Delegator definition. `[worker.notetaker]` is an optional singleton placed last and must refer to `[type.notetaker]`. At least one regular worker is required. Numbered instance IDs are never recycled or renumbered by the management commands. The next number for each type is stored in its type section, so removal does not make an ID available again.

The launcher resolves each worker into its type settings and instance ID before starting panes. It rejects duplicate IDs, missing types or role files, invalid names, and conflicting reserved IDs before creating a session or worktree. It continues to reject unsupported CLIs and missing models. An instance's ID is the mailbox destination and `AGENT_ROLE`. The type is also exposed to the pane as `AGENT_WORKER_TYPE`. Startup instructions identify both values and avoid fixed pane numbers. Worktree paths and branches use the instance ID.

## Worker management

Add `teamflow workers list|add|remove` with the existing `--config <file>` behavior. The `teamflow-workers` skill calls these commands and explains their results. It does not edit INI text directly.

- `list` shows the ordered instance IDs, types, CLI and model, worktree policy, and whether each configured instance has a live pane. It also marks a running configuration that differs from the saved file.
- `add <type>` creates the next unused numbered ID for that type and appends its worker section before the Notetaker. `add notetaker` creates the reserved singleton ID only when absent. It checks that the type exists and reports the new ID. It does not create a pane or worktree until the next team start.
- `remove <id>` removes only that worker section. It refuses when the ID has an assigned or in-progress task or when removal would leave no regular worker. Removing the Notetaker also requires an empty note queue. It preserves task records, conversation history, worktrees, and branches. It reports that a running pane remains until restart.

Commands validate the complete proposed configuration and write it atomically. A failed command leaves the file unchanged. Edits to a running team's configuration take effect after `teamflow --kill` and `teamflow start`. Management commands never stop or restart a team automatically. This follows the existing config hash check.

## Project setup

`teamflow init` ships the new default configuration, role templates, skill, and launcher to adopted projects. As today, it leaves a project's existing `teamflow.conf` untouched. The skill is installed in both `.agents/skills/` and `.claude/skills/`.

## Task routing and Notetaker

The Delegator selects a specific instance ID, then uses it consistently in `task new`, `pane send-to`, and result checks. The role guidance and examples refer to types and instances rather than a fixed role table. Reviewer approval can come from any configured reviewer instance. The Notetaker continues to use its single queue, `notetaker` ID, and one active note task.

## Verification

Test config resolution with multiple instances of one type, singleton and order constraints, and invalid types and IDs. Test add, list, and remove with an active config, a missing type, occupied task, and last-worker guard. Run a launcher smoke test with two instances of one type and verify distinct panes, mailbox destinations, and worktrees. Check that a changed config requires restart and that the Notetaker still processes one released task at a time.
