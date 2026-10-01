#!/usr/bin/env python3
"""Project setup checks: install, thin projects, linked worktrees, and
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
                    "STUB_LOG": str(self.log), "CLAUDE_CONFIG_DIR": str(self.claude_home),
                    "XDG_DATA_HOME": str(self.base / "data"), "XDG_CONFIG_HOME": str(self.base / "config")}
        self.env.pop("TMUX", None)
        self.addCleanup(subprocess.run, [str(stubs / "tmux"), "kill-server"], capture_output=True)

    def make_home(self):
        home = self.base / "home"
        for rel in SHIPPED:
            shutil.copytree(SOURCE / rel, home / rel, ignore=shutil.ignore_patterns("__pycache__", "*.new"))
        shutil.copy2(SOURCE / ".agents/AGENTS-SECTION.md", home / ".agents/AGENTS-SECTION.md")
        (home / "teamflow.conf").write_text(CONFIG)
        (home / ".agents/teamflow.conf").write_text(CONFIG)  # the shipped catalog init starts from
        git("init", "-q", "-b", "main", str(home))
        git("-C", str(home), "add", "-A")
        git("-C", str(home), "commit", "-qm", "ship")
        return home

    def run_tf(self, launcher, *args, cwd):
        return subprocess.run(["bash", str(launcher), *args], cwd=cwd, env=self.env,
                              text=True, capture_output=True)

    def test_install_and_thin_projects(self):
        home = self.make_home()
        git("-C", str(home), "tag", "-a", "v0.1.0", "-m", "v0.1.0")
        data, bin_dir = self.base / "data", self.base / "bin"
        self.env["XDG_DATA_HOME"] = str(self.base)
        installed = self.run_tf(home / "scripts/teamflow", "install", "--bin-dir", str(bin_dir), cwd=self.base)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        tool = bin_dir / "teamflow"
        self.assertEqual((self.base / "teamflow/current").resolve(), (self.base / "teamflow/v0.1.0").resolve())
        self.assertEqual((self.base / "teamflow/v0.1.0/version").read_text(), "v0.1.0\n")

        # A new project holds only its config, the stub skills, and AGENTS.md.
        project = self.base / "project"
        project.mkdir()
        (project / "AGENTS.md").write_text("Own notes.\n\n# >>> ai-team >>>\nold\n# <<< ai-team <<<\n")
        init = self.run_tf(tool, "init", cwd=project)
        self.assertEqual(init.returncode, 0, init.stderr)
        committed = git("-C", str(project), "show", "--name-only", "--format=", "HEAD").split()
        stubs = [f"{scope}/skills/{skill}/SKILL.md" for scope in (".agents", ".claude")
                 for skill in ("teamflow", "teamflow-kill", "teamflow-resume", "teamflow-trim", "teamflow-workers")]
        self.assertEqual(sorted(committed), sorted(stubs + ["AGENTS.md", "teamflow.conf"]))
        self.assertIn("teamflow = 0.1.0", (project / "teamflow.conf").read_text())
        agents = (project / "AGENTS.md").read_text()
        self.assertIn("Own notes.", agents)
        self.assertNotIn("ai-team >>>", agents)
        self.assertIn("teamflow guide teamflow", (project / ".claude/skills/teamflow/SKILL.md").read_text())
        guide = self.run_tf(tool, "guide", "teamflow", cwd=project)
        self.assertEqual(guide.returncode, 0, guide.stderr)
        self.assertTrue(guide.stdout.startswith("\n# teamflow Delegator") or "# teamflow Delegator" in guide.stdout)
        self.assertNotIn("description:", guide.stdout)
        shown = self.run_tf(tool, "role", "show", "developer", cwd=project)
        self.assertEqual(shown.stdout, (home / ".agents/roles/developer.md").read_text())

        # A project from an older version loses its unmodified copies. An
        # edited role stays as an override.
        vendored = self.base / "vendored"
        for rel in ("scripts", ".agents/lib", ".agents/roles", ".agents/doc-templates"):
            shutil.copytree(home / rel, vendored / rel)
        shutil.copy2(home / ".agents/AGENTS-SECTION.md", vendored / ".agents/AGENTS-SECTION.md")
        for scope in (".agents", ".claude"):
            shutil.copytree(home / "skills", vendored / scope / "skills")
        (vendored / "teamflow.conf").write_text(CONFIG)
        with open(vendored / ".agents/roles/developer.md", "a") as stream:
            stream.write("Project rule: run the linters.\n")
        git("init", "-q", "-b", "main", str(vendored))
        git("-C", str(vendored), "add", "-A")
        git("-C", str(vendored), "commit", "-qm", "vendored")
        init = self.run_tf(tool, "init", cwd=vendored)
        self.assertEqual(init.returncode, 0, init.stderr)
        self.assertFalse((vendored / "scripts").exists())
        self.assertFalse((vendored / ".agents/lib").exists())
        self.assertFalse((vendored / ".agents/doc-templates").exists())
        self.assertEqual(sorted(p.name for p in (vendored / ".agents/roles").iterdir()), ["developer.md"])
        self.assertIn("overrides the installed default", init.stderr)
        self.assertIn("teamflow guide teamflow-kill", (vendored / ".agents/skills/teamflow-kill/SKILL.md").read_text())
        shown = self.run_tf(tool, "role", "show", "developer", cwd=vendored)
        self.assertIn("Project rule: run the linters.", shown.stdout)

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

        added = self.run_tf(home / "scripts/teamflow", "workers", "add", "developer-l", cwd=agent)
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertIn("developer-l-2", added.stdout)
        worktrees = git("-C", str(project), "worktree", "list")
        self.assertIn(str(project / ".teamflow-worktrees/developer-l-2"), worktrees)
        self.assertFalse((agent / ".teamflow-worktrees").exists())
        self.assertIn("[worker.developer-l-2]", (project / ".git/teamflow/running.conf").read_text())
        self.assertNotIn("[worker.developer-l-2]", (project / "teamflow.conf").read_text())

        again = self.run_tf(home / "scripts/teamflow", "start", cwd=agent)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(again.stdout.strip(), session)
        self.assertEqual(self.run_tf(home / "scripts/teamflow", "--kill", cwd=project).returncode, 0)
        restarted = self.run_tf(home / "scripts/teamflow", "start", cwd=project)
        self.assertEqual(restarted.returncode, 0, restarted.stderr)
        self.run_tf(home / "scripts/teamflow", "--kill", cwd=project)

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
        refused = self.run_tf(home / "scripts/teamflow", "start", cwd=project)
        self.assertNotEqual(refused.returncode, 0, refused.stderr)
        self.assertIn("old .git/ai-team state", refused.stderr)
        self.assertTrue((legacy / "tasks/T-0001").is_dir())
        subprocess.run([str(tmux), "kill-session", "-t", "=legacy"], check=True, env=self.env)

        started = self.run_tf(home / "scripts/teamflow", "start", cwd=project)
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
        self.run_tf(home / "scripts/teamflow", "--kill", cwd=project)


if __name__ == "__main__":
    unittest.main()
