#!/usr/bin/env python3
"""Install, refresh, and migrate the teamflow scaffold in a project.

install <tool-home> <root>
    Copy the shipped files into the project. A file the project already has
    is replaced only while it is unmodified: it matches the version recorded
    in .agents/teamflow-manifest or any version committed in the tool home.
    A locally edited file is kept, and the shipped copy lands next to it as
    <file>.new. Prints the installed project paths.
pristine <tool-home> <root> <path>
    Exit 0 when the project file is an unmodified shipped version.
migrate <root> <git-common-dir>
    Move state left by teamflow versions that used the name ai-team:
    .git/ai-team, .ai-team-worktrees/, and ai-team/<id> branches.
"""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

SKILLS = ("teamflow", "teamflow-resume", "teamflow-kill", "teamflow-workers")
SCRIPTS = ("scripts/teamflow", "scripts/teamflow_workers.py", "scripts/teamflow_scaffold.py")
MANIFEST = ".agents/teamflow-manifest"
MANIFEST_HEADER = (
    "# Files teamflow installed here, as git blob ids. teamflow replaces a file\n"
    "# only while it still matches the version recorded here or another\n"
    "# version the tool home shipped. Edited files are kept.\n"
)
LEGACY = "ai-team"
MAILBOX = "teamflow"
WORKTREES = ".teamflow-worktrees"
LEGACY_WORKTREES = ".ai-team-worktrees"


class ScaffoldError(Exception):
    pass


def git(*args, check=True):
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    if check and result.returncode:
        raise ScaffoldError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result


def blob_id(path, algorithm="sha1"):
    data = Path(path).read_bytes()
    digest = hashlib.new(algorithm)
    digest.update(b"blob %d\0" % len(data))
    digest.update(data)
    return digest.hexdigest()


def shipped_files(home):
    """(source path in the tool home, destination path in the project) pairs."""
    pairs = [(rel, rel) for rel in SCRIPTS]
    pairs.append((".agents/AGENTS-SECTION.md", ".agents/AGENTS-SECTION.md"))
    for folder, suffix in ((".agents/lib", ".sh"), (".agents/roles", ".md"), (".agents/doc-templates", ".md")):
        for source in sorted((home / folder).glob(f"*{suffix}")):
            pairs.append((f"{folder}/{source.name}", f"{folder}/{source.name}"))
    skills = home / "skills"
    if not skills.is_dir():
        skills = home / ".agents/skills"
    for skill in SKILLS:
        if not (skills / skill / "SKILL.md").is_file():
            raise ScaffoldError(f"tool files incomplete at {home} (missing skills/{skill}/SKILL.md)")
        for source in sorted((skills / skill).rglob("*")):
            if not source.is_file() or source.name.endswith(".new") or source.name == ".DS_Store":
                continue
            rel = source.relative_to(skills / skill).as_posix()
            src = source.relative_to(home).as_posix()
            pairs += [(src, f".agents/skills/{skill}/{rel}"), (src, f".claude/skills/{skill}/{rel}")]
    missing = [src for src, _ in pairs if not (home / src).is_file()]
    if missing:
        raise ScaffoldError(f"tool files incomplete at {home} (missing {', '.join(missing)})")
    return pairs


def shipped_history(home):
    """Blob ids of every committed version of each tool home path."""
    top = git("-C", str(home), "rev-parse", "--show-toplevel", check=False)
    if top.returncode or Path(top.stdout.strip()).resolve() != home.resolve():
        return "sha1", {}
    fmt = git("-C", str(home), "rev-parse", "--show-object-format", check=False).stdout.strip() or "sha1"
    log = git("-C", str(home), "log", "--all", "--format=", "--raw", "--no-abbrev", "--no-renames", check=False)
    history = {}
    for line in log.stdout.splitlines():
        if not line.startswith(":") or "\t" not in line:
            continue
        meta, path = line.split("\t", 1)
        history.setdefault(path, set()).update(meta.split()[2:4])
    return fmt, history


def read_manifest(root):
    path = root / MANIFEST
    entries = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            if line and not line.startswith("#") and " " in line:
                blob, rel = line.split(" ", 1)
                entries[rel] = blob
    return entries


def write_atomic(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.chmod(temp, mode)
        # A new inode: a shell still reading the old launcher keeps its copy.
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def is_pristine(dst, src_rel, dst_rel, manifest, fmt, history):
    if manifest.get(dst_rel) == blob_id(dst):
        return True
    return blob_id(dst, fmt) in history.get(src_rel, ())


def install(home, root):
    pairs = shipped_files(home)
    manifest = read_manifest(root)
    fmt, history = shipped_history(home)
    added, updated, kept = [], [], []
    for src_rel, dst_rel in pairs:
        src, dst = home / src_rel, root / dst_rel
        staged = dst.with_name(dst.name + ".new")
        if src.resolve() == dst.resolve():
            continue
        data, mode = src.read_bytes(), src.stat().st_mode & 0o777
        if not dst.exists():
            write_atomic(dst, data, mode)
            added.append(dst_rel)
        elif dst.read_bytes() == data:
            if dst.stat().st_mode & 0o111 != mode & 0o111:
                os.chmod(dst, mode)
        elif is_pristine(dst, src_rel, dst_rel, manifest, fmt, history):
            write_atomic(dst, data, mode)
            updated.append(dst_rel)
        else:
            if not staged.exists() or staged.read_bytes() != data:
                write_atomic(staged, data, mode)
            kept.append(dst_rel)
            continue
        manifest[dst_rel] = blob_id(src)
        if staged.exists():
            staged.unlink()  # the shipped copy is in place, so the staged one is obsolete
    # The tool home's own copies need no manifest: its history covers them.
    manifest_path = root / MANIFEST
    if home != root:
        body = MANIFEST_HEADER + "".join(f"{manifest[rel]} {rel}\n" for rel in sorted(manifest))
        if not manifest_path.is_file() or manifest_path.read_text() != body:
            write_atomic(manifest_path, body.encode(), 0o644)
    if updated:
        say(f"updated {len(updated)} unmodified teamflow files from {home}: {', '.join(updated)}")
    for rel in kept:
        say(f"{rel} has local edits, so it was kept. The shipped copy is at {rel}.new")
    for _, rel in pairs:
        print(rel)
    if manifest_path.is_file():
        print(MANIFEST)


def pristine(home, root, rel):
    dst = root / rel
    if not dst.is_file():
        return False
    fmt, history = shipped_history(home)
    return is_pristine(dst, rel, rel, read_manifest(root), fmt, history)


def live_session(record):
    """Session name when the launch record still matches a running tmux session."""
    if not record.is_file():
        return ""
    values = dict(line.split("=", 1) for line in record.read_text().splitlines() if "=" in line)
    name, created = values.get("SESSION", ""), values.get("CREATED", "")
    if not name:
        return ""
    try:
        listing = subprocess.run(["tmux", "list-sessions", "-F", "#{session_created} #{session_name}"],
                                 capture_output=True, text=True).stdout
    except FileNotFoundError:
        return ""
    for line in listing.splitlines():
        stamp, _, running = line.partition(" ")
        if running == name and stamp == created:
            return name
    return ""


def legacy_worktrees(root):
    """Registered worktrees under .ai-team-worktrees/, as (path, prunable) pairs."""
    listing = git("-C", str(root), "worktree", "list", "--porcelain").stdout
    base = (root / LEGACY_WORKTREES).resolve()
    found = []
    for block in listing.strip().split("\n\n"):
        fields = [line.split(" ", 1) for line in block.splitlines()]
        info = {pair[0]: (pair[1] if len(pair) > 1 else "") for pair in fields}
        path = Path(info.get("worktree", ""))
        if path.parent.resolve() == base:
            found.append((path, "prunable" in info))
    return found


def migrate(root, common):
    legacy, mailbox = common / LEGACY, common / MAILBOX
    old_trees, new_trees = root / LEGACY_WORKTREES, root / WORKTREES
    branches = git("-C", str(root), "for-each-ref", "--format=%(refname:strip=2)",
                   f"refs/heads/{LEGACY}/").stdout.split()
    trees = legacy_worktrees(root)
    if not (legacy.exists() or old_trees.exists() or branches or trees):
        return
    session = live_session(legacy / "active")
    if session:
        raise ScaffoldError(
            f"session {session} runs from the old .git/{LEGACY} state. Nothing was changed. "
            "Stop it with scripts/teamflow --kill, then run this command again to move that "
            f"state to .git/{MAILBOX}.")
    conflicts = [str(new_trees / path.name) for path, prunable in trees
                 if not prunable and (new_trees / path.name).exists()]
    if legacy.is_dir() and mailbox.is_dir():
        conflicts += [str(mailbox / child.name) for child in legacy.iterdir()
                      if child.name != "workers.lock" and (mailbox / child.name).exists()]
    if conflicts:
        raise ScaffoldError("cannot move old teamflow state, because these paths already exist: "
                            + ", ".join(conflicts))
    if legacy.is_dir():
        mailbox.mkdir(exist_ok=True)
        for child in legacy.iterdir():
            target = mailbox / child.name
            if target.exists():
                child.unlink()  # an empty workers.lock, recreated on demand
            else:
                child.rename(target)
        legacy.rmdir()
        say(f"moved the mailbox from .git/{LEGACY} to .git/{MAILBOX}")
    for path, prunable in trees:
        if prunable:
            continue
        new_trees.mkdir(exist_ok=True)
        git("-C", str(root), "worktree", "move", str(path), str(new_trees / path.name))
        say(f"moved worktree {LEGACY_WORKTREES}/{path.name} to {WORKTREES}/{path.name}")
    for branch in branches:
        target = MAILBOX + branch[len(LEGACY):]
        if git("-C", str(root), "show-ref", "--verify", "--quiet", f"refs/heads/{target}", check=False).returncode == 0:
            say(f"kept branch {branch}, because {target} already exists")
            continue
        git("-C", str(root), "branch", "-m", branch, target)
        say(f"renamed branch {branch} to {target}")
    if old_trees.is_dir():
        (old_trees / ".DS_Store").unlink(missing_ok=True)
        try:
            old_trees.rmdir()
        except OSError:
            say(f"{LEGACY_WORKTREES}/ still holds files that are not worktrees. Review and remove it by hand")
    exclude = common / "info" / "exclude"
    if exclude.is_file() and not old_trees.exists():
        lines = exclude.read_text().splitlines(keepends=True)
        kept = [line for line in lines if line.strip() != f"{LEGACY_WORKTREES}/"]
        if kept != lines:
            exclude.write_text("".join(kept))


def say(message):
    print(f"teamflow: {message}", file=sys.stderr)


def main(argv):
    if len(argv) == 3 and argv[0] == "install":
        install(Path(argv[1]).resolve(), Path(argv[2]).resolve())
    elif len(argv) == 4 and argv[0] == "pristine":
        return 0 if pristine(Path(argv[1]).resolve(), Path(argv[2]).resolve(), argv[3]) else 1
    elif len(argv) == 3 and argv[0] == "migrate":
        migrate(Path(argv[1]), Path(argv[2]))
    else:
        raise ScaffoldError("usage: teamflow_scaffold.py install <tool-home> <root> | "
                            "pristine <tool-home> <root> <path> | migrate <root> <git-common-dir>")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (ScaffoldError, OSError) as exc:
        print(f"teamflow: {exc}", file=sys.stderr)
        sys.exit(1)
