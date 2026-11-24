"""teamflow status / nudge on a real mailbox; panes and hooks are faked."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts/teamflow"
TASK = ROOT / ".agents/lib/task.sh"


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.repo = self.base / "repo"
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
                    "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                    "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
        for k in ["AGENT_MAILBOX", "AGENT_ROLE", "AGENT_ID", "TEAMFLOW_ON_ATTENTION", "TEAMFLOW_PANE_CMD"]:
            self.env.pop(k, None)
        self.run_cmd(self.base, "git", "init", "-q", "--initial-branch=main", str(self.repo))
        self.mailbox = self.repo / ".git/teamflow"
        # A fake pane records what it would type, so nothing needs tmux.
        self.sent = self.base / "sent.log"
        fake = self.base / "fake-pane"
        fake.write_text(f'#!/bin/sh\necho "$@" >> "{self.sent}"\n')
        fake.chmod(0o755)
        self.env["TEAMFLOW_PANE_CMD"] = str(fake)

    def run_cmd(self, cwd, *argv, data="", env=None, check=True):
        p = subprocess.run(argv, cwd=cwd, env=env or self.env, input=data, text=True, capture_output=True)
        if check:
            self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        return p

    def task(self, actor, *args, data=""):
        env = {**self.env, "AGENT_ROLE": actor, "AGENT_MAILBOX": str(self.mailbox)}
        return self.run_cmd(self.repo, "bash", str(TASK), *args, data=data, env=env).stdout.strip()

    def tf(self, *args, at="2026-11-24T12:00:00Z", check=True):
        env = {**self.env, "TEAMFLOW_NOW": at}
        return self.run_cmd(self.repo, "bash", str(TOOL), *args, env=env, check=check)

    def backdate(self, tid, created):
        md = self.mailbox / "tasks" / tid / "task.md"
        lines = md.read_text().splitlines()
        md.write_text("\n".join(f"created: {created}" if l.startswith("created:") else l
                                for l in lines) + "\n")
        ev = self.mailbox / "tasks" / tid / "events"
        if ev.exists():
            ev.write_text("".join(f"{created} {l.split(' ', 1)[1]}\n" for l in ev.read_text().splitlines()))

    def statuses(self, at="2026-11-24T12:00:00Z"):
        rows = json.loads(self.tf("status", "--json", at=at).stdout)
        return {r["id"]: r for r in rows}

    def test_flags_each_kind_of_stuck_task(self):
        fresh = self.task("delegator", "new", "dev-1", "fresh")
        lost = self.task("delegator", "new", "dev-1", "lost handoff")
        slow = self.task("delegator", "new", "dev-2", "slow")
        self.task("dev-2", "take", slow)
        unread = self.task("delegator", "new", "dev-3", "unread result")
        self.task("dev-3", "done", unread, data="ok")
        read = self.task("delegator", "new", "dev-3", "released result")
        self.task("dev-3", "done", read, data="ok")
        self.task("delegator", "release", read)
        for tid in (lost, unread, read):
            self.backdate(tid, "2026-11-24T11:00:00Z")
        self.backdate(slow, "2026-11-24T08:00:00Z")
        self.backdate(fresh, "2026-11-24T11:58:00Z")
        rows = self.statuses()
        self.assertIsNone(rows[fresh]["flag"])
        self.assertEqual(rows[lost]["flag"], "not-picked-up")
        self.assertEqual(rows[slow]["flag"], "long-running")
        self.assertEqual(rows[unread]["flag"], "awaiting-delegator")
        self.assertIsNone(rows[read]["flag"])
        text = self.tf("status").stdout
        self.assertIn("3 of 5 task(s) need attention", text)

    def test_nudge_resends_a_bounded_number_of_times(self):
        lost = self.task("delegator", "new", "dev-1", "lost handoff")
        self.backdate(lost, "2026-11-24T11:00:00Z")
        self.tf("nudge", "--dry-run", "--max-resends", "2")
        self.assertFalse(self.sent.exists(), "a dry run types nothing")
        self.tf("nudge", "--max-resends", "2", at="2026-11-24T12:00:00Z")
        self.tf("nudge", "--max-resends", "2", at="2026-11-24T12:05:00Z")
        self.assertEqual(len(self.sent.read_text().splitlines()), 1, "a resend restarts the pickup wait")
        self.tf("nudge", "--max-resends", "2", at="2026-11-24T12:15:00Z")
        self.tf("nudge", "--max-resends", "2", at="2026-11-24T12:30:00Z")
        lines = self.sent.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn(f"send-to dev-1 Task {lost}. Details: task.sh read {lost}", lines[0])
        self.assertEqual(self.statuses(at="2026-11-24T12:30:00Z")[lost]["resends"], 2)
        self.assertEqual(self.task("delegator", "status", lost), "assigned", "nudge never changes status")
        self.assertIn("check the worker", self.tf("nudge", "--max-resends", "2", at="2026-11-24T12:45:00Z").stdout)

    def test_attention_hook_runs_once_per_change_and_clears(self):
        hook_log = self.base / "hook.log"
        hook = f'cat >> "{hook_log}"; echo --- >> "{hook_log}"'
        a = self.task("delegator", "new", "dev-1", "first result")
        self.task("dev-1", "done", a, data="ok")
        self.backdate(a, "2026-11-24T11:00:00Z")
        self.tf("nudge", "--on-attention", hook)
        self.tf("nudge", "--on-attention", hook)
        self.assertEqual(hook_log.read_text().count("---"), 1, "unchanged attention runs the hook once")
        att = json.loads((self.mailbox / "attention").read_text())
        self.assertEqual([t["id"] for t in att["tasks"]], [a])
        b = self.task("delegator", "new", "dev-1", "second result")
        self.task("dev-1", "done", b, data="ok")
        self.backdate(b, "2026-11-24T11:00:00Z")
        self.tf("nudge", "--on-attention", hook)
        self.assertEqual(hook_log.read_text().count("---"), 2, "a new waiting result runs the hook again")
        self.task("delegator", "release", a)
        self.task("delegator", "release", b)
        self.tf("nudge", "--on-attention", hook)
        self.assertFalse((self.mailbox / "attention").exists(), "attention clears once results are read")

    def test_failing_hook_is_retried(self):
        a = self.task("delegator", "new", "dev-1", "result")
        self.task("dev-1", "done", a, data="ok")
        self.backdate(a, "2026-11-24T11:00:00Z")
        self.assertNotEqual(self.tf("nudge", "--on-attention", "exit 3", check=False).returncode, 0)
        self.assertFalse((self.mailbox / "attention.sum").exists())
        self.tf("nudge", "--on-attention", "true")
        self.assertTrue((self.mailbox / "attention.sum").exists())

    def test_no_hook_does_not_consume_attention(self):
        a = self.task("delegator", "new", "dev-1", "result")
        self.task("dev-1", "done", a, data="ok")
        self.backdate(a, "2026-11-24T11:00:00Z")
        self.tf("nudge")
        self.assertTrue((self.mailbox / "attention").exists())
        self.assertFalse((self.mailbox / "attention.sum").exists())
        marker = self.base / "notified"
        self.tf("nudge", "--on-attention", f'touch "{marker}"')
        self.assertTrue(marker.exists())

    def test_overlapping_nudge_does_not_duplicate_hook(self):
        a = self.task("delegator", "new", "dev-1", "result")
        self.task("dev-1", "done", a, data="ok")
        self.backdate(a, "2026-11-24T11:00:00Z")
        marker = self.base / "entered"
        release = self.base / "release"
        hook = f'echo entered >> "{marker}"; while [ ! -e "{release}" ]; do sleep 0.02; done'
        argv = ["bash", str(TOOL), "nudge", "--on-attention", hook]
        env = {**self.env, "TEAMFLOW_NOW": "2026-11-24T12:00:00Z"}
        with subprocess.Popen(argv, cwd=self.repo, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) as p:
            try:
                limit = time.monotonic() + 3
                while not marker.exists() and time.monotonic() < limit:
                    time.sleep(0.02)
                self.assertTrue(marker.exists(), "first hook entered")
                with subprocess.Popen(argv, cwd=self.repo, env=env, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True) as sibling:
                    try:
                        sibling.communicate(timeout=0.8)
                    except subprocess.TimeoutExpired:
                        pass
                    finally:
                        release.touch()
                    _, err = sibling.communicate(timeout=3)
                    self.assertEqual(sibling.returncode, 0, err)
            finally:
                release.touch()
            _, err = p.communicate(timeout=3)
            self.assertEqual(p.returncode, 0, err)
        self.assertEqual(marker.read_text().splitlines(), ["entered"])

    def test_failed_sends_use_attempt_budget_and_report_failure(self):
        a = self.task("delegator", "new", "dev-1", "lost")
        self.backdate(a, "2026-11-24T11:00:00Z")
        Path(self.env["TEAMFLOW_PANE_CMD"]).write_text(
            f'#!/bin/sh\necho attempt >> "{self.sent}"\necho "private diagnostic" >&2\nexit 9\n')
        first = self.tf("nudge", "--max-resends", "1", check=False)
        self.assertNotEqual(first.returncode, 0)
        self.assertNotIn("private diagnostic", first.stdout + first.stderr)
        self.tf("nudge", "--max-resends", "1", at="2026-11-24T12:30:00Z")
        self.assertEqual(self.sent.read_text().splitlines(), ["attempt"])
        self.assertNotIn("private diagnostic", (self.mailbox / "tasks" / a / "events").read_text())

    def test_pickup_between_two_scheduled_resends_is_not_stale(self):
        a = self.task("delegator", "new", "dev-1", "first")
        b = self.task("delegator", "new", "dev-2", "second")
        self.backdate(a, "2026-11-24T11:00:00Z")
        self.backdate(b, "2026-11-24T11:00:00Z")
        Path(self.env["TEAMFLOW_PANE_CMD"]).write_text(
            f'#!/bin/sh\necho "$@" >> "{self.sent}"\n'
            f'if [ "$2" = dev-1 ]; then AGENT_ROLE=dev-2 bash "{TASK}" take "{b}"; fi\n')
        self.tf("nudge", "--max-resends", "2")
        self.assertEqual(len(self.sent.read_text().splitlines()), 1)
        self.assertEqual(self.task("delegator", "status", b), "in-progress")

    def test_attention_is_refreshed_after_a_pickup(self):
        a = self.task("delegator", "new", "dev-1", "first")
        b = self.task("delegator", "new", "dev-2", "waiting")
        self.task("dev-2", "done", b, data="ok")
        self.backdate(a, "2026-11-24T11:00:00Z")
        self.backdate(b, "2026-11-24T11:00:00Z")
        Path(self.env["TEAMFLOW_PANE_CMD"]).write_text(
            f'#!/bin/sh\nAGENT_ROLE=delegator bash "{TASK}" release "{b}"\n')
        hook_output = self.base / "notified"
        self.tf("nudge", "--max-resends", "2", "--on-attention", f'touch "{hook_output}"')
        self.assertFalse(hook_output.exists())
        self.assertFalse((self.mailbox / "attention").exists())

    def test_spawning_a_hanging_hook_is_bounded(self):
        spec = importlib.util.spec_from_file_location("status_test_tool",
                                                       ROOT / "scripts/teamflow_status.py")
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        start = time.monotonic()
        rc, detail = tool.run_bounded(["/bin/sh", "-c", "sleep 4"], body="{}", timeout=0.1)
        self.assertIsNone(rc)
        self.assertIn("timed out", detail)
        self.assertLess(time.monotonic() - start, 3)

    def test_resends_require_opt_in_but_attention_still_works(self):
        lost = self.task("delegator", "new", "dev-1", "lost")
        a = self.task("delegator", "new", "dev-2", "result")
        self.task("dev-2", "done", a, data="ok")
        for task in (lost, a):
            self.backdate(task, "2026-11-24T11:00:00Z")
        marker = self.base / "notified"
        self.tf("nudge", "--on-attention", f'touch "{marker}"')
        self.assertTrue(marker.exists())
        self.assertFalse(self.sent.exists(), "default nudge must not type")
        self.assertEqual(self.statuses()[lost]["resends"], 0)

    def test_never_registers_as_delegator(self):
        lost = self.task("delegator", "new", "dev-1", "lost")
        self.backdate(lost, "2026-11-24T11:00:00Z")
        self.tf("status")
        self.tf("nudge")
        self.assertFalse((self.mailbox / "delegator").exists())

    def test_no_mailbox_is_not_an_error(self):
        empty = self.base / "empty"
        self.run_cmd(self.base, "git", "init", "-q", str(empty))
        p = self.run_cmd(empty, "bash", str(TOOL), "status")
        self.assertIn("no teamflow mailbox", p.stdout)
        self.assertEqual(json.loads(self.run_cmd(empty, "bash", str(TOOL), "status", "--json").stdout), [])


if __name__ == "__main__":
    unittest.main()
