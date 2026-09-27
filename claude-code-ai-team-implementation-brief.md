# Build a six-agent tmux workspace for this project

Implement this in the **current project repository**. First inspect the repository, existing `AGENTS.md` or `CLAUDE.md`, Git state, and the installed versions and `--help` output of `tmux`, Codex CLI, Claude Code, and the Z.AI CLI. Adapt to the existing project and preserve unrelated work. Do not treat the example CLI names, model names, or flags below as verified command syntax.

## Goal

I want one project-local command, such as `./scripts/ai-team`, to open or reattach to one tmux session containing **six panes in two columns and three rows**. Each pane runs a persistent CLI agent with a distinct role. The top-left Codex pane is my primary voice interface: I speak to it, it sends assignments to the other panes, receives their results, and reports back to me. I may also address any worker pane directly.

Arrange the panes in this exact reading order:

| Row | Left pane | Right pane |
| --- | --- | --- |
| 1 | **Delegator** — Codex CLI, voice interface | **Researcher** — Z.AI CLI, GLM 5.3, maximum available research/reasoning setting |
| 2 | **Reviewer & Planner** — Claude Code, Fable 5.1, maximum effort | **Dev Senior** — Claude Code, Fable 5.1, maximum effort |
| 3 | **Dev Mid** — Claude Code, Opus 5.5, maximum effort | **Dev Junior** — Z.AI CLI, GLM 5.3, maximum effort |

The model names and effort settings express my intended configuration. Verify the installed tools' exact model IDs, supported flags, and research/effort modes. Make these selections easy to change in one configuration file. If a requested model or mode is unavailable, report precisely what is unavailable and how I can configure an alternative; do not silently substitute another model. The launcher should not require API keys in source files.

## Agent responsibilities

- **Delegator:** My main conversational interface. Relay requests to the appropriate worker and relay results back. Keep its own searching, coding, and deep planning to a minimum. It may maintain coordination documents and write documents that I explicitly request. When I explicitly ask it to do a task itself, it may do so. It should not claim that another agent completed work until it has observed an actual result. Preserve Codex CLI's supported voice interaction; verify how it is entered on the installed version and give me the exact startup step if it cannot be enabled automatically.
- **Researcher:** Investigate external sources, papers, APIs, and technical questions. Return concise findings, links/citations, uncertainty, and actionable options. Use the requested maximum research mode if the CLI supports it. Do not edit production code by default.
- **Reviewer & Planner:** Design architecture, break down work, write plans and decisions, and review implementation and test evidence. By default, review rather than implement.
- **Dev Senior:** Implement complex, performance-sensitive, or architecture-sensitive tasks; investigate difficult bugs; coordinate proposed architecture changes with the planner.
- **Dev Mid:** Implement ordinary features, fixes, integrations, and relevant tests.
- **Dev Junior:** Implement bounded tasks, tests, reproductions, documentation, and mechanical changes; surface ambiguity instead of guessing at architectural decisions.

Roles describe the default routing, not rigid permission barriers. Explicit requests from me can override the default assignment. Workers should only start a new task when I or the delegator assigns it; opening six panes should not consume model usage by sending unnecessary startup chat messages.

## Repository files and startup

Create or update a short root `AGENTS.md` for shared project facts and coordination rules. Preserve the existing project's instructions. Put each role's detailed instructions in its own file under `.agents/roles/`. Use a thin `CLAUDE.md` only if required by the installed Claude Code version or the repository's current convention; avoid duplicated, conflicting instructions.

The launcher must assign a unique role ID and explicit role-file path to each pane, for example with `AGENT_ROLE`, `AGENT_ID`, and `AGENT_ROLE_FILE`. **Environment variables and Markdown links alone do not prove a CLI read the role file.** Provide a reliable initialization mechanism supported by each installed CLI and verify that every pane actually received its own role instructions. Do not let each agent choose a role from a list. Keep shared instructions, role instructions, and model/CLI configuration separate.

Add a brief project-specific `README` or setup section that explains prerequisites, the single launch command, pane mapping, starting voice mode, dispatching a task, reading results, resuming the session, and stopping it. If this directory is not a Git repository or required CLIs are missing, give a clear diagnostic rather than creating a misleading partial session.

## Task routing and handoffs

Provide a practical first version of delegator-to-worker communication using tmux and a small shared task mailbox. The delegator should be able to address a worker by stable role or pane ID, send a task with a unique ID, and read a response or status tied to that ID. Avoid relying on blind sleeps or terminal text alone as proof of completion. Sending text into a pane must handle shell quoting, multiline content, pane focus, and the possibility that the target CLI is busy. Document any manual intervention required. Never let multiple agents overwrite a single shared `research.md` or `review.md`; use separate task records or otherwise handle concurrent writes safely.

Make the live mailbox available to **all worktrees immediately**, independently of Git commits or merges. For example, use a nontracked directory under Git's common directory, with absolute paths passed to agents. Ordinary files edited in one worktree are not automatically visible in the others. Keep durable architecture decisions and user-approved project documents in tracked repository files, and explain when/how task results are promoted there. Do not expose secrets in task logs; keep logs concise and provide a way to inspect or clean old task records.

The delegator should choose a worker based on the responsibilities above, send a concrete assignment with expected output, and summarize the worker's real result to me. It should ask me for missing product decisions when needed. Direct user commands to a worker should still work.

## Concurrent coding and Git

The three developer panes should use **separate Git worktrees and branches**, created or reused safely by the launcher. The delegator, researcher, and reviewer/planner may use the main project checkout unless existing changes or project conventions call for another arrangement. Name the worktrees and branches predictably, and avoid deleting or resetting any existing branch, worktree, or user changes. Do not automatically merge, commit, or push. Explain how the reviewer can inspect each developer's diff and how approved changes can be integrated without overwriting concurrent work. Give each task a clear ownership scope; avoid assigning overlapping edits concurrently unless explicitly coordinated.

## Launcher behavior and acceptance checks

- The one-command launcher works from any directory inside this repository, uses the correct absolute project paths, and reattaches to an existing session rather than launching duplicates. Make the tmux session name unique enough to avoid collisions with other projects.
- Panes have readable role labels; the exact two-column, three-row layout and order above remain stable. All six panes start in the intended checkout/worktree with the intended CLI and role instructions.
- Check CLI and model availability before launching where feasible. On failure, show an actionable error and avoid orphaned panes or unwanted worktrees. Do not use hard-coded user-specific paths.
- Safely quote paths with spaces and task text. Use shell scripts compatible with the project's target platform; note any macOS/Linux differences. Never assume that `tmux send-keys` alone guarantees successful delivery or task completion.
- Verify script syntax and exercise the layout, role routing, and mailbox with stub CLI commands where possible, without spending paid model quota. Then explain which live checks require my installed CLIs or credentials.
- At the end, show the files changed, the exact command to run, the resulting pane map, what you actually verified, and any unsupported or still-manual voice/model settings.

Please implement the setup now. Make reasonable project-specific choices, keep the first version small and usable, and document any CLI capability you could not verify rather than inventing a flag.
