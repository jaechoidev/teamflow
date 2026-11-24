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
reconciler: it is idempotent and safe to run from launchd or cron every few
minutes. It re-sends the pickup line to a worker that has not taken its
task (at most --max-resends times, logged in the task's events), and it
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
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

NOW_ENV = "TEAMFLOW_NOW"  # tests pin the clock: ISO-8601 UTC


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


def load_tasks(mailbox: Path) -> list[dict]:
    tasks = []
    root = mailbox / "tasks"
    if not root.is_dir():
        return tasks
    for d in sorted(root.glob("T-*")):
        if not d.is_dir():
            continue
        h = header(d / "task.md")
        try:
            status = (d / "status").read_text().strip()
        except OSError:
            status = "unknown"
        evs = events(d)
        created = None
        try:
            created = parse_ts(h.get("created", ""))
        except ValueError:
            pass
        last = max([t for t, _ in evs] + ([created] if created else []), default=None)
        released = None
        try:
            for line in (d / "released").read_text().splitlines():
                if line.startswith("released:"):
                    released = parse_ts(line.split(":", 1)[1])
        except (OSError, ValueError):
            pass
        done_at = next((t for t, w in reversed(evs) if w.startswith("done by")), None)
        resends = sum(1 for _, w in evs if w.startswith("nudged"))
        tasks.append({
            "id": d.name, "dir": d, "from": h.get("from", ""), "to": h.get("to", ""),
            "title": h.get("title", ""), "status": status, "created": created,
            "last": last, "done_at": done_at, "released": released, "resends": resends,
        })
    return tasks


def minutes_since(t: dt.datetime | None, at: dt.datetime) -> float | None:
    return None if t is None else (at - t).total_seconds() / 60


def classify(task: dict, at: dt.datetime, pickup: float, done: float, long: float) -> str | None:
    """The flag for a task that stopped moving, or None."""
    if task["status"] == "assigned":
        age = minutes_since(task["last"], at)
        if age is not None and age >= pickup and task["to"] != "delegator":
            return "not-picked-up"
    elif task["status"] == "in-progress":
        age = minutes_since(task["last"], at)
        if age is not None and age >= long:
            return "long-running"
    elif task["status"] == "done" and task["released"] is None and task["from"] == "delegator":
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
            "status": t["status"], "released": t["released"] is not None,
            "age_min": None if age is None else round(age, 1),
            "resends": t["resends"], "flag": flag, "_dir": t["dir"],
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


def append_event(task_dir: Path, text: str) -> None:
    with open(task_dir / "events", "a") as f:
        f.write(f"{stamp(now())} {text}\n")


def resend(lib: Path, mailbox: Path, row: dict) -> tuple[bool, str]:
    line = f"Task {row['id']}: {row['title']}. Details: task.sh read {row['id']}"
    env = {**os.environ, "AGENT_MAILBOX": str(mailbox), "AGENT_ROLE": "teamflow-reconciler",
           "AGENT_LIB_DIR": str(lib)}
    pane = os.environ.get("TEAMFLOW_PANE_CMD")  # tests substitute a fake pane
    argv = [pane] if pane else ["bash", str(lib / "pane.sh")]
    p = subprocess.run(argv + ["send-to", row["to"], line], env=env, capture_output=True, text=True)
    return p.returncode == 0, (p.stderr or p.stdout).strip()


def cmd_nudge(mailbox: Path, lib: Path, args) -> int:
    rows = survey(mailbox, args)
    acted = 0
    for r in rows:
        if r["flag"] != "not-picked-up":
            continue
        if r["resends"] >= args.max_resends:
            print(f"{r['id']}: not picked up by {r['to']} after {r['resends']} resend(s); "
                  f"check the worker: teamflow pane tail {r['to']}")
            continue
        if args.dry_run:
            print(f"{r['id']}: would resend to {r['to']}")
            continue
        ok, detail = resend(lib, mailbox, r)
        if ok:
            append_event(r["_dir"], f"nudged by teamflow (resend {r['resends'] + 1})")
            print(f"{r['id']}: resent to {r['to']}")
            acted += 1
        else:
            append_event(r["_dir"], f"nudge failed: {detail[:200]}")
            print(f"{r['id']}: resend to {r['to']} failed: {detail}")

    waiting = [public(r) for r in rows if r["flag"] in ("awaiting-delegator", "long-running")]
    att = mailbox / "attention"
    seen = mailbox / "attention.sum"
    if not waiting:
        if not args.dry_run:
            for p in (att, seen):
                p.unlink(missing_ok=True)
        print(f"-- {acted} resend(s); nothing waits on the Delegator")
        return 0
    body = json.dumps({"mailbox": str(mailbox), "at": stamp(now()), "tasks": waiting}, indent=2)
    key = hashlib.sha256(json.dumps([(w["id"], w["flag"]) for w in waiting]).encode()).hexdigest()
    changed = not seen.exists() or seen.read_text().strip() != key
    for w in waiting:
        print(f"{w['id']}: {w['flag']} ({w['to']}, {w['title']})")
    if args.dry_run:
        print("-- dry run: attention not written")
        return 0
    tmp = mailbox / f".attention.{os.getpid()}"
    tmp.write_text(body + "\n")
    tmp.replace(att)
    hook = args.on_attention or os.environ.get("TEAMFLOW_ON_ATTENTION", "")
    if changed and hook:
        p = subprocess.run(hook, shell=True, input=body, text=True, capture_output=True)
        print(f"-- on-attention hook exited {p.returncode}")
        if p.returncode != 0:
            return 1  # leave attention.sum unchanged so the next run retries
    if changed:
        seen.write_text(key + "\n")
    print(f"-- {acted} resend(s); {len(waiting)} task(s) wait on the Delegator: {att}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="teamflow status|nudge")
    ap.add_argument("mode", choices=["status", "nudge"])
    ap.add_argument("lib", type=Path, help="teamflow's .agents/lib directory")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-resends", type=int, default=2)
    ap.add_argument("--on-attention", default="")
    ap.add_argument("--pickup", type=float, default=10, help="minutes before an assigned task is stuck")
    ap.add_argument("--done", type=float, default=10, help="minutes a done result may wait unread")
    ap.add_argument("--long", type=float, default=120, help="minutes before in-progress is long-running")
    args = ap.parse_args(argv)
    mailbox = mailbox_dir()
    if not (mailbox / "tasks").is_dir():
        print("no teamflow mailbox in this repository yet")
        return 0
    if args.mode == "status":
        return cmd_status(mailbox, args)
    return cmd_nudge(mailbox, args.lib, args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
