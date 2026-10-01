# Changes

Newest first. Each version is a git tag `vX.Y.Z`. An entry lists the changes
a user notices and any step a project needs when it moves to that version.

## 0.2.0 (2026-09-30)

Commands instead of built-in CLIs, and a setup that finds them.

- Install with `git clone`, `cd teamflow`, `./install`. A first install runs
  `teamflow setup`, and offers to put `~/.local/bin` on `PATH`.
- `teamflow setup` finds `claude`, `codex`, and aliases or shell functions
  that wrap them, such as `zai-code`. You choose a command and model per
  worker type, and it writes `~/.config/teamflow/teamflow.conf`, which new
  projects start from.
- Setup lists models to pick by number: Codex's models, Claude Code's
  aliases for `claude`, and the models you used before with each command.
  A model check that fails shows the provider's reason and lets you try
  another model.
- `cli =` names any command. Its flavor, `claude` or `codex`, decides the
  flags teamflow passes. Aliases and functions are copied into scripts under
  `~/.local/share/teamflow/commands/` and kept in step with your shell by
  `teamflow update`, `install`, team starts, and `doctor`.
- `cli = zai` and `[workspace] zai_env` are deprecated. They still work in
  0.2.
- Stub skills recommend installing with `./install`.

Moving a project to 0.2.0: nothing is required. To leave `cli = zai`, make a
command that runs Claude Code through z.ai, run `teamflow setup`, and run
`teamflow init` in the project, which offers the change and refreshes the
stub skills.

## 0.1.0 (2026-09-30)

The first version installed once per machine.

- Install with `scripts/teamflow install` from a clone. It installs to
  `~/.local/share/teamflow/<version>` and links `~/.local/bin/teamflow`.
  `teamflow update`, `teamflow version`, and `teamflow doctor` manage it.
- Projects hold only `teamflow.conf`, stub skills, the `AGENTS.md` section,
  and customized roles. Stub skills load their instructions from the
  installed version with `teamflow guide <skill>`. `teamflow role show|edit`
  reads or customizes a role.
- The Delegator uses `teamflow task` and `teamflow pane`, replacing
  `bash .agents/lib/delegator.sh`.
- Workers join on demand (`teamflow add <type>`), each in its own tmux
  window. `teamflow types` lists the catalog with each type's `use_for`.
  `teamflow config` in the skill still starts the default team.
- Adding or removing a worker changes the running session only, unless
  `--save`. A re-added worker resumes its conversation.
- `teamflow trim` removes idle workers, and `teamflow view` shows several
  workers in one tiled window.
- A watcher trims the team a minute after the Delegator's session ends and
  stops it once workers are idle (at most 2 hours), leaving a note for the
  next session.
- `--kill` removes merged worker worktrees, and `teamflow worktrees
  merge|discard` settles the rest.
- Notes belong to the Notetaker alone. `docs/notes/` appears only with one.
- The full-team mode with a Delegator pane is removed. The default session
  prefix is `tf`.

Moving a project to 0.1.0: stop its team with `teamflow --kill`, then run
`teamflow init`. It removes the teamflow files older versions copied into
the project while they are unmodified, keeps edited roles as overrides, and
writes the stub skills. State from before the rename to teamflow (the name
ai-team) is moved on the first command.
