# Installed tool and thin projects

## Intent

Install teamflow once per machine, and keep each project small. A project holds its config, stub skills that load their instructions from the installed tool, and role files only where the user customized them. A tool update reaches every project without copying files into it. Skills exist only in projects that ran `teamflow init`, never at the user level.

Today `init` copies the launcher, libraries, roles, templates, and full skills into every project, and a refresh step keeps unmodified copies in sync with the tool home. The `teamflow` command is a zsh alias to a development checkout, which non-interactive shells, such as the Codex app-server daemon, never see. This design replaces both.

## Where the command lives

The source of teamflow is its git repository, `https://github.com/jaechoidev/teamflow` (private for now). Installs always come from a git checkout, so the version is known. The first install runs the launcher from that checkout:

```
git clone https://github.com/jaechoidev/teamflow ~/code/teamflow
~/code/teamflow/scripts/teamflow install
```

After that, `teamflow` is an ordinary command on `PATH`:

| Path | Contents |
| --- | --- |
| `~/.local/share/teamflow/<version>/` | A full copy of the shipped files: `scripts/`, `.agents/lib/`, `.agents/roles/`, `.agents/doc-templates/`, `skills/`, the config template, `CHANGES.md`, and a `version` file written at install. The layout matches the repository, so the launcher finds its files the way it does today |
| `~/.local/share/teamflow/current` | Link to the active version directory |
| `~/.local/share/teamflow/source` | The checkout the active version came from |
| `~/.local/bin/teamflow` | Link to `current/scripts/teamflow`. It replaces the shell alias |

`XDG_DATA_HOME` moves the data directory, and `install --bin-dir <dir>` moves the link. The launcher resolves its own path through links before it derives the tool home, so it finds the version directory and not `~/.local/bin`.

## Commands

- `teamflow install [--dev]` copies the shipped files from the source it runs from into a new version directory, written to a temporary directory and renamed into place, then switches `current` and creates the command link. Reinstalling the same version replaces it. `--dev` points `current` at the checkout itself, for developing teamflow, so edits apply at once. `install` ends by running `doctor`.
- `teamflow update` runs `git pull` in the recorded checkout, then installs from it.
- `teamflow version` prints the installed version and the source.
- `teamflow doctor` checks what teamflow assumes and says how to fix each gap: tmux 3.2 or newer, git 2.31 or newer, Python 3.8 or newer, the command link on `PATH`, the agent CLIs a config refers to, and, inside a project, its stub skills and required version.
- `teamflow guide <skill>` prints the full instructions of a skill from the installed version.
- `teamflow task ...` and `teamflow pane ...` replace `bash .agents/lib/delegator.sh task|pane ...`. They record the Delegator the same way.

`install` keeps earlier version directories so the user can switch back. It never removes a version that a running team still uses.

## Only workers mode

The Delegator always runs in the user's own agent session. The full-team mode, which ran a Delegator inside tmux, is removed together with `[pane.delegator]`. `up` and `start` both launch workers only, and `--workers` is accepted without effect. `init` removes a `[pane.delegator]` section from a project's config and says so. A team that an older version started in full-team mode is refused until `--kill`.

## Runtime paths

The launcher resolves `current` to its version directory when a team starts. Worker panes, the watcher, and the lib directory (`AGENT_LIB_DIR`) use that resolved path. A running team therefore keeps the version it started with, and switching `current` affects the next start. `teamflow list` and `--verify` report the running team's version.

## Project footprint

Committed to the project:

- `teamflow.conf`, as today, plus `teamflow = <version>` in `[workspace]`: the lowest version the project needs. `init` writes the installed version there, and `doctor` and the stubs compare against it. The default `session_prefix` becomes `tf`, replacing `at`, a leftover from the ai-team name. A project that sets `at` keeps it.
- Stub skills in `.claude/skills/<skill>/SKILL.md` and `.agents/skills/<skill>/SKILL.md`, one per shipped skill.
- A short managed section in `AGENTS.md`: the project uses teamflow, where coordination state lives, and the install recommendation below.
- `.agents/roles/<role>.md`, only for roles the user customized. The launcher looks for a role file in the project first, then in the installed version. `teamflow role edit <role>` copies the default into the project for editing.
- `docs/notes/`, only while a Notetaker is in the team, as today.

Not committed: `.git/teamflow/` (runtime state) and `.teamflow-worktrees/`, as today.

No longer in projects: `scripts/teamflow*`, `.agents/lib/`, `.agents/doc-templates/`, `.agents/AGENTS-SECTION.md`, `.agents/teamflow-manifest`, default role files, and full skill text.

## Stub skills

A stub carries the skill's trigger text and nothing that goes stale:

```
---
name: teamflow
description: <the skill's trigger text>
---

Run `teamflow guide teamflow` and follow the instructions it prints.

If the `teamflow` command is not found, this project uses teamflow
(version <version> or later, see teamflow.conf), but teamflow is not
installed on this machine. Recommend that the user install it:
<install instructions>
Do not install it without the user's approval.
```

A stub changes only when a skill is added, removed, or gets new trigger text. `init` refreshes stubs that are unmodified and stages `.new` files for edited ones, the same rule as today, now applied to stub files only. A skill's full text changes with the installed version and never touches the project.

## Upgrades and version skew

- An installed version older than the project's `teamflow = <version>` makes `doctor`, `list`, and `start` warn: "this project needs teamflow <version> or later. Run teamflow update."
- A newer installed version works with an older project. `init` raises the project's recorded version only when it rewrites stubs.
- A running team keeps its version until it stops, as described under Runtime paths.

## Moving existing projects

Running `teamflow init` with the installed tool in a project that has vendored copies:

1. It refuses while a team started from the vendored copies runs, and explains how to stop it, as the ai-team migration does.
2. It removes vendored files that are unmodified, using the existing check against the manifest and the tool's history.
3. It keeps modified role files as project overrides. It lists any other modified vendored file and leaves it in place for the user to review.
4. It writes the stub skills, the new `AGENTS.md` section, and `teamflow = <version>`.

The removals show up in `git status` for the user to review and commit.

## Out of scope

Separate specs will cover:
- replacing the `zai` CLI with an `env_file` setting on any type
- building the catalog from the agent CLIs `init` detects
- an explicit per-type permission setting
- adapters for more agent CLIs
- packaging as Claude Code and Codex plugins

## Versions and releases

Git tags are the only source of version numbers. There is no `VERSION` file to keep in sync.

- `install` runs `git describe --tags --always --dirty` in the checkout and writes the result to the version directory's `version` file and name. A release gives `v0.1.0`, a commit between releases gives `v0.1.0-4-g1a2b3c4`, and local edits add `-dirty`. Comparisons use the leading `vMAJOR.MINOR.PATCH`.
- Versions follow semantic versioning, starting at 0.1.0. A change to the project footprint or the config format raises at least the minor version before 1.0.
- A release adds an entry to `CHANGES.md` (newest first, user-facing changes and any migration a project needs), creates an annotated tag `vX.Y.Z` on that commit, pushes the tag, and may create a GitHub release with the same notes.
- The first release, v0.1.0, is the first version installable with `teamflow install`.
