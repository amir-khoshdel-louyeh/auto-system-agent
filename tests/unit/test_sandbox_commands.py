"""Phase 10 item [99]: a shell command runs inside the sandbox, not in-process.

Three properties are asserted rather than assumed:

1. the command string is handed to a *guest* shell inside the sandbox, and an
   explicit argv skips the shell entirely;
2. when the host cannot isolate a command, the request is **refused** and the
   command provably does not run (the workspace stays empty);
3. a real run leaves its file changes as diff metadata and reports a non-zero
   exit or a timeout as `ok=False` instead of success.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.commands import (
    SHELL,
    clamp_timeout,
    command_argv,
    command_request,
    isolation_available,
    run_command,
)
from sandbox.contract import DEFAULT_TIMEOUT_S, MAX_TIMEOUT_S, MIN_TIMEOUT_S


class ArgvTests(unittest.TestCase):
    def test_command_string_goes_through_a_guest_shell(self):
        self.assertEqual(command_argv("echo hi"), (SHELL, "-c", "echo hi"))

    def test_explicit_argv_skips_the_shell(self):
        self.assertEqual(command_argv("ignored", argv=["/bin/echo", "hi"]), ("/bin/echo", "hi"))

    def test_request_is_valid_and_carries_its_bounds(self):
        request = command_request("ls", timeout_s=2.0, max_output_bytes=128)
        self.assertEqual(request.kind, "command")
        self.assertEqual(request.timeout_s, 2.0)
        self.assertEqual(request.max_output_bytes, 128)
        self.assertEqual(request.validate(), ())

    def test_empty_command_is_refused_by_the_contract(self):
        request = command_request("", argv=[])
        self.assertTrue(request.validate(), "an empty argv must not be a valid request")


class ClampTests(unittest.TestCase):
    def test_values_inside_the_contract_pass_through(self):
        self.assertEqual(clamp_timeout(1.5), 1.5)

    def test_a_too_small_or_too_large_value_is_clamped(self):
        self.assertEqual(clamp_timeout(0.0001), MIN_TIMEOUT_S)
        self.assertEqual(clamp_timeout(10_000), MAX_TIMEOUT_S)

    def test_nonsense_falls_back_to_the_default(self):
        for value in ("soon", None, {}, float("nan")):
            self.assertEqual(clamp_timeout(value), DEFAULT_TIMEOUT_S)


class ProcMountTests(unittest.TestCase):
    """The isolation label must say which layer really ran (item [99])."""

    def _argv(self, *, private_proc: bool):
        from sandbox.contract import SandboxRequest
        from sandbox.runner import _bwrap_argv, _layout

        layout = _layout(Path(tempfile.mkdtemp()), None)
        request = SandboxRequest(kind="command", command=("/bin/true",))
        return _bwrap_argv(request, layout, "/out/result.json", private_proc=private_proc)

    def test_a_private_proc_mount_is_opt_out_of_not_silently_dropped(self):
        self.assertIn("--proc", self._argv(private_proc=True))
        self.assertNotIn("--proc", self._argv(private_proc=False))

    @unittest.skipUnless(isolation_available(), "bwrap with user namespaces is unavailable")
    def test_a_host_without_a_private_proc_reports_the_reduced_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("sandbox.runner.bwrap_supports_proc", return_value=False):
                result = run_command("true", workspace=Path(tmp) / "ws")
        self.assertTrue(result.ok, result.output_summary)
        self.assertEqual(result.isolation, "bwrap-no-proc")


class RefusalTests(unittest.TestCase):
    def test_isolation_availability_is_a_boolean(self):
        self.assertIsInstance(isolation_available(), bool)

    def test_without_os_isolation_the_command_is_refused_and_never_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            with patch("sandbox.runner.bwrap_available", return_value=False):
                result = run_command("touch escaped.txt", workspace=workspace)
            self.assertFalse(result.ok)
            self.assertEqual(result.exit_code, 126)
            self.assertIn("OS isolation", result.output_summary)
            self.assertIn("refused", result.output_summary)
            self.assertEqual(list(workspace.rglob("*")), [], "a refused command must not run")


@unittest.skipUnless(isolation_available(), "bwrap with user namespaces is unavailable")
class RealRunTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name) / "ws"

    def test_a_command_runs_in_the_workspace_and_reports_the_diff(self):
        result = run_command(
            "mkdir -p demo-app && printf '# demo' > demo-app/README.md",
            workspace=self.workspace,
        )
        self.assertTrue(result.ok, result.output_summary)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.isolation, "bwrap")
        self.assertEqual((self.workspace / "demo-app/README.md").read_text(), "# demo")
        actions = {change.path: change.action for change in result.files}
        self.assertEqual(actions.get("demo-app/README.md"), "created")

    def test_a_failing_command_is_not_ok(self):
        result = run_command("echo broken >&2; exit 3", workspace=self.workspace)
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 3)
        self.assertIn("exit 3", result.output_summary)
        self.assertIn("broken", result.stderr)

    def test_a_hanging_command_is_killed_at_the_timeout(self):
        result = run_command("sleep 5", workspace=self.workspace, timeout_s=0.3)
        self.assertFalse(result.ok)
        self.assertTrue(result.timed_out)
        self.assertIn("timed out", result.output_summary)

    def test_output_is_truncated_to_the_budget(self):
        result = run_command(
            "printf 'x%.0s' $(seq 1 5000)", workspace=self.workspace, max_output_bytes=64
        )
        self.assertTrue(result.ok, result.output_summary)
        self.assertEqual(len(result.stdout), 64)


if __name__ == "__main__":
    unittest.main()
