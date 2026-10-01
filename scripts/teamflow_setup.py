#!/usr/bin/env python3
"""Find the agent commands on this machine and set teamflow up to use them.

A worker type names a command (cli = <name>): an executable on PATH, a shell
alias, or a shell function. Its flavor (claude or codex) is the flag set
teamflow drives it with. Facts about this machine live in a registry,
<data>/commands.conf. An alias or a function is copied into a script under
<data>/commands/, because worker windows start in a non-interactive shell
that has no aliases or functions. A copy that does not behave like the
original falls back to running through the user's interactive shell.

setup [--yes] [--probe] [--type <type>=<command>[:<model>]]...
    Detect commands, choose one per worker type, and write the registry and
    the user catalog (<config>/teamflow.conf) that teamflow init starts from.
resolve <command> [flavor]
    Print "<flavor>\\t<launch>" for the launcher. launch is exec:<path>,
    script:<path>, interactive:<name>, or zai:<claude path>, or empty.
refresh [--cheap] [--report]
    Compare copied aliases and functions with their definitions. Rewrite
    stale copies, or only report them with --report. --cheap checks the
    shell startup files first and stops when they are unchanged.
offer-zai <config>
    Offer to replace the deprecated cli = zai with a claude-flavored command.
"""

import configparser
import functools
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

SUPPORTED = {"claude": "claude", "codex": "codex"}
OTHER_AGENTS = ("opencode", "cursor-agent", "aider", "gemini", "qwen", "amp", "goose", "crush", "kimi")
MARK = "@@teamflow-probe@@"
TOOL_HOME = Path(__file__).resolve().parents[1]
TEMPLATE = TOOL_HOME / ".agents" / "teamflow.conf"


class SetupError(Exception):
    pass


def data_dir():
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "teamflow"


def config_dir():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "teamflow"


def registry_path():
    return data_dir() / "commands.conf"


def user_catalog():
    return config_dir() / "teamflow.conf"


def say(message):
    print(f"teamflow: {message}", file=sys.stderr)


# ---------- the registry ------------------------------------------------------

def read_registry():
    cp = configparser.ConfigParser(interpolation=None)
    if registry_path().is_file():
        cp.read(registry_path())
    return {s[8:]: dict(cp[s]) for s in cp.sections() if s.startswith("command.")}


def write_registry(entries):
    cp = configparser.ConfigParser(interpolation=None)
    for name in sorted(entries):
        cp[f"command.{name}"] = {k: str(v) for k, v in entries[name].items()}
    registry_path().parent.mkdir(parents=True, exist_ok=True)
    temp = registry_path().with_suffix(".tmp")
    with open(temp, "w") as stream:
        stream.write("# Agent commands on this machine, written by teamflow setup.\n")
        cp.write(stream)
    os.replace(temp, registry_path())


def resolve_command(name, flavor=""):
    """(flavor, launch) for a type's cli. An empty launch means not found."""
    if name == "zai":  # deprecated: claude after sourcing [workspace] zai_env
        path = shutil.which("claude")
        return "zai", f"zai:{path}" if path else ""
    entry = read_registry().get(name)
    if entry:
        flavor = flavor or entry.get("flavor", "")
        if entry.get("run") == "script" and Path(entry.get("script", "")).is_file():
            return flavor, f"script:{entry['script']}"
        if entry.get("run") == "interactive":
            return flavor, f"interactive:{name}"
    path = shutil.which(name)
    return flavor or SUPPORTED.get(name, ""), f"exec:{path}" if path else ""


# ---------- the user's shell --------------------------------------------------

def user_shell():
    shell = os.environ.get("SHELL") or "/bin/sh"
    return shell, Path(shell).name


def startup_files():
    kind = user_shell()[1]
    names = {"zsh": (".zshenv", ".zprofile", ".zshrc", ".zlogin"),
             "bash": (".bashrc", ".bash_profile", ".profile")}.get(kind, (".profile",))
    return [Path.home() / n for n in names]


def startup_hash():
    digest = hashlib.sha256()
    for path in startup_files():
        digest.update(str(path).encode())
        digest.update(path.read_bytes() if path.is_file() else b"-")
    return digest.hexdigest()


def fingerprint(definition):
    return "sha256:" + hashlib.sha256(definition.encode()).hexdigest()


PROBE = {
    "zsh": r'''
print -r -- "@@teamflow-probe@@"
for n in ${(k)aliases}; do print -r -- "alias"$'\t'"$n"$'\t'"$(print -rn -- "${aliases[$n]}" | base64 | tr -d '\n')"; done
for n in ${(k)functions}; do b="$(functions -- "$n")"; case "$b" in *claude*|*codex*) print -r -- "function"$'\t'"$n"$'\t'"$(print -rn -- "$b" | base64 | tr -d '\n')";; esac; done
print -r -- "@@teamflow-probe@@"
''',
    "bash": r'''
echo "@@teamflow-probe@@"
for n in $(compgen -a); do v="$(alias "$n")"; printf 'alias\t%s\t%s\n' "$n" "$(printf '%s' "${v#alias $n=}" | base64 | tr -d '\n')"; done
for n in $(compgen -A function); do b="$(declare -f "$n")"; case "$b" in *claude*|*codex*) printf 'function\t%s\t%s\n' "$n" "$(printf '%s' "$b" | base64 | tr -d '\n')";; esac; done
echo "@@teamflow-probe@@"
''',
}


@functools.lru_cache(maxsize=None)
def shell_definitions():
    """{name: (kind, definition)} for the interactive shell's aliases and the
    functions that mention a supported command."""
    shell, kind = user_shell()
    if kind not in PROBE:
        return {}
    import base64
    try:
        run = subprocess.run([shell, "-ic", PROBE[kind]], capture_output=True, text=True,
                             timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    parts = run.stdout.split(MARK)
    if len(parts) < 3:
        return {}
    found = {}
    for line in parts[1].splitlines():
        fields = line.split("\t")
        if len(fields) != 3:
            continue
        what, name, encoded = fields
        try:
            text = base64.b64decode(encoded).decode()
        except ValueError:
            continue
        if what == "alias" and kind == "bash":
            try:
                words = shlex.split(text)  # bash prints the value quoted: 'claude --x'
            except ValueError:
                words = []
            text = words[0] if len(words) == 1 else text
        found[name] = (what, text)
    return found


# A wrapper calls claude or codex as a command (not, say, a ~/.claude path or
# a CLAUDE_* variable) and passes the worker's arguments through. Setup only
# runs a function or alias with --version when its definition looks like
# that, so it never executes an unrelated shell function on its own.
CALLS_AGENT = re.compile(r"(?:^|[\s;&|(`])(?:command\s+|exec\s+|builtin\s+)?(?:claude|codex)(?=[\s;&|)\"']|$)", re.M)
PASSES_ARGS = re.compile(r"\$[@*]|\$\{[@*]")


def looks_like_wrapper(kind, text):
    if not CALLS_AGENT.search(" " + text):
        return False
    return kind == "alias" or bool(PASSES_ARGS.search(text))


def version_of(launch, name):
    """The command's --version output, run the way a worker would run it."""
    shell = user_shell()[0]
    if launch.startswith("interactive:"):
        argv = [shell, "-ic", f"{name} --version"]
    else:
        argv = [launch.split(":", 1)[1], "--version"]
    try:
        run = subprocess.run(argv, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (run.stdout + run.stderr).strip()


def flavor_of(version):
    if "(Claude Code)" in version:
        return "claude"
    if "codex-cli" in version:
        return "codex"
    return ""


def version_line(version):
    for line in version.splitlines():
        if "(Claude Code)" in line or "codex-cli" in line:
            return line.strip()
    return version.splitlines()[0].strip() if version else ""


def write_script(name, kind, definition):
    """Copy an alias or function into a script a non-interactive shell can run."""
    shell = user_shell()[0]
    body = f'{definition} "$@"\n' if kind == "alias" else f'{definition}\n{name} "$@"\n'
    path = data_dir() / "commands" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{shell}\n# Copied by teamflow setup from the {kind} {name} in your shell. "
                    f"teamflow rewrites it when the {kind} changes.\n{body}")
    path.chmod(0o755)
    return path


def register_definition(name, kind, definition, interactive_version):
    """Copy an alias or function, verify the copy, and return its registry entry."""
    script = write_script(name, kind, definition)
    copied = version_of(f"script:{script}", name)
    entry = {"flavor": flavor_of(interactive_version), "kind": kind, "script": str(script),
             "fingerprint": fingerprint(definition), "startup_hash": startup_hash(),
             "version": version_line(interactive_version)}
    if flavor_of(copied) and version_line(copied) == version_line(interactive_version):
        entry["run"] = "script"
    else:
        entry["run"] = "interactive"
        say(f"the copy of {name} does not run like the {kind} does, so its workers start through "
            f"your interactive shell. That loads your shell startup files in each worker, "
            f"which is slower and lets them export variables into it")
    return entry


# ---------- detection ---------------------------------------------------------

def detect(extra=(), scan=True):
    """Supported commands found, as {name: entry}, and other agent CLIs found.
    scan=False checks only the extra names."""
    found = {}
    for name in SUPPORTED if scan else ():
        path = shutil.which(name)
        if path:
            version = version_of(f"exec:{path}", name)
            if flavor_of(version):
                found[name] = {"flavor": flavor_of(version), "kind": "executable", "run": "exec",
                               "path": path, "version": version_line(version)}
    definitions = shell_definitions()
    wanted, skipped = [], []
    for n, (kind, text) in definitions.items():
        if not scan or n in SUPPORTED or not re.search(r"\b(claude|codex)\b", text):
            continue
        (wanted if looks_like_wrapper(kind, text) else skipped).append(n)
    if skipped:
        say(f"skipped {', '.join(sorted(skipped))}: they mention claude or codex but do not look like wrappers "
            f"that pass arguments to it. Add one by name to include it")
    for name in list(wanted) + [n for n in extra if n not in found]:
        if name in found:
            continue
        if name in definitions:
            kind, text = definitions[name]
            version = version_of(f"interactive:{name}", name)
            if flavor_of(version):
                found[name] = register_definition(name, kind, text, version)
            else:
                say(f"{name} is a shell {kind}, but `{name} --version` does not report Claude Code or Codex")
        elif shutil.which(name):
            version = version_of(f"exec:{shutil.which(name)}", name)
            if flavor_of(version):
                found[name] = {"flavor": flavor_of(version), "kind": "executable", "run": "exec",
                               "path": shutil.which(name), "version": version_line(version)}
            else:
                say(f"`{name} --version` does not report Claude Code or Codex, so teamflow cannot drive it yet")
        else:
            say(f"{name} is not a command, alias, or function here")
    others = [n for n in OTHER_AGENTS if scan and shutil.which(n)]
    return found, others


# ---------- keeping copies current ----------------------------------------------

def refresh(cheap=False, report=False):
    entries = read_registry()
    copied = {n: e for n, e in entries.items() if e.get("kind") in ("alias", "function")}
    if not copied:
        return 0
    current_hash = startup_hash()
    if cheap and all(e.get("startup_hash") == current_hash for e in copied.values()):
        return 0
    definitions = shell_definitions()
    stale = 0
    for name, entry in copied.items():
        if name not in definitions:
            say(f"{name} is no longer an alias or function in your shell. Workers that use it cannot start. Run teamflow setup")
            stale += 1
            continue
        kind, text = definitions[name]
        if fingerprint(text) == entry.get("fingerprint"):
            entry["startup_hash"] = current_hash
            continue
        stale += 1
        if report:
            say(f"{name} changed since teamflow copied it. teamflow update, a team start, or teamflow setup refreshes the copy")
            continue
        version = version_of(f"interactive:{name}", name)
        if not flavor_of(version):
            say(f"{name} changed and no longer reports Claude Code or Codex. Its old copy stays. Run teamflow setup")
            continue
        entries[name] = register_definition(name, kind, text, version)
        say(f"{name} changed in your shell, so teamflow refreshed its copy")
    if not report:
        write_registry(entries)
    return stale


# ---------- choosing and writing the user catalog -------------------------------

def catalog_types(text):
    cp = configparser.ConfigParser(interpolation=None)
    cp.read_string(text)
    return {s[5:]: dict(cp[s]) for s in cp.sections() if s.startswith("type.")}


def set_option(text, section, option, value):
    lines = text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if l.strip() == f"[{section}]"), None)
    if start is None:
        return text
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    for i in range(start + 1, end):
        if re.match(rf"\s*{re.escape(option)}\s*=", lines[i]):
            lines[i] = f"{option} = {value}\n"
            return "".join(lines)
    lines.insert(start + 1, f"{option} = {value}\n")
    return "".join(lines)


def drop_section(text, section):
    lines = text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if l.strip() == f"[{section}]"), None)
    if start is None:
        return text
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    return "".join(lines[:start] + lines[end:])


def ask(question, default=""):
    try:
        answer = input(f"{question} [{default}] > " if default else f"{question} > ").strip()
    except EOFError:
        return default
    return answer or default


def probe_model(name, entry_launch, model):
    shell = user_shell()[0]
    if entry_launch.startswith("interactive:"):
        argv = [shell, "-ic", f"{name} --model {shlex.quote(model)} --print 'reply ok'"]
    else:
        argv = [entry_launch.split(":", 1)[1], "--model", model, "--print", "reply ok"]
    try:
        run = subprocess.run(argv, capture_output=True, text=True, timeout=180, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return run.returncode == 0 and "ok" in run.stdout.lower()


def setup(args):
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    yes = "--yes" in args
    choices = {}
    extra = []
    for value in [a.split("=", 1)[1] for a in args if a.startswith("--type=")] + \
            [args[i + 1] for i, a in enumerate(args) if a == "--type" and i + 1 < len(args)]:
        kind, _, rest = value.partition("=")
        command, _, model = rest.partition(":")
        choices[kind] = (command, model)
        extra.append(command)
    say("looking for agent commands (this runs each one with --version)")
    found, others = detect(extra)

    def show(names):
        print("\nAgent commands found:")
        for i, name in enumerate(names, 1):
            e = found[name]
            how = {"executable": "", "alias": " (alias, copied)", "function": " (function, copied)"}[e["kind"]]
            if e.get("run") == "interactive":
                how = f" ({e['kind']}, runs through your interactive shell)"
            print(f"  {i}. {name:14} {e.get('version', '')}{how}")
        if others:
            print(f"Also found, not supported yet: {', '.join(others)}")

    def ordered():
        return sorted(found, key=lambda n: (n not in SUPPORTED, n))

    names = ordered()
    show(names)
    if interactive:
        while True:
            name = ask("\nAdd another command by name (Enter to continue)")
            if not name:
                break
            more, _ = detect([name], scan=False)
            if more:
                found.update(more)
                names = ordered()
                show(names)
    if not names:
        raise SetupError("no supported agent command found. Install Claude Code (claude) or Codex (codex), then run teamflow setup")
    if not interactive and not yes and not choices:
        print("\nNo terminal to ask in. Run teamflow setup in a terminal, or pass --yes "
              "(take the defaults) or --type <type>=<command>[:<model>].")
        return 1

    base = user_catalog().read_text() if user_catalog().is_file() else TEMPLATE.read_text()
    types = catalog_types(base)
    text = base
    chosen = {}
    print()
    for kind, item in types.items():
        default_cmd = item.get("cli", "claude")
        if default_cmd not in found:
            default_cmd = "claude" if "claude" in found else names[0]
        command, model = choices.get(kind, ("", ""))
        if interactive and kind not in choices:
            answer = ask(f"{kind:12} command (number, name, or s to skip)", default_cmd)
            if answer.lower() == "s":
                command = ""
            elif answer.isdigit() and 1 <= int(answer) <= len(names):
                command = names[int(answer) - 1]
            else:
                command = answer
            if command:
                model = ask(f"{'':12} model", item.get("model", ""))
        elif kind not in choices:
            command, model = default_cmd, item.get("model", "")
        if not command:
            text = drop_section(text, f"type.{kind}")
            for section in re.findall(rf"(?m)^\[worker\.({re.escape(kind)}(?:-\d+)?)\]", text):
                text = drop_section(text, f"worker.{section}")
            continue
        if command not in found:
            raise SetupError(f"{command} is not one of the agent commands found")
        model = model or item.get("model", "")
        text = set_option(text, f"type.{kind}", "cli", command)
        text = set_option(text, f"type.{kind}", "model", model)
        chosen[kind] = (command, model)
    if not chosen:
        raise SetupError("every worker type was skipped, so there is no catalog to write")
    if not any(c == "zai" for c, _ in chosen.values()):
        text = re.sub(r"(?m)^zai_env\s*=.*\n", "", text)

    probe = "--probe" in args or (interactive and ask("Check each chosen model with one tiny request? (spends a little quota) y/N", "n").lower() == "y")
    if probe:
        for kind, (command, model) in chosen.items():
            flavor, launch = found[command]["flavor"], ""
            if flavor != "claude":
                print(f"  {kind:12} {command:14} {model:12} not probed (Codex models are checked at first use)")
                continue
            e = found[command]
            launch = f"script:{e['script']}" if e.get("run") == "script" else \
                f"interactive:{command}" if e.get("run") == "interactive" else f"exec:{e['path']}"
            ok = probe_model(command, launch, model)
            print(f"  {kind:12} {command:14} {model:12} {'OK' if ok else 'UNAVAILABLE'}")

    registry = read_registry()
    registry.update(found)
    write_registry(registry)
    user_catalog().parent.mkdir(parents=True, exist_ok=True)
    user_catalog().write_text(text)
    print(f"\nWrote {user_catalog()}. New projects start from it at `teamflow init`.")
    print(f"Wrote {registry_path()}: the commands on this machine.")
    print("Next: in a project, run `teamflow init`, then use the teamflow skill in your agent.")
    return 0


def offer_zai(config):
    path = Path(config)
    if not path.is_file() or not re.search(r"(?m)^cli\s*=\s*zai\s*$", path.read_text()):
        return
    candidates = [n for n, e in read_registry().items() if e.get("flavor") == "claude" and n != "claude"]
    if not candidates:
        say("this project's config uses cli = zai, which is deprecated. Make a command that runs Claude Code "
            "through z.ai (for example zai-code), run teamflow setup, then set cli = <command>")
        return
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        say(f"this project's config uses cli = zai, which is deprecated. Replace it with cli = {candidates[0]} "
            f"(run teamflow init in a terminal to be asked)")
        return
    answer = ask(f"This config uses the deprecated cli = zai. Replace it with cli = {candidates[0]}? y/N", "n")
    if answer.lower() == "y":
        path.write_text(re.sub(r"(?m)^cli\s*=\s*zai\s*$", f"cli = {candidates[0]}", path.read_text()))
        say(f"replaced cli = zai with cli = {candidates[0]} in {path.name}")


def main(argv):
    command, args = (argv[0], argv[1:]) if argv else ("", [])
    if command == "setup":
        return setup(args)
    if command == "resolve" and args:
        flavor, launch = resolve_command(args[0], args[1] if len(args) > 1 else "")
        print(f"{flavor}\t{launch}")
        return 0
    if command == "refresh":
        stale = refresh(cheap="--cheap" in args, report="--report" in args)
        return 1 if stale and "--report" in args else 0
    if command == "offer-zai" and args:
        offer_zai(args[0])
        return 0
    raise SetupError("usage: see the header of teamflow_setup.py")


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (SetupError, OSError) as exc:
        print(f"teamflow: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nteamflow: setup cancelled, nothing was written", file=sys.stderr)
        sys.exit(130)
