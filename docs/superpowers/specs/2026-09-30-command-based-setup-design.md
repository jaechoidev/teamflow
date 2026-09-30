# Command-based setup

## Intent

Install and configure teamflow with one deterministic script that adapts to the machine. Every worker type names a command the user already runs: `claude`, `codex`, or a wrapper such as `zai-code` that routes Claude Code to another provider. Setup finds those commands, lets the user choose one per worker type, and writes a user-level catalog that new projects start from. teamflow never writes environment files and never reads provider secrets. Those stay in the user's own commands.

This replaces the special `cli = zai` type and `[workspace] zai_env`. It also replaces the idea of an installer prompt run by an agent: once providers are plain commands, setup needs no judgment, and a script is faster, repeatable, and never routes secrets through a model.

## Install entry point

The README's install becomes:

```
git clone https://github.com/jaechoidev/teamflow
cd teamflow
./install
```

`./install` is a script at the repository root that runs `scripts/teamflow install "$@"`, so `./install --dev` works too. Install behaves as today, plus:

- It checks that the command link's directory, `~/.local/bin` by default, is on `PATH`. If it is not, it shows the `export PATH=...` line and offers to append it to the user's shell profile. It asks first, and changes nothing without a yes.
- When no user catalog exists yet and a terminal is attached, it runs `teamflow setup`.
- The clone stays where it is. `teamflow update` pulls it.

## Commands and flavors

A type names its command with `cli = <name>`. The name can be an executable on `PATH`, a shell alias, or a shell function. teamflow drives a command through its flavor, the flag set it understands:

| Flavor | Detected when `<command> --version` prints | Worker launch |
| --- | --- | --- |
| `claude` | `(Claude Code)` | `--session-id` or `--resume`, `--model`, `--effort`, `--append-system-prompt-file` |
| `codex` | `codex-cli` | as the launcher starts Codex today |

Commands of other agents, such as `opencode`, `cursor-agent`, `aider`, and `gemini`, are reported as found but not supported yet. For every command of the `claude` flavor, the launcher clears the Anthropic routing variables before starting it, as it does for `claude` today, so a wrapper sets its own routing and a plain `claude` never inherits one.

A type may set `flavor =` explicitly. Otherwise the flavor comes from the machine's command registry, and `claude` and `codex` need no registry entry.

## The command registry

Whether a command is an executable, an alias, or a function is a fact about one machine, and project configs are committed and shared. So setup records each command it detected in a per-machine registry, `~/.local/share/teamflow/commands.conf`:

```ini
[command.zai-code]
flavor = claude
kind = function
run = script
script = ~/.local/share/teamflow/commands/zai-code
fingerprint = sha256:...
```

A project's `teamflow.conf` only names the command. When the launcher starts a worker, it looks the command up in the registry, then on `PATH`. A command it cannot find stops the launch with a message to run `teamflow setup`.

## Aliases and functions

Worker windows start their command in a non-interactive shell, which never reads the user's shell startup files, so aliases and functions do not exist there. Setup handles each kind:

- **Executable on `PATH`**: used as is.
- **Alias**: setup writes a script into `~/.local/share/teamflow/commands/<name>` that runs the alias's expansion with the worker's arguments.
- **Function**: setup writes a script with the user's shell as its interpreter, containing the function's definition and a call to it with the worker's arguments.

Setup verifies each script: run the way a worker runs it, `--version` must print the same output as the alias or function prints in the user's interactive shell. If a script fails that check, for example because the function depends on other definitions in the startup files, the registry records `run = interactive`. That command's workers then start through the user's interactive shell, `$SHELL -ic`, and setup says so, including the costs: a slower start, startup-file output in the worker's window, and any routing variables the startup files export.

The scripts live in a directory teamflow owns, not in `~/.local/bin`, so they never shadow the user's own commands. The user's shell startup files are never changed. The registry keeps a fingerprint of each copied definition. `teamflow doctor` compares it with the current definition and warns when the alias or function changed since setup: "zai-code changed. Run teamflow setup again."

## Setup

`teamflow setup` runs at the end of the first install and any time after:

1. **Find commands.** It checks the supported names on `PATH`, lists the aliases and functions of the user's interactive shell whose definitions call a supported command, and accepts more names from the user. For each, it records the kind and detects the flavor with `--version`.
2. **Choose per worker type.** For each type in the shipped catalog (researcher, reviewer, developer-l, developer-m, developer-s, notetaker), the user picks a command, or skips the type, and a model. The shipped catalog's model for that type is the default. Effort, worktree policy, role, and `use_for` come from the shipped catalog unchanged.
3. **Optionally probe.** It offers to check each chosen model with one tiny request, as `--check-models` does, and asks first because that spends a little quota.
4. **Write.** It writes the command registry, the scripts for aliases and functions, and the user catalog.

Without a terminal, setup prompts for nothing. It prints what it found and exits unless it gets `--yes`, which takes the first supported command for every type and the shipped default models, or choices as flags, such as `--type researcher=zai-code:glm-5.3`. An agent can run it that way.

## The user catalog

Setup writes `~/.config/teamflow/teamflow.conf` (under `XDG_CONFIG_HOME`). It has the shipped structure with the user's commands and models, and the shipped default team limited to the types the user kept. `teamflow init` copies it into a new project in place of the shipped template. Projects that already have a `teamflow.conf` keep it. Running setup again updates the user catalog for future projects only.

## Moving from `cli = zai`

`cli = zai` and `[workspace] zai_env` keep working in 0.2 as they do today, and `teamflow doctor` warns that they are deprecated. When the user catalog has a `claude`-flavored command, such as `zai-code`, `teamflow init` offers to replace `cli = zai` in the project's config with it, and asks first. A later version removes `zai`.

## Versions

This changes the config format, so it ships as 0.2.0, with a `CHANGES.md` entry that describes the move from `cli = zai`.

## Out of scope

- Flavors for more agent CLIs, such as `opencode`, `cursor-agent`, `aider`, and `gemini`
- Installing the agent CLIs themselves
- A `curl ... | sh` installer, which needs a public repository
