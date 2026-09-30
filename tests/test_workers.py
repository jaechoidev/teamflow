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
        for name in ("teamflow", "teamflow_workers.py", "teamflow_scaffold.py"):
            shutil.copy2(SOURCE / "scripts" / name, self.root / "scripts" / name)
        shutil.copy2(SOURCE / "teamflow.conf", self.root / "teamflow.conf")
        roles = self.root / ".agents" / "roles"
        roles.mkdir(parents=True)
        for source in (SOURCE / ".agents" / "roles").glob("*.md"):
            shutil.copy2(source, roles / source.name)
        libs = self.root / ".agents/lib"
        libs.mkdir()
        for source in (SOURCE / ".agents/lib").glob("*.sh"):
            shutil.copy2(source, libs / source.name)
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
        task = self.root / ".git" / "teamflow" / "tasks" / "T-0001"
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

    def test_old_pane_sections_are_rejected(self):
        (self.root / "teamflow.conf").write_text(
            "[workspace]\n[pane.researcher]\ncli = claude\nmodel = stub\n"
        )
        result = self.teamflow("workers", "list")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported pane section: pane.researcher", result.stderr)

    @unittest.skipUnless(shutil.which("tmux"), "tmux unavailable")
    def test_live_roster_changes(self):
        config = "[workspace]\nsession_prefix = live\n[type.researcher]\ncli = claude\nmodel = stub\nrole_file = .agents/roles/researcher.md\nnext_id = 3\n"
        config += "[type.notetaker]\ncli = claude\nmodel = stub\nrole_file = .agents/roles/notetaker.md\n"
        config += "[worker.researcher-1]\ntype = researcher\n[worker.researcher-2]\ntype = researcher\n[worker.notetaker]\ntype = notetaker\n"
        (self.root / "teamflow.conf").write_text(config)
        stubs = self.root / "stubs"
        stubs.mkdir()
        stub = stubs / "claude"
        stub.write_text("#!/bin/sh\nsleep 60\n")
        stub.chmod(0o755)
        env = {**os.environ, "PATH": str(stubs) + os.pathsep + os.environ["PATH"]}
        registry = self.root / ".git/teamflow/panes.tsv"

        def panes():
            return dict(line.split("\t") for line in registry.read_text().splitlines())

        try:
            start = self.teamflow("up", "--workers", "--no-attach", env=env)
            self.assertEqual(start.returncode, 0, start.stderr)
            original = panes()
            added = self.teamflow("workers", "add", "researcher", env=env)
            self.assertEqual(added.returncode, 0, added.stderr)
            live = panes()
            self.assertEqual(live["researcher-1"], original["researcher-1"])
            self.assertEqual(live["researcher-2"], original["researcher-2"])
            self.assertIn("researcher-3", live)
            order = subprocess.check_output(["tmux", "list-panes", "-s", "-t", "=" + start.stdout.strip(), "-F", "#{pane_id}"], text=True).splitlines()
            self.assertEqual(order[-1], original["notetaker"])
            task = self.root / ".git/teamflow/tasks/T-0001"
            task.mkdir(parents=True)
            (task / "task.md").write_text("to:      researcher-2\n")
            (task / "status").write_text("in-progress\n")
            refused = self.teamflow("workers", "remove", "researcher-2", env=env)
            self.assertNotEqual(refused.returncode, 0)
            self.assertEqual(panes(), live)
            (task / "status").write_text("done\n")
            removed = self.teamflow("workers", "remove", "researcher-2", env=env)
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertNotIn("researcher-2", panes())
            actual = subprocess.check_output(["tmux", "list-panes", "-s", "-t", "=" + start.stdout.strip(), "-F", "#{pane_id}"], text=True)
            self.assertNotIn(original["researcher-2"], actual.splitlines())
            before = (self.root / "teamflow.conf").read_bytes()
            stub.write_text("#!/bin/sh\nexit 1\n")
            failed = self.teamflow("workers", "add", "researcher", env=env)
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(before, (self.root / "teamflow.conf").read_bytes())
            self.assertEqual(panes(), {"researcher-1": original["researcher-1"], "researcher-3": live["researcher-3"], "notetaker": original["notetaker"]})
            stub.write_text("#!/bin/sh\nsleep 60\n")
            changed = (self.root / "teamflow.conf").read_text().replace("[worker.researcher-3]\ntype = researcher\n", "")
            changed = changed.replace("[worker.notetaker]", "[worker.researcher-5]\ntype = researcher\n[worker.notetaker]")
            (self.root / "teamflow.conf").write_text(changed)
            synced = self.teamflow("workers", "sync", env=env)
            self.assertEqual(synced.returncode, 0, synced.stderr)
            self.assertEqual(set(panes()), {"researcher-1", "researcher-5", "notetaker"})
            self.assertEqual(panes()["researcher-1"], original["researcher-1"])
            good = (self.root / "teamflow.conf").read_text()
            (self.root / "teamflow.conf").write_text(good.replace("model = stub", "model = other", 1))
            refused = self.teamflow("workers", "sync", env=env)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("settings changed", refused.stderr)
            (self.root / "teamflow.conf").write_text(good)
            resumed = self.teamflow("up", "--workers", "--no-attach", env=env)
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
        finally:
            self.teamflow("--kill", env=env)

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
            registry = self.root / ".git/teamflow/panes.tsv"
            original = dict(line.split("\t") for line in registry.read_text().splitlines())
            added = self.teamflow("workers", "add", "researcher", env=env)
            self.assertEqual(added.returncode, 0, added.stderr)
            current = dict(line.split("\t") for line in registry.read_text().splitlines())
            self.assertEqual({worker: current[worker] for worker in original}, original)
            self.assertIn("researcher-16", current)
            removed = self.teamflow("workers", "remove", "researcher-16", env=env)
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertEqual(dict(line.split("\t") for line in registry.read_text().splitlines()), original)
        finally:
            self.teamflow("--kill", env=env)


if __name__ == "__main__":
    unittest.main()
