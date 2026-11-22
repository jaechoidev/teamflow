"""Publish or read a deliberately curated Teamflow handoff, with no Git writes."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import unicodedata
from uuid import UUID, uuid4

PARTS = ("docs", "handoffs", "teamflow")
ALIAS = re.compile(r"T-[0-9]{4}", re.ASCII)
LABEL = re.compile(r"[\w.][\w. -]{0,79}", re.UNICODE)
WORKER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}", re.ASCII)
LIMIT = 64 * 1024


def require(ok, message):
    if not ok:
        raise ValueError(message)


def canonical(value):
    try:
        u = UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise ValueError("a full canonical task UUID is required") from None
    require(u.version == 4 and str(u) == value, "a full canonical task UUID is required")
    return value


def text(value, size, multiline=False):
    if not isinstance(value, str) or not value.strip() or len(value) > size:
        return False
    return all(not unicodedata.category(c).startswith("C") or
               (multiline and c in "\n\t") for c in value)


@contextmanager
def directory(root, parts, create=False):
    """Open without following symlinks, including in intermediate components."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.dup(root) if isinstance(root, int) else os.open(root, flags)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, dir_fd=fd)
                except FileExistsError:
                    pass
            nxt = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = nxt
        yield fd
    finally:
        os.close(fd)


def read_file(fd, name):
    with os.fdopen(os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                           dir_fd=fd), encoding="utf-8") as f:
        st = os.fstat(f.fileno())
        require(stat.S_ISREG(st.st_mode) and st.st_size <= LIMIT, "invalid handoff input file")
        data = f.read(LIMIT + 1)
        require(len(data) <= LIMIT, "handoff input file too large")
        return data


def read_json(fd, name):
    record = json.loads(read_file(fd, name))
    require(isinstance(record, dict), "invalid handoff record")
    return record


def safe_label(value):
    return isinstance(value, str) and bool(LABEL.fullmatch(value))


def local_label(key):
    try:
        result = subprocess.run(["git", "config", "--local", "--get", key],
                                capture_output=True, encoding="utf-8", check=False)
    except UnicodeDecodeError:
        return None
    label = result.stdout.rstrip("\n")
    return label if result.returncode == 0 and safe_label(label) else None


def new_identity(title):
    record = {"uuid": str(uuid4()), "title": title}
    for field in ("creator", "origin"):
        label = local_label(f"teamflow.handoff{field.title()}")
        if label is not None:
            record[field] = label
    print(json.dumps(record))


def validate(record):
    required = {"schema_version", "uuid", "task", "title", "completed_by", "summary"}
    require(required <= record.keys() <= required | {"creator", "origin"}, "invalid handoff fields")
    require(type(record["schema_version"]) is int and record["schema_version"] == 1,
            "unsupported handoff schema")
    canonical(record["uuid"])
    require(isinstance(record["task"], str) and ALIAS.fullmatch(record["task"]), "invalid task label")
    require(text(record["title"], 500), "handoff title must be a safe single line (max 500 characters)")
    require(isinstance(record["completed_by"], str) and WORKER.fullmatch(record["completed_by"]),
            "invalid completing worker")
    require(text(record["summary"], 4000, multiline=True), "provide a nonempty safe summary (max 4000 characters)")
    require(all(safe_label(record[k]) for k in ("creator", "origin") if k in record),
            "invalid creator or origin label")


def emit_record(fd, name, record):
    tmp = f".handoff-{uuid4()}.tmp"
    out = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=fd)
    try:
        with os.fdopen(out, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
            f.write("\n")
        try:
            os.link(tmp, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        except FileExistsError:
            raise ValueError("handoff already exists; inspect the existing record") from None
    finally:
        os.unlink(tmp, dir_fd=fd)


def export(root, common, alias, summary):
    require(ALIAS.fullmatch(alias), "export requires a local T-#### task label")
    require(text(summary, 4000, multiline=True), "provide a nonempty safe summary (max 4000 characters)")
    with directory(common, ("teamflow", "tasks", alias)) as fd:
        require(read_file(fd, "status").strip() == "done", "only completed tasks can be exported")
        try:
            identity = read_json(fd, "identity.json")
        except FileNotFoundError:
            raise ValueError("legacy task has no UUID; leave it local") from None
        with directory(fd, ("claim", "completed")) as completion:
            worker = read_file(completion, "by").strip()
    record = {"schema_version": 1, "task": alias, "completed_by": worker, "summary": summary}
    for field in ("uuid", "title", "creator", "origin"):
        if field in identity:
            record[field] = identity[field]
    validate(record)
    name = record["uuid"] + ".json"
    with directory(root, PARTS, create=True) as fd:
        emit_record(fd, name, record)
    print(Path(*PARTS, name))


def read(root, uid):
    name = canonical(uid) + ".json"
    try:
        with directory(root, PARTS) as fd:
            record = read_json(fd, name)
    except FileNotFoundError:
        raise ValueError("no shared handoff found for this UUID") from None
    validate(record)
    require(record["uuid"] == uid, "handoff UUID does not match its file path")
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        argv = sys.argv[1:]
        if len(argv) == 2 and argv[0] == "new-identity":
            new_identity(argv[1])
        elif len(argv) == 5 and argv[0] == "export":
            export(*argv[1:])
        elif len(argv) == 3 and argv[0] == "read":
            read(*argv[1:])
        else:
            raise ValueError("use teamflow handoff export <T-####> <summary> or handoff read <full-uuid>")
    except FileNotFoundError:
        sys.exit("teamflow: no completed local task to export; export before release/cleanup")
    except (OSError, UnicodeError, json.JSONDecodeError):
        sys.exit("teamflow: cannot safely access the handoff or local task")
    except ValueError as exc:
        sys.exit(f"teamflow: {exc}")
