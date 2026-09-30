#!/usr/bin/env python3
"""Integration checks for worker instances and the launcher."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]


class WorkersTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "scripts").mkdir()
        for name in ("teamflow", "teamflow_workers.py"):
            shutil.copy2(SOURCE / "scripts" / name, self.root / "scripts" / name)
        shutil.copy2(SOURCE / "teamflow.conf", self.root / "teamflow.conf")
        roles = self.root / ".agents" / "roles"
        roles.mkdir(parents=True)
        for source in (SOURCE / ".agents" / "roles").glob("*.md"):
            shutil.copy2(source, roles / source.name)
        (self.root / "seed").write_text("seed")
        subprocess.run(["git", "-C", str(self.root), "add", "seed"], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Test",
                        "-c", "user.email=test@example.com", "commit", "-qm", "seed"], check=True)

    def teamflow(self, *args, env=None):
        return subprocess.run(["bash", "scripts/teamflow", *args], cwd=self.root,
                              env=env, text=True, capture_output=True)

    def test_add_remove_and_unfinished_task_guard(self):
        config = (self.root / "teamflow.conf").read_text()
        self.assertEqual(config.count("role_file = .agents/roles/developer.md"), 3)
        added = self.teamflow("workers", "add", "developer-m")
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertIn("developer-m-1", added.stdout)
        added = self.teamflow("workers", "add", "developer-m")
        self.assertIn("developer-m-2", added.stdout)
        task = self.root / ".git" / "ai-team" / "tasks" / "T-0001"
        task.mkdir(parents=True)
        (task / "task.md").write_text("to:      developer-m-1\n")
        (task / "status").write_text("assigned\n")
        before = (self.root / "teamflow.conf").read_bytes()
        refused = self.teamflow("workers", "remove", "developer-m-1")
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(before, (self.root / "teamflow.conf").read_bytes())
        (task / "status").write_text("done\n")
        self.assertEqual(self.teamflow("workers", "remove", "developer-m-1").returncode, 0)
        added = self.teamflow("workers", "add", "developer-m")
        self.assertIn("developer-m-3", added.stdout)

    def test_migrate_preserves_worktree_and_session(self):
        legacy = subprocess.check_output(["git", "show", "HEAD:teamflow.conf"],
                                         cwd=SOURCE, text=True)
        (self.root / "teamflow.conf").write_text(legacy)
        worktree = self.root / ".ai-team-worktrees" / "dev-senior"
        worktree.parent.mkdir()
        subprocess.run(["git", "-C", str(self.root), "worktree", "add", "-qb",
                        "ai-team/dev-senior", str(worktree)], check=True)
        sessions = self.root / ".git" / "ai-team" / "sessions"
        sessions.mkdir(parents=True)
        (sessions / "dev-senior").write_text(f"ROLE=dev-senior\nCWD={worktree}\nSESSION_ID=example\n")
        preview = self.teamflow("workers", "migrate", "--dry-run")
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertIn("dev-senior -> developer-l-1", preview.stdout)
        result = self.teamflow("workers", "migrate")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / ".ai-team-worktrees" / "developer-l-1").exists())
        self.assertIn("ROLE=developer-l-1", (sessions / "developer-l-1").read_text())
        self.assertIn("developer-l-1", self.teamflow("workers", "list").stdout)
        migrated = (self.root / "teamflow.conf").read_text()
        self.assertEqual(migrated.count("role_file = .agents/roles/developer.md"), 3)
        self.assertTrue((self.root / "teamflow.conf.pre-workers.bak").exists())

    @unittest.skipUnless(shutil.which("tmux"), "tmux unavailable")
    def test_fifteen_instances_span_windows(self):
        roles = self.root / ".agents" / "roles"
        config = "[workspace]\nsession_prefix = smoke\n[type.researcher]\ncli = claude\nmodel = stub\nrole_file = .agents/roles/researcher.md\n"
        config += "".join(f"[worker.researcher-{i}]\ntype = researcher\n" for i in range(1, 16))
        (self.root / "teamflow.conf").write_text(config)
        stubs = self.root / "stubs"
        stubs.mkdir()
        stub = stubs / "claude"
        stub.write_text("#!/bin/sh\nsleep 30\n")
        stub.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = str(stubs) + os.pathsep + env["PATH"]
        try:
            launched = self.teamflow("up", "--workers", "--no-attach", env=env)
            self.assertEqual(launched.returncode, 0, launched.stderr)
            verified = self.teamflow("--verify", env=env)
            self.assertEqual(verified.stdout.count("pane %"), 15, verified.stdout)
            windows = subprocess.check_output(["tmux", "list-windows", "-t",
                                               "=" + launched.stdout.strip(), "-F", "#{window_name}"], text=True)
            self.assertGreaterEqual(len(windows.splitlines()), 2)
            positions = subprocess.check_output(["tmux", "list-panes", "-t",
                                                 "=" + launched.stdout.strip() + ":team", "-F",
                                                 "#{pane_left} #{pane_top}"], text=True)
            coordinates = [tuple(map(int, line.split())) for line in positions.splitlines()]
            self.assertGreater(len({x for x, _ in coordinates}), 1)
            self.assertGreater(len({y for _, y in coordinates}), 1)
        finally:
            self.teamflow("--kill", env=env)


if __name__ == "__main__":
    unittest.main()
