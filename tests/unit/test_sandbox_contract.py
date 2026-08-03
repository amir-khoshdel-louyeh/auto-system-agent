"""Phase 8 item [73]: the frozen sandbox request/response contract.

A malformed request is refused before execution (relative-only paths, bounded
timeout and output, one payload kind), and the result always serializes to
the same key set so clients never guess at the shape.
"""

import unittest

from sandbox.contract import (
    CONTRACT_VERSION,
    FILE_ACTIONS,
    KINDS,
    FileChange,
    SandboxError,
    SandboxRequest,
    SandboxResult,
)

PLUGIN = "def run(ctx):\n    return {}\n"


class RequestValidationTests(unittest.TestCase):
    def test_minimal_plugin_request_is_valid(self):
        request = SandboxRequest(module="plugin.py", files={"plugin.py": PLUGIN})
        self.assertEqual(request.validate(), ())

    def test_vocabulary_is_frozen(self):
        self.assertEqual(CONTRACT_VERSION, "sandbox-v1")
        self.assertEqual(KINDS, ("plugin", "command"))
        self.assertEqual(FILE_ACTIONS, ("created", "modified", "deleted"))

    def test_unknown_kind_and_version_are_refused(self):
        problems = SandboxRequest(kind="shell", contract_version="sandbox-v0").validate()
        self.assertTrue(any("unknown kind" in problem for problem in problems))
        self.assertTrue(any("contract_version" in problem for problem in problems))

    def test_plugin_module_must_be_a_relative_python_file(self):
        for module in ("/etc/plugin.py", "../plugin.py", "a/../../plugin.py", "plugin.txt", ""):
            with self.subTest(module=module):
                problems = SandboxRequest(module=module).validate()
                self.assertTrue(problems, f"{module!r} should be refused")

    def test_payload_kinds_are_exclusive(self):
        problems = SandboxRequest(kind="plugin", module="p.py", command=("echo",)).validate()
        self.assertTrue(any("must not also carry a command" in problem for problem in problems))
        problems = SandboxRequest(kind="command", module="p.py", command=("echo",)).validate()
        self.assertTrue(any("must not also carry a module" in problem for problem in problems))
        self.assertEqual(SandboxRequest(kind="command", command=("echo", "hi")).validate(), ())

    def test_timeout_and_output_bounds(self):
        self.assertTrue(SandboxRequest(module="p.py", timeout_s=99).validate())
        self.assertTrue(SandboxRequest(module="p.py", timeout_s=0.0).validate())
        self.assertTrue(SandboxRequest(module="p.py", max_output_bytes=10_000_000).validate())

    def test_seed_files_must_stay_inside_the_workspace(self):
        problems = SandboxRequest(module="p.py", files={"../evil.py": PLUGIN}).validate()
        self.assertTrue(any("seed file" in problem for problem in problems))
        self.assertTrue(SandboxRequest(module="p.py", files={"/abs.py": PLUGIN}).validate())

    def test_seed_budget(self):
        files = {f"f{index}.txt": "x" for index in range(40)}
        self.assertTrue(SandboxRequest(module="p.py", files=files).validate())
        big = {"a.txt": "x" * 300_000}
        self.assertTrue(SandboxRequest(module="p.py", files=big).validate())

    def test_require_valid_raises_with_all_reasons(self):
        with self.assertRaises(SandboxError) as caught:
            SandboxRequest(kind="nope", module="/x.py", timeout_s=99).require_valid()
        self.assertIn("unknown kind", str(caught.exception))
        self.assertIn("timeout_s", str(caught.exception))

    def test_payload_is_json_safe_and_carries_no_host_paths(self):
        request = SandboxRequest(
            module="p.py", files={"p.py": PLUGIN}, params={"n": 1}, stdin="input"
        )
        payload = request.payload()
        self.assertEqual(payload["kind"], "plugin")
        self.assertEqual(payload["command"], [])
        self.assertEqual(payload["params"], {"n": 1})
        self.assertNotIn("files", payload)
        self.assertNotIn("timeout", payload)


class ResultShapeTests(unittest.TestCase):
    def test_result_serializes_the_frozen_keys(self):
        result = SandboxResult(ok=True, exit_code=0, output_summary="ok")
        body = result.as_dict()
        self.assertEqual(
            set(body),
            {
                "contract_version",
                "ok",
                "exit_code",
                "timed_out",
                "duration_ms",
                "stdout",
                "stderr",
                "output_summary",
                "result",
                "files",
                "denied",
                "violations",
                "isolation",
            },
        )

    def test_result_round_trips(self):
        original = SandboxResult(
            ok=False,
            exit_code=126,
            timed_out=True,
            duration_ms=12,
            stdout="out",
            stderr="err",
            output_summary="denied",
            result={"k": "v"},
            files=(FileChange("a.txt", "created", 3, "abc"),),
            denied=("write outside workspace: /etc/x",),
            violations=("outside workspace changed: x",),
            isolation="bwrap",
        )
        rebuilt = SandboxResult.from_dict(original.as_dict())
        self.assertEqual(rebuilt, original)
        self.assertEqual(rebuilt.files[0].path, "a.txt")

    def test_writes_filters_non_write_actions(self):
        result = SandboxResult(
            files=(FileChange("a", "created"), FileChange("b", "deleted")),
        )
        self.assertEqual(len(result.writes), 2)


if __name__ == "__main__":
    unittest.main()
