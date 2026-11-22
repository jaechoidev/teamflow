"""Cross-clone handoffs; only local Git and the task helpers run."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts/teamflow"
TASK = ROOT / ".agents/lib/task.sh"


class HandoffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_CONFIG_SYSTEM": "/dev/null",
                    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
                    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}
        for k in ["AGENT_MAILBOX", "AGENT_ROLE", "AGENT_ID"]:
            self.env.pop(k, None)
        self.seed = self.base / "seed"
        self.git(self.base, "init", "--initial-branch=main", str(self.seed))
        self.git(self.seed, "commit", "--allow-empty", "-m", "seed")

    def run_cmd(self, cwd, *argv, data="", actor=None, check=True):
        env = dict(self.env)
        if actor:
            env.update(AGENT_ROLE=actor, AGENT_MAILBOX=str(cwd / ".git/teamflow"))
        p = subprocess.run(argv, cwd=cwd, env=env, input=data, text=True, capture_output=True)
        if check:
            self.assertEqual(p.returncode, 0, p.stderr)
        return p

    def git(self, cwd, *argv):
        return self.run_cmd(cwd, "git", *argv).stdout

    def clone(self, name, source=None):
        p = self.base / name
        self.git(self.base, "clone", "--no-hardlinks", str(source or self.seed), str(p))
        return p

    def task(self, cwd, actor, *args, data="", check=True):
        return self.run_cmd(cwd, "bash", str(TASK), *args, actor=actor, data=data, check=check)

    def handoff(self, cwd, *args, check=True):
        return self.run_cmd(cwd, "bash", str(TOOL), "handoff", *args, check=check)

    def complete(self, repo, worker, label):
        alias = self.task(repo, "delegator", "new", worker, label, data="PRIVATE_TASK_BODY").stdout.strip()
        self.task(repo, worker, "done", alias, data="PRIVATE_WORKER_RESULT")
        return alias

    def test_independent_clones_and_fresh_reader(self):
        exported = []
        for name, worker in (("studio", "dev-s"), ("laptop", "dev-l")):
            repo = self.clone(name)
            self.git(repo, "config", "--local", "teamflow.handoffCreator", "Sample Person")
            self.git(repo, "config", "--local", "teamflow.handoffOrigin", name)
            alias = self.complete(repo, worker, name)
            self.assertEqual(alias, "T-0001")
            self.assertEqual(self.git(repo, "status", "--porcelain"), "")
            rel = self.handoff(repo, "export", alias, f"{name} result summary").stdout.strip()
            p = repo / rel
            self.assertEqual(p.parent.relative_to(repo).as_posix(), "docs/handoffs/teamflow")
            d = json.loads(p.read_text())
            self.assertEqual(p.stem, d["uuid"])
            self.assertEqual(d["task"], alias)
            self.assertEqual(d["title"], name)
            self.assertEqual(d["creator"], "Sample Person")
            self.assertEqual(d["origin"], name)
            self.assertEqual(d["completed_by"], worker)
            self.assertEqual(d["summary"], f"{name} result summary")
            self.assertNotIn("PRIVATE_", p.read_text())
            self.assertNotEqual(self.handoff(repo, "export", alias, "overwrite", check=False).returncode, 0)
            self.assertEqual(json.loads(p.read_text()), d)
            self.task(repo, "delegator", "release", alias)
            mailbox = repo / ".git/teamflow"
            (mailbox / "note-queue" / "active").write_text(alias + "\n")
            self.task(repo, "notetaker", "noted", alias, "Finished")
            self.assertFalse((mailbox / "tasks" / alias).exists())
            self.git(repo, "add", rel)
            self.git(repo, "commit", "-m", "test handoff")
            exported.append((repo, d))
        self.assertNotEqual(exported[0][1]["uuid"], exported[1][1]["uuid"])
        merged = self.clone("merged", exported[0][0])
        self.git(merged, "fetch", str(exported[1][0]), "main")
        self.git(merged, "merge", "--no-edit", "FETCH_HEAD")
        reader = self.clone("reader", merged)
        self.assertFalse((reader / ".git/teamflow").exists())
        for _, expected in exported:
            self.assertEqual(json.loads(self.handoff(reader, "read", expected["uuid"]).stdout), expected)
        for bad in ("T-0001", "../T-0001", exported[0][1]["uuid"][:8]):
            self.assertNotEqual(self.handoff(reader, "read", bad, check=False).returncode, 0)
        self.assertFalse((reader / ".git/teamflow").exists())

    def test_legacy_and_local_only(self):
        repo = self.clone("local")
        alias = self.complete(repo, "dev-s", "Local task")
        (repo / ".git/teamflow/tasks" / alias / "identity.json").unlink()
        self.assertIn("PRIVATE_WORKER_RESULT", self.task(repo, "delegator", "read", alias).stdout)
        self.assertNotEqual(self.handoff(repo, "export", alias, "Summary", check=False).returncode, 0)
        self.task(repo, "delegator", "release", alias)
        (repo / ".git/teamflow/note-queue/active").write_text(alias + "\n")
        self.task(repo, "notetaker", "noted", alias, "No durable note")
        self.assertFalse((repo / ".git/teamflow/tasks" / alias).exists())
        self.assertFalse((repo / "docs").exists())
        self.assertEqual(self.git(repo, "status", "--porcelain"), "")

    def test_rejects_mismatched_uuid_and_symlink_on_read(self):
        repo = self.clone("invalid")
        alias = self.complete(repo, "dev-s", "Task")
        rel = self.handoff(repo, "export", alias, "Summary").stdout.strip()
        p = repo / rel
        uid = p.stem
        d = json.loads(p.read_text())
        d["uuid"] = "b4267481-c5c0-4a57-8ad8-092fb5e82d8b"
        p.write_text(json.dumps(d))
        self.assertNotEqual(self.handoff(repo, "read", uid, check=False).returncode, 0)
        p.unlink()
        outside = self.base / "outside.json"
        outside.write_text("outside sentinel")
        p.symlink_to(outside)
        refused = self.handoff(repo, "read", uid, check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertNotIn("outside sentinel", refused.stdout + refused.stderr)

    def test_copied_install_ships_identity_and_lookup(self):
        repo = self.clone("installed-user")
        data, bin_dir = self.base / "installed-data", self.base / "bin"
        self.run_cmd(repo, sys.executable, str(ROOT / "scripts/teamflow_scaffold.py"), "install-tool",
                     str(ROOT), str(data), str(bin_dir), "0")
        tool = bin_dir / "teamflow"
        self.assertNotEqual(tool.resolve(), TOOL.resolve())
        alias = self.run_cmd(repo, "bash", str(tool), "task", "new", "delegator", "Installed task").stdout.strip()
        self.run_cmd(repo, "bash", str(tool), "task", "done", alias, data="Local result")
        rel = self.run_cmd(repo, "bash", str(tool), "handoff", "export", alias, "Installed summary").stdout.strip()
        uid = (repo / rel).stem
        item = json.loads(self.run_cmd(repo, "bash", str(tool), "handoff", "read", uid).stdout)
        self.assertEqual(item["completed_by"], "delegator")
        self.assertNotIn("creator", item)
        self.assertNotIn("origin", item)

    def test_bad_optional_label_does_not_break_normal_task_creation(self):
        repo = self.clone("bad-label")
        self.git(repo, "config", "--local", "teamflow.handoffCreator", b"invalid-\xff")
        alias = self.complete(repo, "dev-s", "Task")
        rel = self.handoff(repo, "export", alias, "Summary").stdout.strip()
        self.assertNotIn("creator", json.loads((repo / rel).read_text()))

    def test_unfinished_and_symlink_do_not_export(self):
        repo = self.clone("unsafe")
        alias = self.task(repo, "delegator", "new", "dev-s", "Task").stdout.strip()
        self.assertNotEqual(self.handoff(repo, "export", alias, "Summary", check=False).returncode, 0)
        self.task(repo, "dev-s", "done", alias, data="Result")
        outside = self.base / "outside"
        outside.mkdir()
        (repo / "docs").symlink_to(outside, target_is_directory=True)
        self.assertNotEqual(self.handoff(repo, "export", alias, "Summary", check=False).returncode, 0)
        self.assertEqual(list(outside.iterdir()), [])
