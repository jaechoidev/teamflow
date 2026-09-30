#!/usr/bin/env python3
"""Resolve teamflow worker types and manage configured instances."""

import argparse
import configparser
import fcntl
import shutil
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

SEP = "\x1f"
SLUG = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
RESERVED = {"delegator", "notetaker"}

class ConfigError(Exception):
    pass


class SyncIncomplete(ConfigError):
    pass


def read_config(path):
    cp = configparser.ConfigParser(interpolation=None)
    try:
        with open(path, encoding="utf-8") as stream:
            cp.read_file(stream)
    except (OSError, configparser.Error) as exc:
        raise ConfigError(str(exc)) from exc
    return cp


def sections(cp, prefix):
    return [section for section in cp.sections() if section.startswith(prefix)]


def valid_field(value, label):
    if any(char in value for char in (SEP, "\n", "\r")):
        raise ConfigError(f"{label} contains a control separator")
    return value


def resolve(cp):
    workspace = cp["workspace"] if cp.has_section("workspace") else {}
    prefix = valid_field(workspace.get("session_prefix", "at"), "session prefix")
    zai_env = valid_field(workspace.get("zai_env", "~/.zai/env.sh"), "zai env")
    for section in sections(cp, "pane."):
        if section != "pane.delegator":
            raise ConfigError(f"unsupported pane section: {section}")
    types = {}
    for section in sections(cp, "type."):
        name = section[5:]
        if not SLUG.fullmatch(name) or name == "delegator":
            raise ConfigError(f"invalid worker type: {name}")
        types[name] = cp[section]
    rows = []
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
    return prefix, zai_env, rows


def pane_row(worker_id, item, kind="", role_file=""):
    label = item.get("label", kind or worker_id)
    if kind and worker_id != "notetaker":
        label = f"{label} ({worker_id})"
    fields = [worker_id, label, item.get("cli", ""), item.get("model", ""),
              item.get("effort", ""), item.get("worktree", "no"), kind, role_file]
    return [valid_field(field, worker_id) for field in fields]


def parse_text(content):
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read_string(content)
    except configparser.Error as exc:
        raise ConfigError(str(exc)) from exc
    return cp


def atomic_write(path, content):
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


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


def mailbox_path(root):
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True, check=True)
    return Path(result.stdout.strip()) / "teamflow"


def session_config(root):
    """The running team's roster: a copy of the config at start, plus the
    workers added or removed during the session. The next start begins
    again from the config."""
    return mailbox_path(root) / "running.conf"


def task_destinations(root):
    tasks = mailbox_path(root) / "tasks"
    for task in tasks.glob("T-*/task.md"):
        for line in task.read_text(errors="replace").splitlines():
            if line.startswith("to:      "):
                yield task.parent, line[9:]
                break


def tmux(*args):
    try:
        result = subprocess.run(["tmux", *args], capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise ConfigError("tmux not found") from exc
    if result.returncode:
        raise ConfigError(result.stderr.strip() or "tmux command failed")
    return result.stdout.strip()


def read_record(path):
    if not path.exists():
        return {}
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


def running_team(root, path):
    record = read_record(mailbox_path(root) / "active")
    if not record.get("SESSION"):
        return None
    try:
        sessions = tmux("list-sessions", "-F", "#{session_name} #{session_created}")
        created = dict(line.rsplit(" ", 1) for line in sessions.splitlines()).get(record["SESSION"])
    except ConfigError:
        return None
    if created != record.get("CREATED"):
        return None
    if Path(record.get("CONFIG", "")).resolve() != path.resolve():
        raise ConfigError("a team is running with another config")
    return record


def active_panes(root, record=None):
    record = record or read_record(mailbox_path(root) / "active")
    if not record.get("SESSION"):
        return {}
    try:
        alive = set(tmux("list-panes", "-s", "-t", "=" + record["SESSION"],
                         "-F", "#{pane_id} #{pane_dead}").splitlines())
    except ConfigError:
        return {}
    registry = mailbox_path(root) / "panes.tsv"
    mapping = {}
    if registry.exists():
        for line in registry.read_text().splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2 and parts[1] + " 0" in alive:
                mapping[parts[0]] = parts[1]
    return mapping


def guard_removal(root, worker_id):
    for task, destination in task_destinations(root):
        if destination == worker_id and (task / "status").read_text().strip() in {"assigned", "in-progress"}:
            raise ConfigError(f"{worker_id} has unfinished task {task.name}")
    if worker_id == "notetaker":
        queue = mailbox_path(root) / "note-queue"
        if (queue / "active").exists() or any((task / "released").exists() for task, _ in task_destinations(root)):
            raise ConfigError("notetaker queue is not empty")


def launch_target(session):
    windows = tmux("list-windows", "-t", "=" + session,
                   "-F", "#{window_id} #{window_name} #{window_width} #{window_height} #{window_panes}")
    names = set()
    for line in windows.splitlines():
        window, name, width, height, count = line.split()
        names.add(name)
        if name != "team" and not re.fullmatch(r"team-[0-9]+", name):
            continue
        capacity = min(12, max(1, int(width) // 20) * max(1, (int(height) - 1) // 6))
        if int(count) < capacity:
            panes = tmux("list-panes", "-t", window, "-F", "#{pane_id} #{pane_width} #{pane_height}")
            largest = max((line.split() for line in panes.splitlines()), key=lambda p: int(p[1]) * int(p[2]))
            return largest[0], "-h" if int(largest[1]) > int(largest[2]) * 2 else "-v"
    number = 2
    while f"team-{number}" in names:
        number += 1
    return f"team-{number}", "window"


def sync_workers(root, path, desired):
    """Make the running team match the roster in desired (config text)."""
    record = running_team(root, path)
    if not record:
        return False
    mailbox = mailbox_path(root)
    _, zai_env, rows = resolve(parse_text(desired))
    if record.get("MODE") == "workers":
        rows = [row for row in rows if row[0] != "delegator"]
    wanted = {row[0] for row in rows}
    panes = active_panes(root, record)
    # Include dead panes when removing an instance, so its tile disappears too.
    registry = mailbox / "panes.tsv"
    registered = dict(line.split("\t", 1) for line in registry.read_text().splitlines() if "\t" in line) if registry.exists() else {}
    all_panes = set(tmux("list-panes", "-s", "-t", "=" + record["SESSION"], "-F", "#{pane_id}").splitlines())
    obsolete = {worker: pane for worker, pane in registered.items() if worker not in wanted and pane in all_panes}
    for worker in obsolete:
        guard_removal(root, worker)
    missing = [row for row in rows if row[0] not in panes]
    for worker, _, cli, _, _, _, _, role_file in missing:
        if not shutil.which("claude" if cli == "zai" else cli):
            raise ConfigError(f"required CLI not on PATH: {cli}")
        if role_file and not (root / role_file).is_file():
            raise ConfigError(f"missing role file: {role_file}")
        if cli == "zai" and not Path(zai_env).expanduser().is_file():
            raise ConfigError(f"missing z.ai env file: {zai_env}")
        if worker == "notetaker" and not (root / ".agents/lib/note-queue.sh").is_file():
            raise ConfigError("notetaker queue library is missing")
    # The pane launcher reads a new instance's settings from the session roster.
    session = session_config(root)
    previous = session.read_text() if session.exists() else None
    atomic_write(session, desired)
    launched = []
    try:
        for row in missing:
            worker = row[0]
            target, direction = launch_target(record["SESSION"])
            result = subprocess.run(["bash", str(Path(__file__).with_name("teamflow")),
                                     "--internal-launch-worker", str(session), str(root), record["SESSION"],
                                     worker, target, direction], capture_output=True, text=True)
            pane = result.stdout.strip()
            if result.returncode or not re.fullmatch(r"%[0-9]+", pane):
                raise ConfigError(result.stderr.strip() or f"could not launch {worker}")
            launched.append(pane)
            time.sleep(0.1)
            if tmux("display-message", "-p", "-t", pane, "#{pane_dead}") != "0":
                raise ConfigError(f"{worker} exited during launch")
            panes[worker] = pane
            tmux("select-layout", "-t", pane, "tiled")
    except ConfigError:
        for pane in launched:
            try:
                tmux("kill-pane", "-t", pane)
            except ConfigError:
                pass
        if previous is None:
            session.unlink(missing_ok=True)
        else:
            atomic_write(session, previous)
        raise
    try:
        # Publish additions before closing old panes so a retry can find them.
        interim = {**registered, **panes}
        atomic_write(registry, "".join(f"{worker}\t{pane}\n" for worker, pane in interim.items()))
        for worker, pane in obsolete.items():
            tmux("kill-pane", "-t", pane)
        for row in missing:
            old = registered.get(row[0])
            if old and old in all_panes:
                tmux("kill-pane", "-t", old)
        # Keep visual pane order aligned with the roster, including Notetaker last.
        positions = tmux("list-panes", "-s", "-t", "=" + record["SESSION"],
                         "-F", "#{window_index} #{pane_index} #{pane_id}").splitlines()
        slots = [parts[2] for parts in sorted((line.split() for line in positions),
                                            key=lambda parts: (int(parts[0]), int(parts[1])))]
        ordered = [panes[row[0]] for row in rows]
        slots = [pane for pane in slots if pane in ordered]
        for index, desired_pane in enumerate(ordered):
            if slots[index] != desired_pane:
                other = slots.index(desired_pane)
                tmux("swap-pane", "-d", "-s", desired_pane, "-t", slots[index])
                slots[index], slots[other] = slots[other], slots[index]
        for window in tmux("list-windows", "-t", "=" + record["SESSION"], "-F", "#{window_id}").splitlines():
            tmux("select-layout", "-t", window, "tiled")
        atomic_write(registry, "".join(f"{row[0]}\t{panes[row[0]]}\n" for row in rows))
        record["CONFIG_HASH"] = hashlib.sha256(path.read_bytes()).hexdigest()
        atomic_write(mailbox / "active", "".join(f"{key}={value}\n" for key, value in record.items()))
        if "notetaker" in panes:
            subprocess.run(["bash", str(root / ".agents/lib/note-queue.sh"), "poke"],
                           env={**os.environ, "AGENT_MAILBOX": str(mailbox)}, check=False)
    except (ConfigError, OSError) as exc:
        raise SyncIncomplete(f"roster update incomplete: {exc}. Run workers sync to finish") from exc
    return True


def change_targets(root, path, save):
    """Files a roster change edits: the session roster while a team runs,
    the config when the team is stopped or the change is saved."""
    record = running_team(root, path)
    targets = []
    if record:
        session = session_config(root)
        if not session.exists():
            atomic_write(session, path.read_text())  # a team started before session rosters
        targets.append(session)
    if save or not record:
        targets.append(path)
    return record, targets


def commit_change(root, path, contents, record):
    previous = path.read_text()
    if path in contents:
        atomic_write(path, contents[path])
    session = session_config(root)
    if not record or session not in contents:
        return False
    try:
        return sync_workers(root, path, contents[session])
    except SyncIncomplete:
        raise
    except (ConfigError, OSError):
        if path in contents:
            atomic_write(path, previous)
        raise


def next_number(root, kind, configs):
    """The next instance number of a type. Numbers are never reused: not
    within a config, and not from instances launched in earlier sessions,
    whose conversations, worktrees, and branches keep their IDs."""
    pattern = re.compile(re.escape(kind) + r"-([1-9][0-9]*)")
    floor, used = 1, set()
    for cp in configs:
        try:
            floor = max(floor, int(cp[f"type.{kind}"].get("next_id", "1")))
        except ValueError as exc:
            raise ConfigError(f"type {kind} has invalid next_id") from exc
        for section in sections(cp, "worker."):
            match = pattern.fullmatch(section[7:])
            if match:
                used.add(int(match.group(1)))
    registry = mailbox_path(root) / "sessions"
    if registry.is_dir():
        for entry in registry.iterdir():
            match = pattern.fullmatch(entry.name.split(".", 1)[0])
            if match:
                used.add(int(match.group(1)))
    return max(floor, max(used, default=0) + 1)


def with_worker(content, cp, worker_id, kind):
    insertion = f"\n[worker.{worker_id}]\ntype = {kind}\n"
    if cp.has_section("worker.notetaker") and kind != "notetaker":
        match = re.search(r"(?m)^\s*\[worker\.notetaker\]\s*$", content)
        if not match:
            raise ConfigError("cannot locate worker.notetaker section")
        return content[:match.start()] + insertion + content[match.start():]
    return content.rstrip() + insertion


def list_workers(root, path):
    record = running_team(root, path)
    saved = read_config(path)
    session = session_config(root)
    current = read_config(session) if record and session.exists() else saved
    _, _, rows = resolve(current)
    panes = active_panes(root, record) if record else {}
    in_config = {section[7:] for section in sections(saved, "worker.")}
    listed = {row[0] for row in rows}
    for worker_id, label, cli, model, _, worktree, kind, _ in rows:
        if worker_id == "delegator":
            continue
        state = panes.get(worker_id, "stopped")
        if record and worker_id not in in_config:
            state += " (session only)"
        print(f"{worker_id}\t{kind or worker_id}\t{cli}\t{model}\t{worktree}\t{state}")
    for worker_id, pane in panes.items():
        if worker_id not in listed and worker_id != "delegator":
            print(f"{worker_id}\tnot in the roster\t\t\t\t{pane}")
    if record:
        for worker_id in sorted(in_config - listed):
            print(f"{worker_id}\tin {path.name}, not in this session\t\t\t\tstopped")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if record.get("CONFIG_HASH", digest) != digest:
            print(f"{path.name} changed since this team started. It applies at the next start",
                  file=sys.stderr)


def add_worker(root, path, kind, save):
    record, targets = change_targets(root, path, save)
    contents = {target: target.read_text() for target in targets}
    parsed = {target: parse_text(text) for target, text in contents.items()}
    for target, cp in parsed.items():
        resolve(cp)
        if not cp.has_section(f"type.{kind}"):
            raise ConfigError(f"unknown worker type: {kind}")
    role_file = root / parsed[targets[0]][f"type.{kind}"].get("role_file", "")
    if not role_file.is_file():
        raise ConfigError(f"missing role file for {kind}: {role_file}")
    if kind == "notetaker":
        worker_id = "notetaker"
        targets = [target for target in targets if not parsed[target].has_section("worker.notetaker")]
        if not targets:
            raise ConfigError("notetaker already exists")
    else:
        number = next_number(root, kind, parsed.values())
        worker_id = f"{kind}-{number}"
        for target in targets:
            contents[target] = update_option(contents[target], f"type.{kind}", "next_id", number + 1)
    changed = {}
    for target in targets:
        changed[target] = with_worker(contents[target], parsed[target], worker_id, kind)
        resolve(parse_text(changed[target]))
    live = commit_change(root, path, changed, record)
    if live and path in changed:
        print(f"added {worker_id}, launched its pane, and saved it to {path.name}")
    elif live:
        print(f"added {worker_id} to this session and launched its pane. "
              f"{path.name} is unchanged: add --save to keep it for the next start")
    else:
        print(f"added {worker_id} to {path.name}" + ("" if record else " (team is stopped)"))


def remove_worker(root, path, worker_id, save):
    record, targets = change_targets(root, path, save)
    section = f"worker.{worker_id}"
    changed = {}
    for target in targets:
        text = target.read_text()
        cp = parse_text(text)
        if not cp.has_section(section):
            continue
        _, _, rows = resolve(cp)
        if worker_id != "notetaker" and sum(row[0] not in RESERVED for row in rows) <= 1:
            raise ConfigError("cannot remove the last regular worker")
        changed[target] = replace_section(text, section, "")
        resolve(parse_text(changed[target]))
    if not changed:
        raise ConfigError(f"worker not found: {worker_id}" + (" in this session" if record and not save else ""))
    guard_removal(root, worker_id)
    live = commit_change(root, path, changed, record)
    if live and path in changed:
        print(f"removed {worker_id}, closed its pane, and removed it from {path.name}")
    elif live:
        print(f"removed {worker_id} from this session and closed its pane. {path.name} is unchanged"
              + (": add --save to drop it for the next start" if read_config(path).has_section(section) else ""))
    else:
        print(f"removed {worker_id} from {path.name}" + ("" if record else " (team is stopped)"))
    print("conversation, branch, and worktree were preserved")


def sync_session(root, path):
    record = running_team(root, path)
    if not record:
        print(f"team is stopped, {path.name} is ready for the next start")
        return
    session = session_config(root)
    sync_workers(root, path, session.read_text() if session.exists() else path.read_text())
    print("running panes match the session roster")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["parse", "list", "add", "remove", "sync"])
    parser.add_argument("config", type=Path)
    parser.add_argument("root", type=Path)
    parser.add_argument("value", nargs="?")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args()
    cp = read_config(args.config)
    prefix, zai_env, rows = resolve(cp)
    if args.command == "parse":
        print(SEP.join([prefix, zai_env]))
        for row in rows:
            print(SEP.join(row))
    elif args.command == "list":
        list_workers(args.root, args.config)
    elif args.command == "sync":
        sync_session(args.root, args.config)
    elif args.command == "add":
        if not args.value:
            raise ConfigError("usage: workers add <type> [--save]")
        add_worker(args.root, args.config, args.value, args.save)
    elif args.command == "remove":
        if not args.value:
            raise ConfigError("usage: workers remove <id> [--save]")
        remove_worker(args.root, args.config, args.value, args.save)


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1 and sys.argv[1] in {"add", "remove", "sync"}:
            root_arg = Path(sys.argv[3]) if len(sys.argv) > 3 else Path.cwd()
            mailbox = mailbox_path(root_arg)
            mailbox.mkdir(parents=True, exist_ok=True)
            with (mailbox / "workers.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                main()
        else:
            main()
    except (ConfigError, OSError, subprocess.CalledProcessError) as exc:
        print(f"teamflow workers: {exc}", file=sys.stderr)
        sys.exit(1)
