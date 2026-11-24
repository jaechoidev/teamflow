"""teamflow status / nudge on a real mailbox; panes and hooks are faked."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
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
        self.tf("nudge", "--dry-run")
        self.assertFalse(self.sent.exists(), "a dry run types nothing")
        self.tf("nudge", "--max-resends", "2", at="2026-11-24T12:00:00Z")
        self.tf("nudge", "--max-resends", "2", at="2026-11-24T12:05:00Z")
        self.assertEqual(len(self.sent.read_text().splitlines()), 1, "a resend restarts the pickup wait")
        self.tf("nudge", "--max-resends", "2", at="2026-11-24T12:15:00Z")
        self.tf("nudge", "--max-resends", "2", at="2026-11-24T12:30:00Z")
        lines = self.sent.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn(f"send-to dev-1 Task {lost}: lost handoff. Details: task.sh read {lost}", lines[0])
        self.assertEqual(self.statuses(at="2026-11-24T12:30:00Z")[lost]["resends"], 2)
        self.assertEqual(self.task("delegator", "status", lost), "assigned", "nudge never changes status")
        self.assertIn("check the worker", self.tf("nudge", at="2026-11-24T12:45:00Z").stdout)

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


if __name__ == "__main__":
    unittest.main()
