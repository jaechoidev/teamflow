#!/usr/bin/env python3
"""teamflow status / nudge: find mailbox tasks that stopped moving, and move them.

teamflow's mailbox (<git common dir>/teamflow/tasks/T-####/) records each
task's state, but nothing re-drives a task whose handoff was lost:

  * a worker never picked a task up (the send-to text queued behind a busy
    turn, a trust prompt ate it, or the pane died), so it sits `assigned`;
  * a worker finished, but the Delegator is an interactive agent that only
    learns of results when it runs `task inbox delegator`, so a `done` task
    waits until someone prompts it.

`status` reads the mailbox and flags those tasks. `nudge` is the
reconciler: it serializes periodic runs per mailbox and limits recovery
attempts. When explicitly enabled, it sends a task-ID reminder to a worker
that has not taken its task (at most --max-resends attempts, logged in the
task's events), and it
raises attention for results that wait on the Delegator: it writes
<mailbox>/attention and, when the set of waiting tasks changes, runs the
--on-attention command (or $TEAMFLOW_ON_ATTENTION) with a JSON summary on
stdin. That hook is where a notification, a watcher ping, or a headless
Delegator turn plugs in.

It never registers itself as the Delegator, never types into the
Delegator's session, and changes no task's status.

Usage:
  teamflow_status.py status <lib dir> [--json] [thresholds]
  teamflow_status.py nudge  <lib dir> [--dry-run] [--max-resends N]
                            [--on-attention CMD] [thresholds]
Thresholds (minutes): --pickup 10  --done 10  --long 120
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

NOW_ENV = "TEAMFLOW_NOW"  # tests pin the clock: ISO-8601 UTC
TASK_ID = re.compile(r"T-[0-9]{4,}")
WORKER_ID = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
PANE_TIMEOUT = 10
HOOK_TIMEOUT = 30


def now() -> dt.datetime:
    pinned = os.environ.get(NOW_ENV)
    if pinned:
        return parse_ts(pinned)
    return dt.datetime.now(dt.timezone.utc)


def parse_ts(text: str) -> dt.datetime:
    return dt.datetime.strptime(text.strip(), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def stamp(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def mailbox_dir() -> Path:
    out = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit("teamflow: not inside a git repository: run this from the project that uses teamflow")
    return Path(out.stdout.strip()) / "teamflow"


def header(task_md: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    try:
        lines = task_md.read_text(errors="replace").splitlines()
    except OSError:
        return fields
    for line in lines:
        if not line.strip():
            break  # the body starts after the first blank line
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def events(task_dir: Path) -> list[tuple[dt.datetime, str]]:
    out = []
    try:
        lines = (task_dir / "events").read_text(errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        ts, _, what = line.partition(" ")
        try:
            out.append((parse_ts(ts), what))
        except ValueError:
            continue
    return out


def load_task(d: Path) -> dict | None:
    if not TASK_ID.fullmatch(d.name) or d.is_symlink() or not d.is_dir():
        return None
    try:
        st = d.stat()
        status = (d / "status").read_text().strip()
    except OSError:
        return None
    h = header(d / "task.md")
    evs = events(d)
    try:
        created = parse_ts(h.get("created", ""))
    except ValueError:
        created = None
    try:
        uuid = json.loads((d / "identity.json").read_text()).get("uuid")
    except (OSError, ValueError, AttributeError):
        uuid = None
    # Legacy tasks do not have a UUID. Their directory identity still lets a
    # running nudge detect cleanup and replacement under the same short ID.
    identity = (st.st_dev, st.st_ino, uuid if isinstance(uuid, str) else None)
    last = max([t for t, _ in evs] + ([created] if created else []), default=None)
    released = (d / "released").exists()
    done_at = next((t for t, w in reversed(evs) if w.startswith("done by")), None)
    resends = sum(1 for _, w in evs if w.startswith(("nudged by teamflow", "nudge attempt ")))
    return {
        "id": d.name, "dir": d, "from": h.get("from", ""), "to": h.get("to", ""),
        "title": h.get("title", ""), "status": status, "created": created, "identity": identity,
        "last": last, "done_at": done_at, "released": released, "resends": resends,
        "claimed": (d / "claim").exists(),
    }


def load_tasks(mailbox: Path) -> list[dict]:
    tasks = (load_task(d) for d in sorted((mailbox / "tasks").glob("T-*")))
    return [t for t in tasks if t is not None]


def minutes_since(t: dt.datetime | None, at: dt.datetime) -> float | None:
    return None if t is None else (at - t).total_seconds() / 60


def classify(task: dict, at: dt.datetime, pickup: float, done: float, long: float) -> str | None:
    """The flag for a task that stopped moving, or None."""
    if task["status"] == "assigned":
        age = minutes_since(task["last"], at)
        if (age is not None and age >= pickup and not task["claimed"] and
                task["to"] != "delegator" and WORKER_ID.fullmatch(task["to"])):
            return "not-picked-up"
    elif task["status"] == "in-progress":
        age = minutes_since(task["last"], at)
        if age is not None and age >= long:
            return "long-running"
    elif task["status"] == "done" and not task["released"] and task["from"] == "delegator":
        age = minutes_since(task["done_at"] or task["last"], at)
        if age is not None and age >= done:
            return "awaiting-delegator"
    return None


def survey(mailbox: Path, args) -> list[dict]:
    at = now()
    rows = []
    for t in load_tasks(mailbox):
        flag = classify(t, at, args.pickup, args.done, args.long)
        age = minutes_since(t["last"], at)
        rows.append({
            "id": t["id"], "to": t["to"], "from": t["from"], "title": t["title"],
            "status": t["status"], "released": t["released"],
            "age_min": None if age is None else round(age, 1),
            "resends": t["resends"], "flag": flag, "_dir": t["dir"], "_identity": t["identity"],
        })
    return rows


def public(row: dict) -> dict:
    return {k: v for k, v in row.items() if not k.startswith("_")}


def cmd_status(mailbox: Path, args) -> int:
    rows = survey(mailbox, args)
    if args.json:
        print(json.dumps([public(r) for r in rows], indent=2))
        return 0
    if not rows:
        print("no tasks in the mailbox")
        return 0
    for r in rows:
        age = "?" if r["age_min"] is None else f"{r['age_min']:.0f}m"
        state = r["status"] + ("/released" if r["released"] else "")
        flag = f"  <-- {r['flag']}" if r["flag"] else ""
        print(f"{r['id']}  {state:<16} -> {r['to']:<14} {age:>6}  {r['title']}{flag}")
    stuck = [r for r in rows if r["flag"]]
    print(f"-- {len(stuck)} of {len(rows)} task(s) need attention" if stuck else "-- nothing stuck")
    return 0


def record_attempt(task: dict) -> bool:
    # Reserve under the nudge lock before typing. If the send partly worked,
    # failed, or the process died, it still consumes one bounded attempt.
    # Anchor the append to the current task directory, not a recycled alias.
    try:
        fd = os.open(task["dir"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            st = os.fstat(fd)
            if (st.st_dev, st.st_ino) != task["identity"][:2]:
                return False
            ev = os.open("events", os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
                         0o600, dir_fd=fd)
            with os.fdopen(ev, "a") as stream:
                stream.write(f"{stamp(now())} nudge attempt {task['resends'] + 1}\n")
        finally:
            os.close(fd)
    except FileNotFoundError:
        return False
    return True


def run_bounded(argv, *, timeout, env=None, body=None) -> tuple[int | None, str]:
    """No subprocess output is copied into logs. Timeout kills its process group."""
    # File-backed stdin ensures a child that retains a descriptor cannot
    # make the parent wait for a pipe to drain after the timeout.
    try:
        with (tempfile.TemporaryFile() if body is not None else nullcontext(None)) as payload:
            if payload is not None:
                payload.write(body.encode("utf-8"))
                payload.seek(0)
            proc = subprocess.Popen(argv, env=env, start_new_session=True,
                                    stdin=payload if payload is not None else subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                rc = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    return None, f"timed out after {timeout}s; process exit unconfirmed"
                return None, f"timed out after {timeout}s"
            return rc, f"exited {rc}"
    except OSError:
        return None, "could not start"


def resend(lib: Path, mailbox: Path, task: dict) -> tuple[int | None, str]:
    # Only the canonical task ID is typed; task titles and results stay data.
    line = f"Task {task['id']}. Details: task.sh read {task['id']}"
    env = {**os.environ, "AGENT_MAILBOX": str(mailbox), "AGENT_ROLE": "teamflow-reconciler",
           "AGENT_LIB_DIR": str(lib)}
    pane = os.environ.get("TEAMFLOW_PANE_CMD")  # tests substitute a fake pane
    argv = [pane] if pane else ["bash", str(lib / "pane.sh")]
    return run_bounded(argv + ["send-to", task["to"], line], env=env, timeout=PANE_TIMEOUT)


def atomic_write(path: Path, text: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=f".{path.name}.",
                                     delete=False) as stream:
        tmp = Path(stream.name)
        try:
            stream.write(text)
            stream.close()
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)


def reconcile(mailbox: Path, lib: Path, args) -> int:
    rows = survey(mailbox, args)
    acted = 0
    failed = False
    for r in rows:
        if r["flag"] != "not-picked-up":
            continue
        if args.max_resends == 0:
            print(f"{r['id']}: pickup pending for {r['to']}; automatic resends are disabled")
            continue
        if r["resends"] >= args.max_resends:
            print(f"{r['id']}: not picked up by {r['to']} after {r['resends']} resend attempt(s); "
                  f"check the worker: teamflow pane tail {r['to']}")
            continue
        if args.dry_run:
            print(f"{r['id']}: would resend to {r['to']}")
            continue
        task = load_task(r["_dir"])
        if (task is None or task["identity"] != r["_identity"] or task["to"] != r["to"] or
                task["resends"] >= args.max_resends or
                classify(task, now(), args.pickup, args.done, args.long) != "not-picked-up"):
            continue
        if not record_attempt(task):
            continue
        current = load_task(task["dir"])
        if (current is None or current["identity"] != task["identity"] or
                current["status"] != "assigned" or current["claimed"] or current["to"] != task["to"]):
            continue
        rc, detail = resend(lib, mailbox, current)
        if rc == 0:
            print(f"{r['id']}: sent pickup reminder to {r['to']}; check task state for acknowledgment")
            acted += 1
        else:
            print(f"{r['id']}: resend to {r['to']} failed: {detail}")
            failed = True

    # Resends may have changed task state; use the latest view for attention.
    waiting = [r for r in survey(mailbox, args) if r["flag"] in ("awaiting-delegator", "long-running")]
    att = mailbox / "attention"
    seen = mailbox / "attention.sum"
    if not waiting:
        if not args.dry_run:
            for p in (att, seen):
                p.unlink(missing_ok=True)
        print(f"-- {acted} resend(s); nothing waits on the Delegator")
        return int(failed)
    for w in waiting:
        print(f"{w['id']}: {w['flag']} ({w['to']}, {w['title']})")
    if args.dry_run:
        print("-- dry run: attention not written")
        return 0
    body = json.dumps({"mailbox": str(mailbox), "at": stamp(now()),
                       "tasks": [public(r) for r in waiting]}, indent=2)
    atomic_write(att, body + "\n")
    hook = args.on_attention or os.environ.get("TEAMFLOW_ON_ATTENTION", "")
    # A different hook or task generation must receive its own notice.
    key = hashlib.sha256(json.dumps([hook, [(w["id"], w["_identity"], w["flag"])
                                            for w in waiting]]).encode()).hexdigest()
    if hook and (not seen.exists() or seen.read_text().strip() != key):
        rc, detail = run_bounded(["/bin/sh", "-c", hook], body=body, timeout=HOOK_TIMEOUT)
        print(f"-- on-attention hook {detail}")
        if rc != 0:
            return 1  # only successful delivery advances attention.sum
        atomic_write(seen, key + "\n")
    print(f"-- {acted} resend(s); {len(waiting)} task(s) wait on the Delegator: {att}")
    return int(failed)


def cmd_nudge(mailbox: Path, lib: Path, args) -> int:
    if args.dry_run:
        return reconcile(mailbox, lib, args)
    fd = os.open(mailbox / "nudge.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("-- another nudge is running for this repository; skipped")
            return 0
        return reconcile(mailbox, lib, args)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="teamflow status|nudge")
    ap.add_argument("mode", choices=["status", "nudge"])
    ap.add_argument("lib", type=Path, help="teamflow's .agents/lib directory")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-resends", type=int, default=0,
                    help="opt in to at most N pickup attempts per task (default 0)")
    ap.add_argument("--on-attention", default="")
    ap.add_argument("--pickup", type=float, default=10, help="minutes before an assigned task is stuck")
    ap.add_argument("--done", type=float, default=10, help="minutes a done result may wait unread")
    ap.add_argument("--long", type=float, default=120, help="minutes before in-progress is long-running")
    args = ap.parse_args(argv)
    if args.max_resends < 0:
        ap.error("--max-resends must be nonnegative")
    mailbox = mailbox_dir()
    if args.mode == "status" and args.json:
        return cmd_status(mailbox, args)
    if not (mailbox / "tasks").is_dir():
        print("no teamflow mailbox in this repository yet")
        return 0
    if args.mode == "status":
        return cmd_status(mailbox, args)
    return cmd_nudge(mailbox, args.lib, args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
