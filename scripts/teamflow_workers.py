#!/usr/bin/env python3
"""Resolve teamflow worker types and manage configured instances."""

import argparse
import configparser
import hashlib
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

SEP = "\x1f"
SLUG = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
LEGACY = {"researcher", "reviewer", "dev-senior", "dev-mid", "dev-junior"}
RESERVED = {"delegator", "notetaker"}
TYPE_FOR_LEGACY = {"dev-senior": "developer-l", "dev-mid": "developer-m", "dev-junior": "developer-s"}


def role_file_for_type(kind):
    return "developer.md" if kind in TYPE_FOR_LEGACY.values() else f"{kind}.md"


class ConfigError(Exception):
    pass


def read_config(path):
    cp = configparser.ConfigParser(interpolation=None)
    try:
        with open(path, encoding="utf-8") as stream:
            cp.read_file(stream)
    except (OSError, configparser.Error) as exc:
        raise ConfigError(str(exc)) from exc
    return cp


def read_config_from_text(content):
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read_file(io.StringIO(content))
    except configparser.Error as exc:
        raise ConfigError(str(exc)) from exc
    return cp


def sections(cp, prefix):
    return [section for section in cp.sections() if section.startswith(prefix)]


def valid_field(value, label):
    if any(char in value for char in (SEP, "\n", "\r")):
        raise ConfigError(f"{label} contains a control separator")
    return value


def resolve(cp):
    modern = bool(sections(cp, "type.") or sections(cp, "worker."))
    workspace = cp["workspace"] if cp.has_section("workspace") else {}
    prefix = valid_field(workspace.get("session_prefix", "at"), "session prefix")
    zai_env = valid_field(workspace.get("zai_env", "~/.zai/env.sh"), "zai env")
    rows = []
    if modern:
        for section in sections(cp, "pane."):
            if section != "pane.delegator":
                raise ConfigError(f"{section} cannot be mixed with worker instances")
        types = {}
        for section in sections(cp, "type."):
            name = section[5:]
            if not SLUG.fullmatch(name) or name == "delegator":
                raise ConfigError(f"invalid worker type: {name}")
            types[name] = cp[section]
        if cp.has_section("pane.delegator"):
            rows.append(pane_row("delegator", cp["pane.delegator"]))
        workers = sections(cp, "worker.")
        if not workers:
            raise ConfigError("config requires worker instances")
        for index, section in enumerate(workers):
            worker_id = section[7:]
            item = cp[section]
            if set(item.keys()) != {"type"}:
                raise ConfigError(f"{section} may contain only type")
            kind = item.get("type", "")
            if kind not in types:
                raise ConfigError(f"{section} refers to missing type {kind!r}")
            if kind == "notetaker":
                if worker_id != "notetaker" or index != len(workers) - 1:
                    raise ConfigError("the single notetaker must be worker.notetaker and last")
            elif worker_id != "notetaker":
                if not SLUG.fullmatch(worker_id) or not re.fullmatch(re.escape(kind) + r"-[1-9][0-9]*", worker_id):
                    raise ConfigError(f"invalid instance ID {worker_id!r} for type {kind}")
            else:
                raise ConfigError("worker.notetaker must use type notetaker")
            definition = types[kind]
            role_file = definition.get("role_file", "")
            if not role_file or Path(role_file).is_absolute() or ".." in Path(role_file).parts:
                raise ConfigError(f"type {kind} needs a safe relative role_file")
            rows.append(pane_row(worker_id, definition, kind, role_file))
        if not any(row[0] not in RESERVED for row in rows):
            raise ConfigError("config requires at least one regular worker")
    else:
        for section in sections(cp, "pane."):
            role = section[5:]
            if role not in LEGACY | RESERVED:
                raise ConfigError(f"unknown pane role {role!r}")
            rows.append(pane_row(role, cp[section]))
    ids = [row[0] for row in rows]
    if len(ids) != len(set(ids)):
        raise ConfigError("duplicate worker ID")
    for row in rows:
        if row[3] == "":
            raise ConfigError(f"{row[0]} has no model")
        if row[2] not in {"claude", "codex", "zai"}:
            raise ConfigError(f"{row[0]} has unsupported cli {row[2]!r}")
        if row[5] not in {"yes", "no"}:
            raise ConfigError(f"{row[0]} worktree must be yes or no")
    return modern, prefix, zai_env, rows


def pane_row(worker_id, item, kind="", role_file=""):
    label = item.get("label", kind or worker_id)
    if kind and worker_id != "notetaker":
        label = f"{label} ({worker_id})"
    fields = [worker_id, label, item.get("cli", ""), item.get("model", ""),
              item.get("effort", ""), item.get("worktree", "no"), kind, role_file]
    return [valid_field(field, worker_id) for field in fields]


def atomic_write(path, content):
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.chmod(temp, path.stat().st_mode & 0o777)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def validated_write(path, content):
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.check.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        resolve(read_config(temp))
    finally:
        os.unlink(temp)
    atomic_write(path, content)


def replace_section(content, name, replacement):
    lines = content.splitlines(keepends=True)
    pattern = re.compile(r"^\[([^]]+)\]\s*$")
    start = end = None
    for index, line in enumerate(lines):
        match = pattern.match(line.strip())
        if match and match.group(1) == name:
            start = index
        elif match and start is not None:
            end = index
            break
    if start is None:
        raise ConfigError(f"missing section [{name}]")
    if end is None:
        end = len(lines)
    return "".join(lines[:start]) + replacement + "".join(lines[end:])


def update_option(content, section, option, value):
    lines = content.splitlines(keepends=True)
    header = f"[{section}]"
    start = next((i for i, line in enumerate(lines) if line.strip() == header), None)
    if start is None:
        raise ConfigError(f"missing section {header}")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[")), len(lines))
    for i in range(start + 1, end):
        if re.match(rf"^\s*{re.escape(option)}\s*=", lines[i]):
            lines[i] = f"{option} = {value}\n"
            break
    else:
        lines.insert(start + 1, f"{option} = {value}\n")
    return "".join(lines)


def task_destinations(root):
    tasks = root / ".git" / "ai-team" / "tasks"
    for task in tasks.glob("T-*/task.md"):
        for line in task.read_text(errors="replace").splitlines():
            if line.startswith("to:      "):
                yield task.parent, line[9:]
                break


def active_panes(root):
    mailbox = root / ".git" / "ai-team"
    mapping = {}
    registry = mailbox / "panes.tsv"
    if registry.exists():
        for line in registry.read_text().splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                mapping[parts[0]] = parts[1]
    try:
        result = subprocess.run(["tmux", "list-panes", "-a", "-F", "#{pane_id}"],
                                capture_output=True, text=True, check=False)
        alive = set(result.stdout.splitlines())
    except FileNotFoundError:
        alive = set()
    return {worker_id: pane for worker_id, pane in mapping.items() if pane in alive}


def list_workers(cp, root, path):
    modern, _, _, rows = resolve(cp)
    panes = active_panes(root)
    configured = {row[0] for row in rows}
    for worker_id, label, cli, model, _, worktree, kind, _ in rows:
        if worker_id == "delegator":
            continue
        print(f"{worker_id}\t{kind or worker_id}\t{cli}\t{model}\t{worktree}\t{panes.get(worker_id, 'stopped')}")
    for worker_id, pane in panes.items():
        if worker_id not in configured and worker_id != "delegator":
            print(f"{worker_id}\tremoved from config\t\t\t\t{pane}")
    active = root / ".git" / "ai-team" / "active"
    if active.exists() and panes:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if f"CONFIG_HASH={digest}" not in active.read_text():
            print("saved config differs from the running team. Restart to apply it", file=sys.stderr)
    if not modern:
        print("legacy config: use 'teamflow workers migrate --dry-run' before add or remove", file=sys.stderr)


def add_worker(cp, root, path, kind):
    modern, _, _, _ = resolve(cp)
    if not modern:
        raise ConfigError("legacy config: run workers migrate first")
    if not cp.has_section(f"type.{kind}"):
        raise ConfigError(f"unknown worker type: {kind}")
    role_file = root / cp[f"type.{kind}"].get("role_file", "")
    if not role_file.is_file():
        raise ConfigError(f"missing role file for {kind}: {role_file}")
    content = path.read_text()
    if kind == "notetaker":
        worker_id = "notetaker"
        if cp.has_section("worker.notetaker"):
            raise ConfigError("notetaker already exists")
    else:
        definition = cp[f"type.{kind}"]
        current = [int(section.rsplit("-", 1)[1]) for section in sections(cp, "worker.")
                   if re.fullmatch(r"worker\." + re.escape(kind) + r"-[1-9][0-9]*", section)]
        try:
            next_id = int(definition.get("next_id", "1"))
        except ValueError as exc:
            raise ConfigError(f"type {kind} has invalid next_id") from exc
        number = max(next_id, max(current, default=0) + 1)
        worker_id = f"{kind}-{number}"
        content = update_option(content, f"type.{kind}", "next_id", number + 1)
    insertion = f"\n[worker.{worker_id}]\ntype = {kind}\n"
    if cp.has_section("worker.notetaker") and kind != "notetaker":
        match = re.search(r"(?m)^\s*\[worker\.notetaker\]\s*$", content)
        if not match:
            raise ConfigError("cannot locate worker.notetaker section")
        index = match.start()
        content = content[:index] + insertion + content[index:]
    else:
        content = content.rstrip() + insertion
    validated_write(path, content)
    print(f"added {worker_id}. Restart the team to launch it")


def remove_worker(cp, root, path, worker_id):
    modern, _, _, rows = resolve(cp)
    if not modern:
        raise ConfigError("legacy config: run workers migrate first")
    section = f"worker.{worker_id}"
    if not cp.has_section(section):
        raise ConfigError(f"worker not found: {worker_id}")
    if worker_id != "notetaker" and sum(row[0] not in RESERVED for row in rows) <= 1:
        raise ConfigError("cannot remove the last regular worker")
    for task, destination in task_destinations(root):
        if destination == worker_id and (task / "status").read_text().strip() in {"assigned", "in-progress"}:
            raise ConfigError(f"{worker_id} has unfinished task {task.name}")
    if worker_id == "notetaker":
        queue = root / ".git" / "ai-team" / "note-queue"
        if (queue / "active").exists() or any((task / "released").exists() for task, _ in task_destinations(root)):
            raise ConfigError("notetaker queue is not empty")
    content = replace_section(path.read_text(), section, "")
    validated_write(path, content)
    print(f"removed {worker_id} from config. Restart the team to close its pane")
    print("conversation, branch, and worktree were preserved")


def check_stopped(root):
    active = root / ".git" / "ai-team" / "active"
    if active.exists():
        match = re.search(r"(?m)^SESSION=(.+)$", active.read_text())
        if match:
            session = match.group(1).strip("'\"")
            if subprocess.run(["tmux", "has-session", "-t", f"={session}"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                raise ConfigError("stop the team before migration")


def migrate(cp, root, path, dry_run):
    modern, _, _, rows = resolve(cp)
    if modern:
        raise ConfigError("config already uses worker instances")
    check_stopped(root)
    old_ids = [row[0] for row in rows if row[0] not in RESERVED]
    if not old_ids:
        raise ConfigError("config has no regular workers")
    for task, destination in task_destinations(root):
        if destination in old_ids:
            raise ConfigError(f"task {task.name} still addresses {destination}; finish its workflow first")
    mapping = {worker_id: f"{TYPE_FOR_LEGACY.get(worker_id, worker_id)}-1" for worker_id in old_ids}
    if any(row[0] == "notetaker" for row in rows):
        mapping["notetaker"] = "notetaker"
        note_role = root / ".agents" / "roles" / "notetaker.md"
        if not note_role.is_file():
            raise ConfigError(f"missing {note_role}. Run teamflow init with the updated tool home first")
    worktree_root = root / ".ai-team-worktrees"
    mailbox = root / ".git" / "ai-team"
    moves = []
    for old_id, new_id in mapping.items():
        if old_id == "notetaker":
            continue
        kind = TYPE_FOR_LEGACY.get(old_id, old_id)
        role_file = root / ".agents" / "roles" / role_file_for_type(kind)
        if not role_file.is_file():
            raise ConfigError(f"missing {role_file}. Run teamflow init with the updated tool home first")
        old_wt, new_wt = worktree_root / old_id, worktree_root / new_id
        if new_wt.exists() or any((mailbox / "sessions").glob(new_id + "*")):
            raise ConfigError(f"migration destination already exists for {new_id}")
        if old_wt.exists():
            moves.append((old_wt, new_wt))
        branch = subprocess.run(["git", "-C", str(root), "show-ref", "--verify", "--quiet", f"refs/heads/ai-team/{new_id}"], check=False)
        if branch.returncode == 0:
            raise ConfigError(f"branch ai-team/{new_id} already exists")
    for old_id, new_id in mapping.items():
        print(f"{old_id} -> {new_id}")
    if dry_run:
        return
    new = configparser.ConfigParser(interpolation=None)
    new["workspace"] = dict(cp["workspace"]) if cp.has_section("workspace") else {}
    if cp.has_section("pane.delegator"):
        new["pane.delegator"] = dict(cp["pane.delegator"])
    for row in rows:
        old_id = row[0]
        if old_id == "delegator":
            continue
        kind = TYPE_FOR_LEGACY.get(old_id, old_id)
        settings = dict(cp[f"pane.{old_id}"])
        settings["role_file"] = f".agents/roles/{role_file_for_type(kind)}"
        if old_id != "notetaker":
            settings["next_id"] = "2"
        new[f"type.{kind}"] = settings
    for row in rows:
        old_id = row[0]
        if old_id == "delegator":
            continue
        new[f"worker.{mapping[old_id]}"] = {"type": TYPE_FOR_LEGACY.get(old_id, old_id)}
    buffer = io.StringIO()
    new.write(buffer)
    resolve(read_config_from_text(buffer.getvalue()))
    backup = path.with_name(path.name + ".pre-workers.bak")
    if backup.exists():
        raise ConfigError(f"backup already exists: {backup}")
    moved = []
    renamed = []
    modified = []
    try:
        for old_wt, new_wt in moves:
            subprocess.run(["git", "-C", str(root), "worktree", "move", str(old_wt), str(new_wt)], check=True)
            moved.append((old_wt, new_wt))
        for old_id, new_id in mapping.items():
            if old_id == "notetaker":
                continue
            old_branch = f"ai-team/{old_id}"
            branch = subprocess.run(["git", "-C", str(root), "show-ref", "--verify", "--quiet", f"refs/heads/{old_branch}"], check=False)
            if branch.returncode == 0:
                subprocess.run(["git", "-C", str(root), "branch", "-m", old_branch, f"ai-team/{new_id}"], check=True)
                renamed.append((old_branch, f"ai-team/{new_id}"))
        for old_id, new_id in mapping.items():
            if old_id == "notetaker":
                continue
            sessions = mailbox / "sessions"
            for source in sessions.glob(old_id + "*"):
                if source.name == old_id or source.name.startswith(old_id + "."):
                    destination = sessions / (new_id + source.name[len(old_id):])
                    if destination.exists():
                        raise ConfigError(f"session destination exists: {destination}")
                    source.rename(destination)
                    moved.append((source, destination))
                    original = destination.read_text()
                    changed = original.replace(f"ROLE={old_id}\n", f"ROLE={new_id}\n")
                    changed = changed.replace(f"CWD={worktree_root / old_id}\n", f"CWD={worktree_root / new_id}\n")
                    atomic_write(destination, changed)
                    modified.append((destination, original))
        shutil.copy2(path, backup)
        validated_write(path, buffer.getvalue())
    except (OSError, subprocess.CalledProcessError, ConfigError):
        for destination, original in reversed(modified):
            if destination.exists():
                atomic_write(destination, original)
        for old, new_path in reversed(moved):
            if new_path.exists():
                if ".ai-team-worktrees" in str(new_path):
                    subprocess.run(["git", "-C", str(root), "worktree", "move", str(new_path), str(old)], check=False)
                else:
                    new_path.rename(old)
        for old, new_branch in reversed(renamed):
            subprocess.run(["git", "-C", str(root), "branch", "-m", new_branch, old], check=False)
        raise
    print(f"migrated config. Backup: {backup}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["parse", "list", "add", "remove", "migrate"])
    parser.add_argument("config", type=Path)
    parser.add_argument("root", type=Path)
    parser.add_argument("value", nargs="?")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cp = read_config(args.config)
    modern, prefix, zai_env, rows = resolve(cp)
    if args.command == "parse":
        print(SEP.join([prefix, zai_env]))
        for row in rows:
            print(SEP.join(row))
    elif args.command == "list":
        list_workers(cp, args.root, args.config)
    elif args.command == "add":
        if not args.value:
            raise ConfigError("usage: workers add <type>")
        add_worker(cp, args.root, args.config, args.value)
    elif args.command == "remove":
        if not args.value:
            raise ConfigError("usage: workers remove <id>")
        remove_worker(cp, args.root, args.config, args.value)
    elif args.command == "migrate":
        migrate(cp, args.root, args.config, args.dry_run)


if __name__ == "__main__":
    try:
        main()
    except ConfigError as exc:
        print(f"teamflow workers: {exc}", file=sys.stderr)
        sys.exit(1)
