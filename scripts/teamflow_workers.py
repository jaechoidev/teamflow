#!/usr/bin/env python3
"""Resolve teamflow worker types, manage configured instances, and clean up their worktrees."""

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
TOOL_HOME = Path(__file__).resolve().parents[1]
NOTE_QUEUE = TOOL_HOME / ".agents/lib/note-queue.sh"
SLUG = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")

class ConfigError(Exception):
    pass


def role_path(root, role_file):
    """A role file in the project overrides the installed default."""
    project = Path(root) / role_file
    return project if project.is_file() else TOOL_HOME / role_file


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
    prefix = valid_field(workspace.get("session_prefix", "tf"), "session prefix")
    zai_env = valid_field(workspace.get("zai_env", "~/.zai/env.sh"), "zai env")
    # [pane.delegator] configured the removed full-team mode. It is ignored,
    # and teamflow init removes it.
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
    workers = sections(cp, "worker.")
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
    # Released tasks wait for a later notetaker. Only a note in progress blocks.
    if worker_id == "notetaker" and (mailbox_path(root) / "note-queue" / "active").exists():
        raise ConfigError("the notetaker is writing a note. Remove it after it finishes")


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
    # Include dead panes when removing an instance, so its window closes too.
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
        if role_file and not role_path(root, role_file).is_file():
            raise ConfigError(f"missing role file: {role_file}")
        if cli == "zai" and not Path(zai_env).expanduser().is_file():
            raise ConfigError(f"missing z.ai env file: {zai_env}")
        if worker == "notetaker" and not NOTE_QUEUE.is_file():
            raise ConfigError(f"notetaker queue library is missing from {NOTE_QUEUE.parent}")
    if any(row[0] == "notetaker" for row in missing):
        (root / "docs/notes").mkdir(parents=True, exist_ok=True)  # notes arrive with the notetaker
    # The pane launcher reads a new instance's settings from the session roster.
    session = session_config(root)
    previous = session.read_text() if session.exists() else None
    atomic_write(session, desired)
    launched = []
    try:
        for row in missing:
            worker = row[0]
            result = subprocess.run(["bash", str(Path(__file__).with_name("teamflow")),
                                     "--internal-launch-worker", str(session), str(root), record["SESSION"],
                                     worker], capture_output=True, text=True)
            pane = result.stdout.strip()
            if result.returncode or not re.fullmatch(r"%[0-9]+", pane):
                raise ConfigError(result.stderr.strip() or f"could not launch {worker}")
            launched.append(pane)
            # A CLI that cannot start exits within moments. Watch briefly.
            for _ in range(5):
                time.sleep(0.1)
                if tmux("display-message", "-p", "-t", pane, "#{pane_dead}") != "0":
                    raise ConfigError(f"{worker} exited during launch")
            panes[worker] = pane
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
        atomic_write(registry, "".join(f"{row[0]}\t{panes[row[0]]}\n" for row in rows))
        record["CONFIG_HASH"] = hashlib.sha256(path.read_bytes()).hexdigest()
        atomic_write(mailbox / "active", "".join(f"{key}={value}\n" for key, value in record.items()))
        if "notetaker" in panes:
            subprocess.run(["bash", str(NOTE_QUEUE), "poke"],
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
    if not record and not any(row[0] != "delegator" for row in rows):
        print(f"no default team in {path.name}. Add workers on demand: teamflow add <type> (see teamflow types)")
    for worker_id, label, cli, model, _, worktree, kind, _ in rows:
        if worker_id == "delegator":
            continue
        state = panes.get(worker_id, "stopped")
        if record and worker_id not in in_config:
            state += " (session only)"
        target = window_target(record, worker_id, panes[worker_id]) if worker_id in panes else ""
        opener = f"\ttmux attach -t {target}" if target else ""
        print(f"{worker_id}\t{kind or worker_id}\t{cli}\t{model}\t{worktree}\t{state}{opener}")
    for worker_id, pane in panes.items():
        if worker_id not in listed and worker_id != "delegator":
            print(f"{worker_id}\tnot in the roster\t\t\t\t{pane}")
    if record:
        for worker_id in sorted(in_config - listed):
            print(f"{worker_id}\tin {path.name}, not in this session\t\t\t\tstopped")
        if panes:
            print("already inside tmux? use tmux switch-client -t with the same target instead of attach")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if record.get("CONFIG_HASH", digest) != digest:
            print(f"{path.name} changed since this team started. It applies at the next start",
                  file=sys.stderr)


def window_target(record, worker_id, pane):
    """The tmux target of a worker's window. A window named after its worker
    is targeted by name, which survives window renumbering."""
    try:
        index, name = tmux("display-message", "-p", "-t", pane, "#{window_index} #{window_name}").split(" ", 1)
    except (ConfigError, ValueError):
        return ""
    return f"{record['SESSION']}:{name if name in (worker_id, 'view') else index}"


def instances(cp, kind=None):
    """Worker IDs in roster order, optionally only those of one type."""
    return [section[7:] for section in sections(cp, "worker.")
            if kind is None or cp[section].get("type") == kind]


def used_instances(root, cp, kind=None):
    """Numbered instances launched before, most recently used first. Each
    keeps its conversation record, so bringing one back resumes it."""
    registry = mailbox_path(root) / "sessions"
    found = []
    if registry.is_dir():
        for entry in registry.iterdir():
            match = re.fullmatch(r"(.+)-[1-9][0-9]*", entry.name)
            if not match or not cp.has_section(f"type.{match.group(1)}"):
                continue
            if kind is None or match.group(1) == kind:
                found.append((read_record(entry).get("UPDATED_AT", ""), entry.name))
    return [name for _, name in sorted(found, reverse=True)]


def stopped_add_message(path, saved, kind, worker_id):
    if worker_id:
        return (f"no team is running. {worker_id} is already in the default team, so starting the team "
                f"launches it. To start it alone: scripts/teamflow start --only {worker_id}")
    return (f"no team is running. Start the default team, then add {kind}, or start a team with only this "
            f"worker: scripts/teamflow start --only {kind}. To add {kind} to the default team in "
            f"{path.name} instead, use --save")


def add_worker(root, path, value, save):
    """Add a worker by type, or bring one back by ID. A type brings back a
    worker of that type that is not running before it mints a new number:
    first a default-team instance, then the most recently used one. The
    worker keeps its ID, conversation, and branch."""
    saved = read_config(path)
    used = used_instances(root, saved)
    explicit = saved.has_section(f"worker.{value}") or value in used
    if saved.has_section(f"worker.{value}"):
        kind = saved[f"worker.{value}"].get("type", "")
    elif explicit:
        kind = re.fullmatch(r"(.+)-[1-9][0-9]*", value).group(1)
    else:
        kind = value
    if not saved.has_section(f"type.{kind}"):
        raise ConfigError(f"unknown worker type or instance: {value}")
    record = running_team(root, path)
    if not record and not save:
        default = value if explicit else next(iter(instances(saved, kind)), "")
        raise ConfigError(stopped_add_message(path, saved, kind, default))
    record, targets = change_targets(root, path, save)
    contents = {target: target.read_text() for target in targets}
    parsed = {target: parse_text(text) for target, text in contents.items()}
    for cp in parsed.values():
        resolve(cp)
        if not cp.has_section(f"type.{kind}"):
            raise ConfigError(f"unknown worker type: {kind}")
    role_file = role_path(root, parsed[targets[0]][f"type.{kind}"].get("role_file", ""))
    if not role_file.is_file():
        raise ConfigError(f"missing role file for {kind}: {role_file}")
    session = session_config(root)
    running = set(instances(parsed[session])) if record else set()
    number = None
    if explicit or kind == "notetaker":
        worker_id = value if explicit else "notetaker"
    else:
        # --save grows the default team, so it skips workers already in it.
        returning = [] if save else [worker for worker in instances(saved, kind) if worker not in running]
        returning += [worker for worker in used_instances(root, saved, kind)
                      if worker not in running and worker not in returning
                      and not saved.has_section(f"worker.{worker}")]
        if returning:
            worker_id = returning[0]
        else:
            number = next_number(root, kind, parsed.values())
            worker_id = f"{kind}-{number}"
    targets = [target for target in targets if not parsed[target].has_section(f"worker.{worker_id}")]
    if not targets:
        raise ConfigError(f"{worker_id} is already in " + ("this session" if record else f"the default team in {path.name}"))
    changed = {}
    for target in targets:
        text = contents[target]
        if number is not None:
            text = update_option(text, f"type.{kind}", "next_id", number + 1)
        changed[target] = with_worker(text, parsed[target], worker_id, kind)
        resolve(parse_text(changed[target]))
    # Checked before launching, which writes this worker's conversation record.
    launched_before = (mailbox_path(root) / "sessions" / worker_id).exists()
    live = commit_change(root, path, changed, record)
    verb = f"brought back {worker_id}" if number is None and launched_before else f"added {worker_id}"
    if live and path in changed:
        print(f"{verb}, launched its window, and saved it to {path.name}")
    elif live and saved.has_section(f"worker.{worker_id}"):
        print(f"{verb} from the default team and launched its window")
    elif live:
        print(f"{verb} to this session and launched its window. "
              f"{path.name} is unchanged: add --save to keep it for the next start")
    else:
        print(f"{verb} to the default team in {path.name}. It launches at the next start")


def remove_worker(root, path, worker_id, save):
    record = running_team(root, path)
    if not record and not save:
        raise ConfigError(f"no team is running, so there is no session to remove {worker_id} from. "
                          f"To remove it from the default team in {path.name}, use --save")
    record, targets = change_targets(root, path, save)
    section = f"worker.{worker_id}"
    changed = {}
    for target in targets:
        text = target.read_text()
        cp = parse_text(text)
        if not cp.has_section(section):
            continue
        if len(instances(cp)) <= 1:
            raise ConfigError("cannot remove the last worker. Stop the team with scripts/teamflow --kill")
        changed[target] = replace_section(text, section, "")
        resolve(parse_text(changed[target]))
    if not changed:
        raise ConfigError(f"worker not found: {worker_id}" + (" in this session" if record and not save else ""))
    guard_removal(root, worker_id)
    live = commit_change(root, path, changed, record)
    if live and path in changed:
        print(f"removed {worker_id}, closed its window, and removed it from {path.name}")
    elif live:
        print(f"removed {worker_id} from this session and closed its window. {path.name} is unchanged"
              + (": add --save to drop it for the next start" if read_config(path).has_section(section) else ""))
    else:
        print(f"removed {worker_id} from the default team in {path.name}")
    print("conversation, branch, and worktree were preserved")


def only_roster(root, path, value):
    """Print a session roster with a single worker, chosen like add: an
    instance by ID, or for a type its first default-team instance, else its
    most recently used one, else a new one."""
    text = path.read_text()
    cp = parse_text(text)
    resolve(cp)
    used = used_instances(root, cp)
    if cp.has_section(f"worker.{value}"):
        worker_id, kind = value, cp[f"worker.{value}"].get("type", "")
    elif value in used:
        worker_id, kind = value, re.fullmatch(r"(.+)-[1-9][0-9]*", value).group(1)
    elif cp.has_section(f"type.{value}") and value != "delegator":
        kind = value
        choices = instances(cp, kind) + used_instances(root, cp, kind)
        worker_id = choices[0] if choices else ("notetaker" if kind == "notetaker" else "")
    else:
        raise ConfigError(f"--only: no worker or type named {value} in {path.name}")
    for other in instances(cp):
        if other != worker_id:
            text = replace_section(text, f"worker.{other}", "")
    if not worker_id:
        number = next_number(root, kind, [cp])
        worker_id = f"{kind}-{number}"
        text = update_option(text, f"type.{kind}", "next_id", number + 1)
    if not parse_text(text).has_section(f"worker.{worker_id}"):
        text = with_worker(text, parse_text(text), worker_id, kind)
    resolve(parse_text(text))
    print(text, end="")


def list_types(path):
    """The worker catalog: every type the Delegator can add, with what it is for."""
    cp = read_config(path)
    resolve(cp)
    print("type\tcli\tmodel\teffort\tworktree\tuse for")
    for section in sections(cp, "type."):
        item = cp[section]
        print("\t".join([section[5:], item.get("cli", ""), item.get("model", ""), item.get("effort", ""),
                         item.get("worktree", "no"), item.get("use_for", "")]))


def busy_workers(root):
    """Workers with an assigned or in-progress task. The notetaker is busy
    while it writes a note or released tasks wait for it."""
    busy = set()
    released = False
    for task, destination in task_destinations(root):
        if (task / "status").read_text().strip() in {"assigned", "in-progress"}:
            busy.add(destination)
        released = released or (task / "released").exists()
    if released or (mailbox_path(root) / "note-queue" / "active").exists():
        busy.add("notetaker")
    return busy


def trim_workers(root, path):
    """Remove idle workers from the running session. Exit status 3 means
    every worker is idle, so the caller stops the team instead."""
    if not running_team(root, path):
        raise ConfigError("no team is running, so there are no workers to trim")
    session = session_config(root)
    text = session.read_text() if session.exists() else path.read_text()
    workers = instances(parse_text(text))
    busy = busy_workers(root)
    idle = [worker for worker in workers if worker not in busy]
    if not idle:
        print("no idle workers: every worker has a task in progress")
        return 0
    if len(idle) == len(workers):
        print(f"every worker is idle ({', '.join(idle)})")
        return 3
    for worker in idle:
        text = replace_section(text, f"worker.{worker}", "")
    sync_workers(root, path, text)
    print(f"removed idle workers from this session: {', '.join(idle)}")
    print(f"still working: {', '.join(worker for worker in workers if worker in busy)}")
    return 0


def last_roster(root, path):
    """Print a session roster with the workers of the last session, using
    the current type settings from the config."""
    session = session_config(root)
    if not session.exists():
        raise ConfigError("no earlier session to resume. Start the default team with teamflow start, "
                          "or add workers on demand with teamflow add <type>")
    previous = read_config(session)
    text = path.read_text()
    cp = parse_text(text)
    resolve(cp)
    for worker in instances(cp):
        text = replace_section(text, f"worker.{worker}", "")
    for worker in instances(previous):
        kind = previous[f"worker.{worker}"].get("type", "")
        current = parse_text(text)
        if not current.has_section(f"type.{kind}"):
            print(f"teamflow: skipped {worker}: type {kind} is no longer in {path.name}", file=sys.stderr)
            continue
        text = with_worker(text, current, worker, kind)
    if not instances(parse_text(text)):
        raise ConfigError("the last session had no workers to resume")
    resolve(parse_text(text))
    print(text, end="")


def sync_session(root, path):
    record = running_team(root, path)
    if not record:
        print(f"team is stopped, {path.name} is ready for the next start")
        return
    session = session_config(root)
    sync_workers(root, path, session.read_text() if session.exists() else path.read_text())
    print("running panes match the session roster")


def git(cwd, *args, check=True):
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if check and result.returncode:
        raise ConfigError(result.stderr.strip() or f"git {args[0]} failed")
    return result


def team_worktrees(root):
    """Worker worktrees: {instance ID: path} for .teamflow-worktrees/<id> on teamflow/<id>."""
    base = (root / ".teamflow-worktrees").resolve()
    found = {}
    for block in git(root, "worktree", "list", "--porcelain").stdout.strip().split("\n\n"):
        info = dict(line.split(" ", 1) if " " in line else (line, "") for line in block.splitlines())
        path = Path(info.get("worktree", ""))
        if path.parent.resolve() == base and info.get("branch") == f"refs/heads/teamflow/{path.name}":
            found[path.name] = path
    return found


def head_name(root):
    return git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).stdout.strip() or "HEAD"


def pending_commits(root, worker_id):
    """Commits on the worker's branch that the main checkout's branch lacks, oldest first."""
    branch = f"teamflow/{worker_id}"
    pending = [line.split()[1] for line in git(root, "cherry", "HEAD", branch).stdout.splitlines()
               if line.startswith("+ ")]
    if pending:
        # A squash merge leaves no matching commit. The work counts as
        # merged when every file the branch changed already matches HEAD.
        base = git(root, "merge-base", "HEAD", branch).stdout.strip()
        files = [name for name in git(root, "diff", "--name-only", "-z", base, branch).stdout.split("\0") if name]
        if not files or git(root, "diff", "--quiet", branch, "HEAD", "--", *files, check=False).returncode == 0:
            return []
    return pending


def describe(pending, dirty, branch):
    parts = []
    if pending:
        parts.append(f"{len(pending)} commit{'s' if len(pending) != 1 else ''} not in {branch}")
    if dirty:
        parts.append(f"{len(dirty)} uncommitted file{'s' if len(dirty) != 1 else ''}")
    return ", ".join(parts)


def remove_worktree(root, worker_id, path, force):
    git(root, "worktree", "remove", *(["--force", "--force"] if force else []), str(path))
    git(root, "branch", "-D", f"teamflow/{worker_id}")
    try:
        (root / ".teamflow-worktrees").rmdir()
    except OSError:
        pass


def stopped_worktree(root, worker_id):
    path = team_worktrees(root).get(worker_id)
    if not path:
        raise ConfigError(f"no worktree for {worker_id} under .teamflow-worktrees/")
    if worker_id in active_panes(root):
        raise ConfigError(f"{worker_id} is still running. Stop the team with scripts/teamflow --kill "
                          f"or remove the worker with scripts/teamflow workers remove {worker_id}")
    return path


def clean_worktrees(root):
    """Remove the worktrees of stopped workers whose work is all in the main
    checkout's branch. Report the others, which need a merge or discard."""
    git(root, "worktree", "prune")
    live, branch = active_panes(root), head_name(root)
    kept = []
    for worker_id, path in sorted(team_worktrees(root).items()):
        if worker_id in live:
            continue
        try:
            pending = pending_commits(root, worker_id)
            dirty = git(path, "status", "--porcelain").stdout.splitlines()
            if pending or dirty:
                kept.append(f"  {worker_id}: {describe(pending, dirty, branch)}")
                continue
            remove_worktree(root, worker_id, path, force=False)
            print(f"removed worktree {worker_id}: its work is already in {branch}")
        except ConfigError as exc:
            kept.append(f"  {worker_id}: {exc}")
    if kept:
        print(f"kept {len(kept)} worktree{'s' if len(kept) != 1 else ''} with work that is not in {branch}:")
        print("\n".join(kept))
        print("merge or discard each: scripts/teamflow worktrees merge <id> | scripts/teamflow worktrees discard <id>")


def list_worktrees(root):
    live, branch = active_panes(root), head_name(root)
    worktrees = team_worktrees(root)
    if not worktrees:
        print("no worker worktrees")
    for worker_id, path in sorted(worktrees.items()):
        pending = pending_commits(root, worker_id)
        dirty = git(path, "status", "--porcelain").stdout.splitlines()
        state = describe(pending, dirty, branch) or f"all work is in {branch}"
        print(f"{worker_id}\t{'running' if worker_id in live else 'stopped'}\t{state}")


def merge_worktree(root, worker_id):
    path = stopped_worktree(root, worker_id)
    branch = head_name(root)
    if git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise ConfigError(f"the main checkout has uncommitted changes. Commit or stash them before merging {worker_id}")
    if git(path, "status", "--porcelain").stdout.strip():
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", f"chore(teamflow): keep uncommitted work from {worker_id}")
    pending = pending_commits(root, worker_id)
    if pending and git(root, "cherry-pick", *pending, check=False).returncode:
        git(root, "cherry-pick", "--abort", check=False)
        raise ConfigError(f"merging {worker_id} into {branch} stopped on a conflict. Nothing was merged, and "
                          f"its worktree and branch teamflow/{worker_id} were kept. Resolve it by hand with: "
                          f"git cherry-pick {' '.join(pending)}")
    remove_worktree(root, worker_id, path, force=False)
    print(f"merged {len(pending)} commit{'s' if len(pending) != 1 else ''} from {worker_id} into {branch} "
          f"and removed its worktree and branch")


def discard_worktree(root, worker_id):
    path = stopped_worktree(root, worker_id)
    remove_worktree(root, worker_id, path, force=True)
    print(f"discarded {worker_id}: removed its worktree and branch teamflow/{worker_id}")


def worktrees_main(argv):
    actions = {"clean": clean_worktrees, "list": list_worktrees}
    if len(argv) == 2 and argv[0] in actions:
        actions[argv[0]](Path(argv[1]))
    elif len(argv) == 3 and argv[0] in {"merge", "discard"}:
        (merge_worktree if argv[0] == "merge" else discard_worktree)(Path(argv[1]), argv[2])
    else:
        raise ConfigError("usage: worktrees list|clean <root> | worktrees merge|discard <root> <id>")


def registered_panes(root):
    """{pane id: worker} from the pane registry, including dead panes."""
    registry = mailbox_path(root) / "panes.tsv"
    if not registry.exists():
        return {}
    return {pane: worker for worker, pane in
            (line.split("\t", 1) for line in registry.read_text().splitlines() if "\t" in line)}


def own_window(pane, worker):
    """Settings every worker window gets at launch: its ID as a fixed name,
    kept after the CLI exits, with new output flagged."""
    tmux("rename-window", "-t", pane, worker)
    for option, value in (("remain-on-exit", "on"), ("automatic-rename", "off"),
                          ("allow-rename", "off"), ("monitor-activity", "on")):
        tmux("set-window-option", "-t", pane, option, value)


def close_view(record, root):
    """Move every pane in the view window back into its own worker window."""
    try:
        listing = tmux("list-panes", "-t", f"={record['SESSION']}:view", "-F", "#{pane_id}").split()
    except ConfigError:
        return []
    owners = registered_panes(root)
    first, rest = listing[0], listing[1:]
    for pane in rest:
        tmux("break-pane", "-d", "-s", pane, "-n", owners.get(pane, pane.lstrip("%")))
        own_window(pane, owners.get(pane, pane.lstrip("%")))
    tmux("set-window-option", "-u", "-t", first, "pane-border-status")
    tmux("set-window-option", "-u", "-t", first, "pane-border-format")
    own_window(first, owners.get(first, first.lstrip("%")))
    return [owners.get(pane, pane) for pane in listing]


def view_workers(root, path, ids, close):
    """Gather workers into one tiled window named view, or put them back."""
    record = running_team(root, path)
    if not record:
        raise ConfigError("no team is running, so there is nothing to view")
    session = record["SESSION"]
    closed = close_view(record, root)
    if close:
        print(f"closed the view: {', '.join(closed)} are back in their own windows" if closed
              else "no view is open")
        return
    panes = active_panes(root, record)
    roster = [worker for worker in panes if worker != "delegator"]
    chosen = ids or roster
    missing = [worker for worker in chosen if worker not in panes]
    if missing:
        raise ConfigError(f"not running: {', '.join(missing)}. See teamflow list")
    if not chosen:
        raise ConfigError("no running workers to view")
    first = panes[chosen[0]]
    tmux("rename-window", "-t", first, "view")
    for worker in chosen:
        # CLIs rewrite pane titles, so the border shows a pane option instead.
        tmux("set-option", "-p", "-t", panes[worker], "@teamflow_worker", worker)
    for worker in chosen[1:]:
        tmux("join-pane", "-d", "-s", panes[worker], "-t", first)
        tmux("select-layout", "-t", first, "tiled")
    tmux("set-window-option", "-t", first, "pane-border-status", "top")
    tmux("set-window-option", "-t", first, "pane-border-format", " #{@teamflow_worker} ")
    print(f"view: {', '.join(chosen)}")
    print(f"open it: tmux attach -t {session}:view (inside tmux: tmux switch-client -t {session}:view)")
    print("put them back in their own windows: teamflow view --close")


def view_main(argv):
    if len(argv) < 2:
        raise ConfigError("usage: view <config> <root> [--close] [worker ...]")
    config, root, rest = Path(argv[0]), Path(argv[1]), argv[2:]
    close = "--close" in rest
    ids = [value for value in rest if value != "--close"]
    if close and ids:
        raise ConfigError("--close takes no worker IDs")
    view_workers(root, config, ids, close)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["parse", "list", "add", "remove", "sync", "only", "last", "types", "trim"])
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
    elif args.command == "only":
        if not args.value:
            raise ConfigError("usage: only <config> <root> <worker-or-type>")
        only_roster(args.root, args.config, args.value)
    elif args.command == "last":
        last_roster(args.root, args.config)
    elif args.command == "types":
        list_types(args.config)
    elif args.command == "trim":
        return trim_workers(args.root, args.config)
    elif args.command == "add":
        if not args.value:
            raise ConfigError("usage: workers add <type-or-id> [--save]")
        add_worker(args.root, args.config, args.value, args.save)
    elif args.command == "remove":
        if not args.value:
            raise ConfigError("usage: workers remove <id> [--save]")
        remove_worker(args.root, args.config, args.value, args.save)
    return 0


if __name__ == "__main__":
    try:
        # worktrees <action> <root> [id]; view <config> <root> [--close] [id ...];
        # the other commands: <command> <config> <root> [id]
        run = {"worktrees": lambda: worktrees_main(sys.argv[2:]),
               "view": lambda: view_main(sys.argv[2:])}.get(sys.argv[1] if len(sys.argv) > 1 else "", main)
        if len(sys.argv) > 3 and sys.argv[1] in {"add", "remove", "sync", "trim", "worktrees", "view"}:
            mailbox = mailbox_path(Path(sys.argv[3]))
            mailbox.mkdir(parents=True, exist_ok=True)
            with (mailbox / "workers.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                code = run()
        else:
            code = run()
        sys.exit(code or 0)
    except (ConfigError, OSError, subprocess.CalledProcessError) as exc:
        label = sys.argv[1] if sys.argv[1:2] in (["worktrees"], ["view"]) else "workers"
        print(f"teamflow {label}: {exc}", file=sys.stderr)
        sys.exit(1)
