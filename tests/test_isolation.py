#!/usr/bin/env python3
"""Each project's team on its own tmux server: socket names, the stripped
session variables, pane checks before typing, doctor, and teams that an
older teamflow started on the shared default server.

Every tmux server here, the default one included, lives in a private
TMUX_TMPDIR, and cleanup stops only those servers."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

SOURCE = Path(__file__).resolve().parents[1]
LIB = SOURCE / ".agents/lib/tmux-server.sh"
STRIPPED = {"CLAUDECODE": "1", "CLAUDE_CODE_CHILD_SESSION": "1", "CLAUDE_CODE_SESSION_ID": "leaked-session",
            "CLAUDE_CODE_MESSAGING_TOKEN": "s3cr3t-token-value", "CLAUDE_CODE_ENTRYPOINT": "cli",
            "CLAUDE_PID": "24847", "CLAUDE_JOB_DIR": "/tmp/jobs/x", "CLAUDE_EFFORT": "xhigh",
            "AI_AGENT": "claude-code_test"}
KEPT = {"CLAUDE_CONFIG_DIR": None, "CLAUDE_IOS_CONFIG": "/tmp/ios-config", "ANTHROPIC_EXTRA_HEADER": "kept",
        "AGENT_TEST_SETTING": "kept"}
CONFIG = """[workspace]
session_prefix = tf
teamflow = 0.2.0
[type.researcher]
cli = claude
model = stub
role_file = .agents/roles/researcher.md
next_id = 3
[worker.researcher-1]
type = researcher
[worker.researcher-2]
type = researcher
"""
# A worker CLI stand-in: it answers --help like Claude Code (with or without
# --name), records its arguments and the names of its environment, and
# appends each typed line to <role>.input.
STUB = """#!/bin/sh
if [ "$1" = --help ]; then
  [ -n "$STUB_NO_NAME" ] || echo "  -n, --name <name>  Set a display name for this session"
  exit 0
fi
echo "$AGENT_ROLE $*" >> "$STUB_DIR/launch.log"
env | cut -d= -f1 | sort > "$STUB_DIR/$AGENT_ROLE.env"
# Not a shell: macOS hides the environment of system binaries from ps.
exec python3 "$STUB_DIR/reader.py"
"""
READER = """import os, sys
path = os.path.join(os.environ["STUB_DIR"], os.environ.get("AGENT_ROLE", "") + ".input")
while True:
    line = sys.stdin.readline()
    if not line:
        break
    with open(path, "a") as stream:
        stream.write(line)
"""


def ps_shows_environment():
    """doctor reads a worker's environment with ps eww (or /proc)."""
    child = subprocess.Popen(["python3", "-c", "import time; time.sleep(5)"],
                             env={**os.environ, "TEAMFLOW_PROBE": "1"})
    try:
        time.sleep(0.3)
        if Path(f"/proc/{child.pid}/environ").exists():
            return True
        shown = subprocess.run(["ps", "eww", "-o", "command=", "-p", str(child.pid)],
                               capture_output=True, text=True).stdout
        return "TEAMFLOW_PROBE=1" in shown
    finally:
        child.kill()
        child.wait()


PS_ENV = ps_shows_environment()


def bash_lib(snippet, *args):
    return subprocess.run(["bash", "-c", f'. "{LIB}"; {snippet}', "_", *args],
                          capture_output=True, text=True, check=True).stdout


class Project:
    """A throwaway repository that runs this checkout's launcher."""

    def __init__(self, test, name):
        self.test = test
        base = Path(tempfile.mkdtemp())
        test.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.root = base / name
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.root)], check=True)
        for rel in ("scripts", ".agents/lib", ".agents/roles"):
            shutil.copytree(SOURCE / rel, self.root / rel, ignore=shutil.ignore_patterns("__pycache__"))
        (self.root / "teamflow.conf").write_text(CONFIG)
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                        "commit", "-qm", "seed"], check=True)
        self.root = Path(subprocess.check_output(["git", "-C", str(self.root), "rev-parse", "--show-toplevel"],
                                                 text=True).strip())
        self.stubs = base / "stubs"
        self.stubs.mkdir()
        (self.stubs / "claude").write_text(STUB)
        (self.stubs / "claude").chmod(0o755)
        (self.stubs / "reader.py").write_text(READER)
        self.claude_home = base / "claude-home"
        self.mailbox = self.root / ".git/teamflow"

    @property
    def session(self):
        digest = hashlib.md5(str(self.root).encode()).hexdigest()[:8]
        return f"tf-{self.root.name}-{digest}"

    def env(self, **extra):
        env = {**os.environ, **STRIPPED, **{k: v for k, v in KEPT.items() if v}}
        env.update({"PATH": f"{self.stubs}{os.pathsep}{os.environ['PATH']}", "STUB_DIR": str(self.stubs),
                    "CLAUDE_CONFIG_DIR": str(self.claude_home), "TMUX_TMPDIR": str(self.test.tmux_dir),
                    "XDG_DATA_HOME": str(self.root.parent / "data"),
                    "XDG_CONFIG_HOME": str(self.root.parent / "config")})
        env.pop("TMUX", None)
        env.update(extra)
        return env

    def teamflow(self, *args, **extra):
        return subprocess.run(["bash", "scripts/teamflow", *args], cwd=self.root, env=self.env(**extra),
                              text=True, capture_output=True)

    def pane(self, *args):
        env = self.env(AGENT_MAILBOX=str(self.mailbox))
        return subprocess.run(["bash", str(self.root / ".agents/lib/pane.sh"), *args], env=env,
                              text=True, capture_output=True)

    def record(self):
        path = self.mailbox / "active"
        if not path.exists():
            return {}
        return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)

    def tmux(self, socket, *args, check=True):
        result = subprocess.run(["tmux", "-L", socket, *args], env=self.env(), text=True, capture_output=True)
        if check and result.returncode:
            raise AssertionError(result.stderr)
        return result

    def input_of(self, role):
        path = self.stubs / f"{role}.input"
        return path.read_text() if path.exists() else ""

    def wait_for(self, path, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            if path.exists() and path.stat().st_size:
                return
            time.sleep(0.05)
        raise AssertionError(f"{path} never appeared")


@unittest.skipUnless(shutil.which("tmux"), "tmux unavailable")
class IsolationTest(unittest.TestCase):
    def setUp(self):
        # Short: a socket path has a length limit.
        self.tmux_dir = Path(tempfile.mkdtemp(prefix="tft-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.tmux_dir, ignore_errors=True)
        self.addCleanup(self.kill_servers)

    def kill_servers(self):
        for socket in self.tmux_dir.glob("tmux-*/*"):
            subprocess.run(["tmux", "-S", str(socket), "kill-server"], capture_output=True)

    def start(self, project, *args, **extra):
        started = project.teamflow("start", *args, **extra)
        self.assertEqual(started.returncode, 0, started.stderr)
        for role in ("researcher-1", "researcher-2"):
            if f"[worker.{role}]" in (project.mailbox / "running.conf").read_text():
                project.wait_for(project.stubs / f"{role}.env")
        return started

    # 1. one server per project ----------------------------------------------

    def test_socket_names(self):
        self.assertEqual(bash_lib('tf_socket_name "$1"', "tf-blankTracker-e187108e"), "tf-blankTracker-e187108e")
        self.assertEqual(bash_lib('tf_socket_name "$1"', "tf-my project!-e187108e"), "tf-my-project--e187108e")
        long = "tf-" + "x" * 80 + "-e187108e"
        named = bash_lib('tf_socket_name "$1"', long)
        self.assertLessEqual(len(named), 64)
        self.assertTrue(named.endswith("-e187108e"))
        self.assertEqual(named, bash_lib('tf_socket_name "$1"', long))

    def test_team_runs_on_its_own_server(self):
        one, two = Project(self, "alpha"), Project(self, "beta")
        started = self.start(one)
        self.start(two)
        record = one.record()
        self.assertEqual(record["SESSION"], one.session)
        self.assertEqual(record["SOCKET"], one.session)
        self.assertNotEqual(record["SOCKET"], two.record()["SOCKET"])
        self.assertIn(f"tmux -L {one.session} attach -t {one.session}", started.stderr)
        # Each server holds one project's session. The default server holds none.
        listed = one.tmux(record["SOCKET"], "list-sessions", "-F", "#{session_name}").stdout.split()
        self.assertEqual(listed, [one.session])
        self.assertNotEqual(one.tmux("default", "has-session", check=False).returncode, 0)
        roster = one.teamflow("list")
        self.assertEqual(roster.returncode, 0, roster.stderr)
        self.assertIn(f"tmux -L {one.session} attach -t {one.session}:researcher-1", roster.stdout)
        self.assertIn("switch-client", roster.stdout)
        verified = one.teamflow("--verify")
        self.assertIn(f"server:  tmux -L {one.session}", verified.stdout)
        self.assertEqual(verified.stdout.count("pane %"), 2, verified.stdout)
        # A worker added later and a reattach use the same server.
        added = one.teamflow("add", "researcher")
        self.assertEqual(added.returncode, 0, added.stderr)
        windows = one.tmux(record["SOCKET"], "list-windows", "-t", f"={one.session}:", "-F", "#{window_name}")
        self.assertEqual(windows.stdout.split(), ["researcher-1", "researcher-2", "researcher-3"])
        again = one.teamflow("start")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("already running", again.stderr)
        killed = one.teamflow("--kill")
        self.assertIn(f"killed {one.session}", killed.stderr)
        self.assertNotEqual(one.tmux(record["SOCKET"], "has-session", "-t", f"={one.session}",
                                     check=False).returncode, 0)
        self.assertEqual(two.tmux(two.record()["SOCKET"], "has-session", "-t", f"={two.session}",
                                  check=False).returncode, 0)

    # 2. clean worker environment ---------------------------------------------

    def test_session_variable_list(self):
        for name in STRIPPED:
            self.assertEqual(subprocess.run(["bash", "-c", f'. "{LIB}"; tf_session_var "$1"', "_", name]).returncode,
                             0, name)
        for name in ("PATH", "HOME", "CLAUDE_CONFIG_DIR", "CLAUDE_IOS_CONFIG", "ANTHROPIC_BASE_URL",
                     "ANTHROPIC_API_KEY", "AGENT_ROLE", "AGENT_MAILBOX", "CLAUDECODEX", "CLAUDE_CODEX", "PWD"):
            self.assertNotEqual(subprocess.run(["bash", "-c", f'. "{LIB}"; tf_session_var "$1"', "_", name]).returncode,
                                0, name)
        found = bash_lib("printf 'CLAUDECODE=1\\nPATH=/bin\\nCLAUDE_CODE_X=a=b\\n-CLAUDE_PID\\nAI_AGENT=x\\n' | tf_session_vars")
        self.assertEqual(found.split(), ["CLAUDECODE", "CLAUDE_CODE_X", "AI_AGENT"])

    def assert_clean_worker(self, project, role):
        names = set((project.stubs / f"{role}.env").read_text().split())
        self.assertFalse(names & set(STRIPPED), names & set(STRIPPED))
        self.assertLessEqual({"PATH", "HOME", "CLAUDE_CONFIG_DIR", "CLAUDE_IOS_CONFIG", "ANTHROPIC_EXTRA_HEADER",
                              "AGENT_TEST_SETTING", "AGENT_ROLE", "AGENT_MAILBOX"}, names)

    def test_workers_start_without_session_variables(self):
        project = Project(self, "gamma")
        self.start(project, ANTHROPIC_BASE_URL="https://example.invalid")
        for role in ("researcher-1", "researcher-2"):
            self.assert_clean_worker(project, role)
        sock = project.record()["SOCKET"]
        server = project.tmux(sock, "show-environment", "-g").stdout
        names = {line.split("=", 1)[0] for line in server.splitlines() if "=" in line}
        self.assertFalse(names & set(STRIPPED), names & set(STRIPPED))
        self.assertFalse(names & {"PWD", "OLDPWD"})
        # The user's own settings stay in the server's environment.
        self.assertLessEqual({"ANTHROPIC_BASE_URL", "CLAUDE_IOS_CONFIG", "CLAUDE_CONFIG_DIR", "PATH", "HOME"}, names)
        added = project.teamflow("add", "researcher")
        self.assertEqual(added.returncode, 0, added.stderr)
        project.wait_for(project.stubs / "researcher-3.env")
        self.assert_clean_worker(project, "researcher-3")
        launched = (project.stubs / "launch.log").read_text()
        self.assertIn(f"--name {project.root.name}/researcher-1", launched)
        self.assertIn(f"--name {project.root.name}/researcher-3", launched)
        doctor = project.teamflow("doctor")
        self.assertIn(f"ok       team {project.session} runs on its own tmux server", doctor.stdout)
        if PS_ENV:
            self.assertIn("ok       3 running worker(s) started without Claude Code session variables", doctor.stdout)
        self.assertNotIn("PROBLEM", doctor.stdout)

    def test_a_polluted_server_still_starts_clean_workers(self):
        # A server already running on the team's socket, with the Delegator's
        # variables in its global environment, is cleaned before workers start.
        project = Project(self, "delta")
        subprocess.run(["tmux", "-L", project.session, "new-session", "-d", "-s", "placeholder", "sleep 60"],
                       env=project.env(), check=True)
        self.assertIn("CLAUDE_CODE_SESSION_ID=", project.tmux(project.session, "show-environment", "-g").stdout)
        self.start(project)
        for role in ("researcher-1", "researcher-2"):
            self.assert_clean_worker(project, role)
        server = project.tmux(project.session, "show-environment", "-g").stdout
        self.assertNotIn("CLAUDE_CODE_SESSION_ID=", server)

    def test_workers_start_under_a_login_shell_that_is_not_posix(self):
        # Worker commands are POSIX sh. A login shell such as fish cannot
        # parse them, so tmux must start them with /bin/sh, never $SHELL.
        project = Project(self, "zeta")
        fish = project.stubs / "fish"
        fish.write_text(f'#!/bin/sh\necho "$*" >> {project.stubs}/fish.log\nexit 127\n')
        fish.chmod(0o755)
        self.start(project, SHELL=str(fish))
        added = project.teamflow("add", "researcher", SHELL=str(fish))
        self.assertEqual(added.returncode, 0, added.stderr)
        project.wait_for(project.stubs / "researcher-3.env")
        self.assertFalse((project.stubs / "fish.log").exists())

    def test_no_name_flag_for_an_older_cli(self):
        project = Project(self, "epsilon")
        self.start(project, STUB_NO_NAME="1")
        self.assertNotIn("--name", (project.stubs / "launch.log").read_text())

    # 3. safe pane addressing -------------------------------------------------

    def test_pane_checks_before_typing(self):
        mine, other = Project(self, "zeta"), Project(self, "eta")
        self.start(mine)
        self.start(other)
        sent = mine.pane("send-to", "researcher-2", "Task T-0001: hello")
        self.assertEqual(sent.returncode, 0, sent.stderr)
        time.sleep(0.3)
        self.assertEqual(mine.input_of("researcher-2"), "Task T-0001: hello\n")
        self.assertEqual(mine.input_of("researcher-1"), "")

        # A window that is not the worker's own, or a pane that names
        # another worker, is refused.
        sock = mine.record()["SOCKET"]
        mine.tmux(sock, "rename-window", "-t", f"={mine.session}:=researcher-2", "renamed")
        refused = mine.pane("send-to", "researcher-2", "should not arrive")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("is not running in this project's session", refused.stderr)
        mine.tmux(sock, "rename-window", "-t", f"={mine.session}:=renamed", "researcher-2")
        mine.tmux(sock, "set-option", "-p", "-t", f"={mine.session}:=researcher-2", "@teamflow_worker", "researcher-1")
        refused = mine.pane("send-to", "researcher-2", "should not arrive")
        self.assertNotEqual(refused.returncode, 0)
        mine.tmux(sock, "set-option", "-p", "-t", f"={mine.session}:=researcher-2", "@teamflow_worker", "researcher-2")
        # A second pane in the worker's window makes the target ambiguous.
        extra = mine.tmux(sock, "split-window", "-d", "-P", "-F", "#{pane_id}", "-t",
                          f"={mine.session}:=researcher-2", "sleep 60").stdout.strip()
        refused = mine.pane("send-to", "researcher-2", "should not arrive")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("more than one pane", refused.stderr)
        mine.tmux(sock, "kill-pane", "-t", extra)
        sent = mine.pane("send-to", "researcher-2", "back to one pane")
        self.assertEqual(sent.returncode, 0, sent.stderr)

        # In the view window, a worker's pane is found by its option.
        viewed = mine.teamflow("view")
        self.assertEqual(viewed.returncode, 0, viewed.stderr)
        self.assertIn(f"tmux -L {sock} attach -t {mine.session}:view", viewed.stdout)
        sent = mine.pane("send-to", "researcher-1", "in the view")
        self.assertEqual(sent.returncode, 0, sent.stderr)
        tail = mine.pane("tail", "researcher-1")
        self.assertEqual(tail.returncode, 0, tail.stderr)
        self.assertEqual(mine.teamflow("view", "--close").returncode, 0)
        time.sleep(0.3)
        self.assertEqual(mine.input_of("researcher-1"), "in the view\n")

        # A window with the worker's name in another session of the same
        # server is not the worker's.
        subprocess.run(["tmux", "-L", sock, "new-session", "-d", "-s", "intruder", "-n", "researcher-1",
                        f"env AGENT_ROLE=intruder {mine.stubs / 'claude'}"], env=mine.env(), check=True)
        mine.wait_for(mine.stubs / "intruder.env")
        registry = (mine.mailbox / "panes.tsv").read_text()
        mine.tmux(sock, "kill-window", "-t", f"={mine.session}:=researcher-1")
        refused = mine.pane("send-to", "researcher-1", "should not arrive")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn(f"worker 'researcher-1' is not running in this project's session {mine.session}",
                      refused.stderr)
        time.sleep(0.3)
        self.assertEqual(mine.input_of("intruder"), "")
        mine.tmux(sock, "kill-session", "-t", "=intruder")

        # A stopped team keeps no claim on pane IDs, even when another
        # project's panes carry the same ones.
        stale = mine.record()
        self.assertEqual(mine.teamflow("--kill").returncode, 0)
        (mine.mailbox / "panes.tsv").write_text(registry)
        (mine.mailbox / "active").write_text("".join(f"{k}={v}\n" for k, v in stale.items()))
        self.assertEqual(set(registry.split()) & {"%0", "%1"}, {"%0", "%1"})
        refused = mine.pane("send-to", "researcher-1", "should not arrive")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("teamflow start --last", refused.stderr)
        # The same record without SOCKET, as an older teamflow wrote it,
        # looks on the default server, where another project's old team runs
        # a window with this worker's name and these pane IDs.
        (mine.mailbox / "active").write_text("".join(f"{k}={v}\n" for k, v in stale.items() if k != "SOCKET"))
        env = other.env()
        subprocess.run(["tmux", "-L", "default", "new-session", "-d", "-s", other.session, "-n", "researcher-1",
                        "sleep 60"], env=env, check=True)
        refused = mine.pane("send-to", "researcher-1", "should not arrive")
        self.assertNotEqual(refused.returncode, 0)
        time.sleep(0.3)
        for project in (mine, other):
            for role in ("researcher-1", "researcher-2"):
                self.assertNotIn("should not arrive", project.input_of(role))

    # 4 and 5. doctor and teams on the old shared server ----------------------

    def legacy_team(self, project):
        """A team as teamflow 0.3 started it: on the default server, with
        the Delegator's session variables, and a record without SOCKET."""
        env = project.env(AGENT_ROLE="researcher-1")
        command = f"exec {project.stubs / 'claude'} --legacy"
        subprocess.run(["tmux", "-L", "default", "new-session", "-d", "-s", project.session, "-n", "researcher-1",
                        "-c", str(project.root), command], env=env, check=True)
        created, pane = project.tmux("default", "list-panes", "-s", "-t", f"={project.session}:", "-F",
                                     "#{session_created} #{pane_id}").stdout.split()
        project.mailbox.mkdir(parents=True, exist_ok=True)
        (project.mailbox / "active").write_text(
            f"SESSION={project.session}\nCREATED={created}\nCONFIG={project.root / 'teamflow.conf'}\n"
            f"ROOT={project.root}\nMODE=workers\nTOOL=/old/teamflow\nVERSION=v0.2.0-1-g88a954f\n")
        (project.mailbox / "panes.tsv").write_text(f"researcher-1\t{pane}\n")
        (project.mailbox / "running.conf").write_text(CONFIG.replace("[worker.researcher-2]\ntype = researcher\n", ""))
        # Its conversation, which the restart resumes.
        sid = "33333333-3333-4333-8333-333333333333"
        (project.mailbox / "sessions").mkdir(exist_ok=True)
        (project.mailbox / "sessions/researcher-1").write_text(
            f"ROLE=researcher-1\nCLI=claude\nSESSION_ID={sid}\nCWD={project.root}\nSTATUS=live\n")
        transcripts = project.claude_home / "projects/old"
        transcripts.mkdir(parents=True)
        (transcripts / f"{sid}.jsonl").write_text("{}\n")
        project.wait_for(project.stubs / "researcher-1.env")
        return sid

    @unittest.skipUnless(PS_ENV, "ps shows no process environment here")
    def test_doctor_reports_the_shared_server_and_leaked_variables(self):
        project = Project(self, "theta")
        self.legacy_team(project)
        doctor = project.teamflow("doctor")
        self.assertIn(f"PROBLEM  team {project.session} runs on the shared default tmux server", doctor.stdout)
        leak = [line for line in doctor.stdout.splitlines() if "worker researcher-1 started with" in line]
        self.assertEqual(len(leak), 1, doctor.stdout)
        for name in STRIPPED:
            self.assertIn(name, leak[0])
        for value in STRIPPED.values():
            if len(value) > 2:
                self.assertNotIn(value, doctor.stdout)

    def test_team_on_the_shared_server_moves_to_its_own(self):
        project = Project(self, "iota")
        sid = self.legacy_team(project)
        own = project.session
        refused = project.teamflow("start")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("runs on the shared default tmux server", refused.stderr)
        self.assertIn("teamflow --kill", refused.stderr)
        self.assertIn("teamflow start --last", refused.stderr)
        self.assertNotEqual(project.tmux(own, "has-session", check=False).returncode, 0, "a second team started")
        refused = project.teamflow("add", "researcher")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("runs on the shared default tmux server", refused.stderr)
        roster = project.teamflow("list")
        self.assertEqual(roster.returncode, 0, roster.stderr)
        self.assertIn("teamflow start --last", roster.stderr)
        self.assertIn(f"tmux -L default attach -t {project.session}:researcher-1", roster.stdout)
        # The Delegator can still reach the old team's workers, safely, until it moves.
        sent = project.pane("send-to", "researcher-1", "still reachable")
        self.assertEqual(sent.returncode, 0, sent.stderr)
        self.assertIn("shared default tmux server", sent.stderr)

        killed = project.teamflow("--kill")
        self.assertEqual(killed.returncode, 0, killed.stderr)
        self.assertIn("on the shared default tmux server", killed.stderr)
        self.assertNotEqual(project.tmux("default", "has-session", "-t", f"={project.session}",
                                         check=False).returncode, 0)
        self.assertFalse((project.mailbox / "active").exists())
        moved = project.teamflow("start", "--last")
        self.assertEqual(moved.returncode, 0, moved.stderr)
        self.assertEqual(project.record()["SOCKET"], own)
        self.assertIn(f"researcher-1 --resume {sid}", (project.stubs / "launch.log").read_text())
        self.assertNotIn("researcher-2", project.tmux(own, "list-windows", "-t", f"={own}:", "-F",
                                                      "#{window_name}").stdout)


if __name__ == "__main__":
    unittest.main()
