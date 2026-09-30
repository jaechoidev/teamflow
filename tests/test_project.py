#!/usr/bin/env python3
"""Project setup checks: refresh from the tool home, linked worktrees, and
migration from the ai-team names. Stub CLIs and a private tmux server."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]
SHIPPED = ("scripts", ".agents/lib", ".agents/roles", ".agents/doc-templates", "skills")
GIT = {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
       "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com"}
CONFIG = """[workspace]
session_prefix = proj
[type.researcher]
cli = claude
model = stub
role_file = .agents/roles/researcher.md
next_id = 2
[type.developer-l]
cli = claude
model = stub
worktree = yes
role_file = .agents/roles/developer.md
next_id = 2
[worker.researcher-1]
type = researcher
[worker.developer-l-1]
type = developer-l
"""


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, text=True,
                          capture_output=True, env={**os.environ, **GIT}).stdout


class ProjectTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        stubs = self.base / "stubs"
        stubs.mkdir()
        self.log = self.base / "stub.log"
        (stubs / "claude").write_text(
            '#!/bin/sh\necho "$AGENT_ROLE $*" >> "$STUB_LOG"\nexec sleep 60\n')
        socket = f"teamflow-test-{os.getpid()}"
        (stubs / "tmux").write_text(f'#!/bin/sh\nexec {shutil.which("tmux")} -L {socket} "$@"\n')
        for stub in stubs.iterdir():
            stub.chmod(0o755)
        self.claude_home = self.base / "claude-home"
        self.env = {**os.environ, **GIT, "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
                    "STUB_LOG": str(self.log), "CLAUDE_CONFIG_DIR": str(self.claude_home)}
        self.env.pop("TMUX", None)
        self.addCleanup(subprocess.run, [str(stubs / "tmux"), "kill-server"], capture_output=True)

    def make_home(self):
        home = self.base / "home"
        for rel in SHIPPED:
            shutil.copytree(SOURCE / rel, home / rel, ignore=shutil.ignore_patterns("__pycache__", "*.new"))
        shutil.copy2(SOURCE / ".agents/AGENTS-SECTION.md", home / ".agents/AGENTS-SECTION.md")
        (home / "teamflow.conf").write_text(CONFIG)
        git("init", "-q", "-b", "main", str(home))
        git("-C", str(home), "add", "-A")
        git("-C", str(home), "commit", "-qm", "ship")
        return home

    def run_tf(self, launcher, *args, cwd):
        return subprocess.run(["bash", str(launcher), *args], cwd=cwd, env=self.env,
                              text=True, capture_output=True)

    def test_project_copy_follows_tool_home(self):
        home = self.make_home()
        project = self.base / "project"
        project.mkdir()
        (project / "AGENTS.md").write_text("Own notes.\n\n# >>> ai-team >>>\nold\n# <<< ai-team <<<\n")
        init = self.run_tf(home / "scripts/teamflow", "init", cwd=project)
        self.assertEqual(init.returncode, 0, init.stderr)
        agents = (project / "AGENTS.md").read_text()
        self.assertIn("Own notes.", agents)
        self.assertNotIn("ai-team >>>", agents)
        self.assertEqual(agents.count("# >>> teamflow >>>"), 1)
        self.assertIn(".agents/teamflow-manifest", git("-C", str(project), "show", "--name-only", "HEAD"))

        # A committed tool update replaces unmodified copies. Local edits stay.
        (project / ".agents/roles/developer.md").write_text("local role\n")
        for rel in (".agents/lib/task.sh", ".agents/roles/developer.md"):
            with open(home / rel, "a") as stream:
                stream.write("# shipped update 1\n")
        git("-C", str(home), "commit", "-qam", "update")
        init = self.run_tf(home / "scripts/teamflow", "init", cwd=project)
        self.assertEqual(init.returncode, 0, init.stderr)
        self.assertEqual((project / ".agents/lib/task.sh").read_text(), (home / ".agents/lib/task.sh").read_text())
        self.assertEqual((project / ".agents/roles/developer.md").read_text(), "local role\n")
        self.assertEqual((project / ".agents/roles/developer.md.new").read_text(),
                         (home / ".agents/roles/developer.md").read_text())

        # An uncommitted tool change followed by another one: the manifest
        # still recognizes the first as shipped.
        for mark in ("2", "3"):
            with open(home / ".agents/lib/task.sh", "a") as stream:
                stream.write(f"# shipped update {mark}\n")
            init = self.run_tf(home / "scripts/teamflow", "init", cwd=project)
            self.assertEqual(init.returncode, 0, init.stderr)
            self.assertEqual((project / ".agents/lib/task.sh").read_text(),
                             (home / ".agents/lib/task.sh").read_text())

        # The project's own launcher hands off to the tool home, which
        # refreshes the project first.
        for rel in ("scripts/teamflow", ".agents/lib/pane.sh"):
            with open(home / rel, "a") as stream:
                stream.write("# shipped update 4\n")
        listed = self.run_tf(project / "scripts/teamflow", "workers", "list", cwd=project)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn("researcher-1", listed.stdout)
        for rel in ("scripts/teamflow", ".agents/lib/pane.sh"):
            self.assertEqual((project / rel).read_text(), (home / rel).read_text(), rel)

        # A locally edited project launcher runs as is.
        with open(project / "scripts/teamflow", "a") as stream:
            stream.write("# local launcher edit\n")
        with open(home / "scripts/teamflow", "a") as stream:
            stream.write("# shipped update 5\n")
        listed = self.run_tf(project / "scripts/teamflow", "workers", "list", cwd=project)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn("has local edits", listed.stderr)
        self.assertTrue((project / "scripts/teamflow").read_text().endswith("# local launcher edit\n"))

    @unittest.skipUnless(shutil.which("tmux"), "tmux unavailable")
    def test_linked_worktree_acts_on_main_checkout(self):
        home = self.make_home()
        project = self.base / "project"
        project.mkdir()
        self.assertEqual(self.run_tf(home / "scripts/teamflow", "init", cwd=project).returncode, 0)
        started = self.run_tf(home / "scripts/teamflow", "start", cwd=project)
        self.assertEqual(started.returncode, 0, started.stderr)
        session = started.stdout.strip()
        agent = self.base / "agent-worktree"
        git("-C", str(project), "worktree", "add", "-q", "-b", "agent", str(agent))

        added = self.run_tf(agent / "scripts/teamflow", "workers", "add", "developer-l", cwd=agent)
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertIn("developer-l-2", added.stdout)
        worktrees = git("-C", str(project), "worktree", "list")
        self.assertIn(str(project / ".teamflow-worktrees/developer-l-2"), worktrees)
        self.assertFalse((agent / ".teamflow-worktrees").exists())
        self.assertIn("[worker.developer-l-2]", (project / ".git/teamflow/running.conf").read_text())
        self.assertNotIn("[worker.developer-l-2]", (project / "teamflow.conf").read_text())

        again = self.run_tf(agent / "scripts/teamflow", "start", cwd=agent)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(again.stdout.strip(), session)
        self.assertEqual(self.run_tf(project / "scripts/teamflow", "--kill", cwd=project).returncode, 0)
        restarted = self.run_tf(project / "scripts/teamflow", "start", cwd=project)
        self.assertEqual(restarted.returncode, 0, restarted.stderr)
        self.run_tf(project / "scripts/teamflow", "--kill", cwd=project)

    @unittest.skipUnless(shutil.which("tmux"), "tmux unavailable")
    def test_migrates_ai_team_state(self):
        home = self.make_home()
        project = self.base / "project"
        project.mkdir()
        self.assertEqual(self.run_tf(home / "scripts/teamflow", "init", cwd=project).returncode, 0)
        common = project / ".git"
        legacy = common / "ai-team"
        (legacy / "tasks/T-0001").mkdir(parents=True)
        (legacy / "tasks/T-0001/task.md").write_text("to:      developer-l-1\n")
        (legacy / "tasks/T-0001/status").write_text("done\n")
        git("-C", str(project), "worktree", "add", "-q", "-b", "ai-team/developer-l-1",
            str(project / ".ai-team-worktrees/developer-l-1"))
        with open(common / "info/exclude", "a") as stream:
            stream.write(".ai-team-worktrees/\n")
        # Conversations recorded before the move: the worktree one moves
        # with its directory, the main checkout one stays put.
        (legacy / "sessions").mkdir()
        transcripts = self.claude_home / "projects/old"
        transcripts.mkdir(parents=True)
        for role, cwd, sid in (("researcher-1", project, "11111111-1111-4111-8111-111111111111"),
                               ("developer-l-1", project / ".ai-team-worktrees/developer-l-1",
                                "22222222-2222-4222-8222-222222222222")):
            (legacy / "sessions" / role).write_text(f"ROLE={role}\nCLI=claude\nSESSION_ID={sid}\nCWD={cwd}\n")
            (transcripts / f"{sid}.jsonl").write_text("{}\n")

        # A team still running from the old state blocks the move.
        tmux = self.base / "stubs/tmux"
        subprocess.run([str(tmux), "new-session", "-d", "-s", "legacy", "sleep 60"], check=True, env=self.env)
        created = subprocess.run([str(tmux), "list-sessions", "-F", "#{session_created}"],
                                 check=True, capture_output=True, text=True, env=self.env).stdout.strip()
        (legacy / "active").write_text(f"SESSION=legacy\nCREATED={created}\n")
        refused = self.run_tf(project / "scripts/teamflow", "start", cwd=project)
        self.assertNotEqual(refused.returncode, 0, refused.stderr)
        self.assertIn("old .git/ai-team state", refused.stderr)
        self.assertTrue((legacy / "tasks/T-0001").is_dir())
        subprocess.run([str(tmux), "kill-session", "-t", "=legacy"], check=True, env=self.env)

        started = self.run_tf(project / "scripts/teamflow", "start", cwd=project)
        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertFalse(legacy.exists())
        self.assertTrue((common / "teamflow/tasks/T-0001/task.md").is_file())
        self.assertFalse((project / ".ai-team-worktrees").exists())
        worktrees = git("-C", str(project), "worktree", "list")
        self.assertIn(f"{project / '.teamflow-worktrees/developer-l-1'} ", worktrees)
        self.assertIn("[teamflow/developer-l-1]", worktrees)
        self.assertEqual(git("-C", str(project), "branch", "--list", "ai-team/*"), "")
        self.assertNotIn(".ai-team-worktrees/", (common / "info/exclude").read_text())
        launched = self.log.read_text()
        self.assertIn("researcher-1 --resume 11111111-1111-4111-8111-111111111111", launched)
        self.assertNotIn("22222222-2222-4222-8222-222222222222", launched)
        self.assertIn("developer-l-1 --session-id", launched)
        self.run_tf(project / "scripts/teamflow", "--kill", cwd=project)


if __name__ == "__main__":
    unittest.main()
