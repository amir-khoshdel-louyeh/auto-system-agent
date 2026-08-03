"""Phase 8 gate: untrusted code cannot leave its workspace (items [73], [74]).

Each case seeds a plugin into a disposable workspace and calls the real
runner. The assertions are made twice on purpose: the result must report the
violation, and the host filesystem must be unchanged — the second check is
the actual gate, because a truthful report about a successful escape would
still be a failure.

Both isolation layers are exercised: namespace isolation (``bwrap``) when the
host allows it, and the in-Python audit hook fallback otherwise. The command
kind is refused without OS isolation rather than run unguarded.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.contract import SandboxRequest
from sandbox.runner import MAX_FILE_BYTES, run

PLUGIN_HEADER = "def run(ctx):\n"


def plugin(body: str) -> str:
    """A plugin module whose ``run`` body is ``body`` (already indented)."""
    return PLUGIN_HEADER + body


def write_inside(action: str = "open('inside.txt','w').write('hello')") -> str:
    return plugin(f"    {action}\n    return {{'done': True}}\n")


class SandboxCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.workspace = self.root / "workspace"
        self.outside = self.root / "outside"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.outside.mkdir(parents=True, exist_ok=True)

    def execute(self, code: str, *, timeout_s: float = 5.0, **kwargs):
        return run(
            SandboxRequest(
                kind="plugin",
                module="plugin.py",
                files={"plugin.py": code},
                timeout_s=timeout_s,
                **kwargs,
            ),
            root=self.root,
            keep_root=True,
        )

    def assert_outside_untouched(self):
        self.assertEqual(list(self.outside.rglob("*")), [])

    def assert_denied(self, result, needle: str):
        self.assertFalse(result.ok, result.output_summary)
        self.assertTrue(
            any(needle in denial for denial in result.denied),
            f"{needle!r} missing from {result.denied}",
        )


class WorkspaceTests(SandboxCase):
    def test_plugin_writes_inside_the_workspace_and_reports_file_metadata(self):
        result = self.execute(write_inside())
        self.assertTrue(result.ok, result.stderr)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.result, {"done": True})
        created = [change for change in result.files if change.path == "inside.txt"]
        self.assertEqual(created[0].action, "created")
        self.assertEqual(created[0].size, 5)
        self.assertEqual(
            created[0].sha256, "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        )
        self.assertTrue((self.workspace / "inside.txt").exists())

    def test_deleted_file_is_reported(self):
        (self.workspace / "old.txt").write_text("old", encoding="utf-8")
        result = self.execute(write_inside("__import__('os').remove('old.txt')"))
        self.assertTrue(result.ok, result.stderr)
        deleted = [change for change in result.files if change.action == "deleted"]
        self.assertEqual([change.path for change in deleted], ["old.txt"])
        self.assertFalse((self.workspace / "old.txt").exists())

    def test_isolation_layer_is_reported(self):
        result = self.execute(write_inside())
        self.assertIn(result.isolation, ("bwrap", "audit"))


class EscapeTests(SandboxCase):
    def test_relative_path_cannot_climb_out(self):
        result = self.execute(write_inside("open('../outside/evil.txt','w').write('pwned')"))
        self.assert_denied(result, "write outside workspace")
        self.assert_outside_untouched()

    def test_absolute_path_cannot_escape(self):
        target = self.root / "absolute-escape.txt"
        result = self.execute(write_inside(f"open({str(target)!r},'w').write('pwned')"))
        self.assert_denied(result, "write outside workspace")
        self.assertFalse(target.exists())

    def test_symlink_cannot_escape(self):
        link = self.workspace / "escape"
        link.symlink_to(self.outside, target_is_directory=True)
        result = self.execute(write_inside("open('escape/through-link.txt','w').write('pwned')"))
        self.assert_denied(result, "write outside workspace")
        self.assert_outside_untouched()

    def test_deleting_outside_is_denied_and_the_file_survives(self):
        sentinel = self.outside / "sentinel.txt"
        sentinel.write_text("keep", encoding="utf-8")
        result = self.execute(write_inside(f"__import__('os').remove({str(sentinel)!r})"))
        self.assert_denied(result, "outside workspace")
        self.assertTrue(sentinel.exists())

    def test_reading_host_secrets_is_denied(self):
        result = self.execute(plugin("    return {'p': open('/etc/passwd').read()}\n"))
        self.assert_denied(result, "read outside sandbox")

    def test_spawning_a_process_is_denied(self):
        result = self.execute(
            plugin(
                "    import subprocess\n    subprocess.run(['sh', '-c', 'touch /tmp/x'])\n    return {}\n"
            )
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("subprocess" in denial for denial in result.denied), result.denied)

    def test_network_connections_are_denied(self):
        result = self.execute(
            plugin(
                "    import socket\n"
                "    sock = socket.socket()\n"
                "    sock.connect(('127.0.0.1', 9))\n"
                "    return {}\n"
            )
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("socket" in denial for denial in result.denied), result.denied)


class LimitTests(SandboxCase):
    def test_wall_clock_timeout_kills_the_process(self):
        result = self.execute(
            plugin("    import time\n    time.sleep(5)\n    return {}\n"), timeout_s=0.4
        )
        self.assertTrue(result.timed_out)
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, -9)
        self.assertIn("timed out", result.output_summary)

    def test_file_size_limit_stops_an_endless_writer(self):
        (self.workspace / "data").mkdir()
        result = self.execute(
            plugin("    open('data/big.bin','wb').write(b'x' * 5_000_000)\n    return {}\n")
        )
        self.assertFalse(result.ok)
        written = [path for path in (self.workspace / "data").iterdir()]
        self.assertLessEqual(written[0].stat().st_size, MAX_FILE_BYTES)

    def test_memory_bomb_is_stopped(self):
        result = self.execute(
            plugin("    blob = bytearray(1024 * 1024 * 1024)\n    return {'n': len(blob)}\n")
        )
        self.assertFalse(result.ok)


class CommandTests(SandboxCase):
    def test_command_needs_os_isolation(self):
        with patch("sandbox.runner.bwrap_available", return_value=False):
            result = run(
                SandboxRequest(kind="command", command=("echo", "hi")),
                root=self.root,
                keep_root=True,
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 126)
        self.assertIn("OS isolation", result.output_summary)

    def test_command_runs_under_namespace_isolation_when_available(self):
        from sandbox.runner import bwrap_available

        if not bwrap_available():
            self.skipTest("bwrap unavailable on this host")
        code = "open('/work/from-command.txt', 'w').write('ok')"
        result = run(
            SandboxRequest(kind="command", command=(sys.executable, "-I", "-c", code)),
            root=self.root,
            keep_root=True,
        )
        self.assertTrue(result.ok, result.output_summary)
        self.assertEqual(result.isolation, "bwrap")
        self.assertTrue((self.workspace / "from-command.txt").exists())
        self.assertIn("from-command.txt", [change.path for change in result.files])


if __name__ == "__main__":
    unittest.main()
