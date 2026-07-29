"""DSL + validator fixtures + W4-novalidate (proposal phase 2, items 18-24)."""

import unittest
from pathlib import Path

from engine.dsl.compiler import compile as compile_wf  # noqa: E402
from engine.dsl.grammar import ALLOWED_EVENTS, NODE_TYPES, TRIGGER_KEYS  # noqa: E402
from engine.dsl.loader import DSLParseError, load_text  # noqa: E402
from engine.validator.validator import (  # noqa: E402
    is_valid,
    topological_order,
    validate,
)

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
FIXTURES = SRC_DIR / "engine" / "validator" / "fixtures"


def load_fixture(name):
    return load_text((FIXTURES / name).read_text(encoding="utf-8"))


class DslLoaderTests(unittest.TestCase):
    def test_yaml_and_json_parse(self):
        wf = load_fixture("valid-minimal.yaml")
        self.assertEqual(wf.name, "minimal")
        self.assertEqual(wf.version, 3)
        wf_json = load_text('{"workflow":"j","version":3,"nodes":[]}', "json")
        self.assertEqual(wf_json.name, "j")

    def test_yaml_syntax_error_reports_line_and_col(self):
        broken = "workflow: w\nversion: 1\nnodes:\n  - id: a\n   type: shell\n"
        with self.assertRaises(DSLParseError) as ctx:
            load_text(broken)
        self.assertGreaterEqual(ctx.exception.line, 4)
        self.assertGreaterEqual(ctx.exception.col, 1)
        self.assertIn("YAML syntax error", str(ctx.exception))
        # still a ValueError so older call sites keep catching it
        self.assertIsInstance(ctx.exception, ValueError)

    def test_json_syntax_error_reports_line_and_col(self):
        with self.assertRaises(DSLParseError) as ctx:
            load_text('{"workflow": "w",,}', dsl_type="json")
        self.assertGreaterEqual(ctx.exception.line, 1)
        self.assertGreaterEqual(ctx.exception.col, 1)
        self.assertIn("JSON syntax error", str(ctx.exception))

    def test_non_mapping_top_level_is_a_parse_error(self):
        with self.assertRaises(DSLParseError) as ctx:
            load_text("- just\n- a list\n")
        self.assertIn("mapping", str(ctx.exception))
        self.assertEqual(ctx.exception.line, 1)

    def test_invalid_dsl_type_rejected(self):
        with self.assertRaises(ValueError):
            load_text("workflow: w", dsl_type="toml")


class ValidatorFixtureTests(unittest.TestCase):
    def test_valid_fixtures_pass(self):
        for name in ("valid-minimal.yaml", "valid-demo.yaml", "valid-gated-custom.yaml"):
            with self.subTest(fixture=name):
                self.assertEqual(validate(load_fixture(name)), [])

    def test_invalid_schema(self):
        errors = validate(load_fixture("invalid-missing-workflow.yaml"))
        self.assertTrue(any(e.code == "schema" for e in errors))
        self.assertTrue(all(e.line > 0 for e in errors))

    def test_invalid_type(self):
        errors = validate(load_fixture("invalid-bad-type.yaml"))
        self.assertTrue(any(e.code == "type" and "robot_arm" in e.message for e in errors))

    def test_invalid_missing_param(self):
        errors = validate(load_fixture("invalid-missing-param.yaml"))
        self.assertTrue(any(e.code == "type" and "'run'" in e.message for e in errors))

    def test_invalid_unknown_edge(self):
        errors = validate(load_fixture("invalid-unknown-edge.yaml"))
        self.assertTrue(any(e.code == "edge" and "ghost" in e.message for e in errors))

    def test_invalid_unknown_placeholder(self):
        errors = validate(load_fixture("invalid-unknown-placeholder.yaml"))
        self.assertTrue(any(e.code == "edge" and "ghost" in e.message for e in errors))

    def test_cyclic(self):
        errors = validate(load_fixture("cyclic-two-node.yaml"))
        self.assertTrue(any(e.code == "cycle" for e in errors))
        self.assertIsNone(topological_order(load_fixture("cyclic-two-node.yaml")))

    def test_policy_violation(self):
        errors = validate(load_fixture("policy-dangerous-no-approval.yaml"))
        self.assertTrue(any(e.code == "policy" for e in errors))

    def test_ten_fixtures_present(self):
        self.assertEqual(len(sorted(FIXTURES.glob("*.yaml"))), 10)


class FrozenGrammarTests(unittest.TestCase):
    """Item [18]: the five node types and three trigger types are frozen."""

    def test_five_node_types_frozen(self):
        self.assertEqual(NODE_TYPES, ("shell", "gui_click", "gui_type", "approval", "custom"))

    def test_three_trigger_types_frozen(self):
        self.assertEqual(TRIGGER_KEYS, ("manual", "cron", "event"))
        self.assertEqual(ALLOWED_EVENTS, ("file.arrived",))

    def test_webhook_trigger_is_out_of_scope(self):
        wf = load_text(
            "workflow: w\nversion: 1\ntriggers:\n  - webhook: /hooks/x\n"
            "nodes:\n  - id: only\n    type: shell\n    run: echo hi\n"
        )
        errors = validate(wf)
        self.assertTrue(any(e.code == "schema" and "trigger" in e.message for e in errors))


class SchemaAndParamTests(unittest.TestCase):
    """Item [20]: top-level schema, container types and param contracts."""

    def _validate(self, text):
        return validate(load_text(text))

    def test_wrong_container_types_are_flagged(self):
        node_block = "nodes:\n  - id: a\n    type: shell\n    run: echo hi\n"
        cases = {
            "triggers": (
                f"workflow: w\nversion: 1\ntriggers: nope\n{node_block}",
                "key 'triggers' must be a list",
            ),
            "edges": (
                f"workflow: w\nversion: 1\n{node_block}edges: 5\n",
                "key 'edges' must be a list",
            ),
            "policy": (
                f"workflow: w\nversion: 1\n{node_block}policy: 5\n",
                "key 'policy' must be a dict",
            ),
            "nodes": (
                "workflow: w\nversion: 1\nnodes: nope\n",
                "key 'nodes' must be a list",
            ),
            "version": (
                f'workflow: w\nversion: "3"\n{node_block}',
                "key 'version' must be an integer",
            ),
        }
        for label, (doc, needle) in cases.items():
            with self.subTest(label=label):
                errors = self._validate(doc)
                self.assertTrue(
                    any(e.code == "schema" and needle in e.message for e in errors),
                    [str(e) for e in errors],
                )
                self.assertTrue(all(e.line > 0 for e in errors))

    def test_non_string_params_are_type_errors(self):
        doc = "workflow: w\nversion: 1\nnodes:\n  - id: a\n    type: shell\n    run: [1, 2]\n"
        errors = self._validate(doc)
        self.assertTrue(
            any(e.code == "type" and "'run' must be a string" in e.message for e in errors),
            [str(e) for e in errors],
        )

    def test_manual_trigger_must_be_boolean(self):
        doc = (
            "workflow: w\nversion: 1\ntriggers:\n  - manual: /hooks/x\n"
            "nodes:\n  - id: a\n    type: shell\n    run: echo hi\n"
        )
        errors = self._validate(doc)
        self.assertTrue(
            any(e.code == "type" and "trigger 'manual'" in e.message for e in errors),
            [str(e) for e in errors],
        )

    def test_cron_trigger_must_be_non_empty_string(self):
        doc = (
            "workflow: w\nversion: 1\ntriggers:\n  - cron: 5\n"
            "nodes:\n  - id: a\n    type: shell\n    run: echo hi\n"
        )
        errors = self._validate(doc)
        self.assertTrue(
            any(e.code == "type" and "cron" in e.message for e in errors),
            [str(e) for e in errors],
        )

    def test_event_trigger_value_must_be_allowed(self):
        doc = (
            "workflow: w\nversion: 1\ntriggers:\n  - event: mail.arrived\n"
            "nodes:\n  - id: a\n    type: shell\n    run: echo hi\n"
        )
        errors = self._validate(doc)
        self.assertTrue(
            any(e.code == "type" and "mail.arrived" in e.message for e in errors),
            [str(e) for e in errors],
        )

    def test_wrong_param_types_for_gui_nodes(self):
        doc = (
            "workflow: w\nversion: 1\nnodes:\n"
            "  - id: g\n    type: gui_type\n    target: 42\n    text: hello\n"
        )
        errors = self._validate(doc)
        self.assertTrue(
            any(e.code == "type" and "'target' must be a string" in e.message for e in errors),
            [str(e) for e in errors],
        )


class EdgeAndPlaceholderTests(unittest.TestCase):
    """Item [21]: duplicate ids, edge endpoints and placeholder refs."""

    def _validate(self, text):
        return validate(load_text(text))

    def test_duplicate_node_id_is_rejected(self):
        doc = (
            "workflow: w\nversion: 1\nnodes:\n"
            "  - id: a\n    type: shell\n    run: echo one\n"
            "  - id: a\n    type: shell\n    run: echo two\n"
        )
        errors = self._validate(doc)
        dup = [e for e in errors if e.code == "schema" and "duplicate node id 'a'" in e.message]
        self.assertEqual(len(dup), 1, [str(e) for e in errors])
        self.assertGreater(dup[0].line, 0)

    def test_unknown_depends_on_ref_is_an_edge_error(self):
        doc = (
            "workflow: w\nversion: 1\nnodes:\n"
            "  - id: a\n    type: shell\n    run: echo hi\n"
            "    depends_on: [ghost]\n"
        )
        errors = self._validate(doc)
        self.assertTrue(
            any(e.code == "edge" and "ghost" in e.message for e in errors),
            [str(e) for e in errors],
        )

    def test_valid_placeholder_ref_produces_no_error(self):
        doc = (
            "workflow: w\nversion: 1\nnodes:\n"
            "  - id: a\n    type: shell\n    run: echo hi\n"
            "  - id: b\n    type: shell\n    run: echo ${a.out}\n"
            "edges: [[a, b]]\n"
        )
        self.assertEqual(self._validate(doc), [])

    def test_duplicated_edge_is_deduplicated_not_rejected(self):
        doc = (
            "workflow: w\nversion: 1\nnodes:\n"
            "  - id: a\n    type: shell\n    run: echo one\n"
            "  - id: b\n    type: shell\n    run: echo two\n"
            "edges: [[a, b], [a, b]]\n"
        )
        self.assertEqual(self._validate(doc), [])


class TopologicalOrderTests(unittest.TestCase):
    """Item [22]: Kahn's algorithm is correct and deterministic."""

    def _order(self, edges):
        lines = ["workflow: w", "version: 1", "nodes:"]
        for nid in ("a", "b", "c", "d"):
            lines += [f"  - id: {nid}", "    type: shell", f"    run: echo {nid}"]
        lines.append("edges:")
        lines += [f"  - [{src}, {dst}]" for src, dst in edges]
        return topological_order(load_text("\n".join(lines) + "\n"))

    def test_independent_nodes_come_out_lexicographically(self):
        self.assertEqual(self._order([]), ["a", "b", "c", "d"])

    def test_edge_order_does_not_change_the_result(self):
        graph = [("a", "c"), ("a", "b"), ("b", "d"), ("c", "d")]
        forward = self._order(graph)
        backward = self._order(list(reversed(graph)))
        self.assertEqual(forward, backward)
        self.assertEqual(forward, ["a", "b", "c", "d"])

    def test_repeated_runs_are_stable(self):
        graph = [("a", "c"), ("a", "b"), ("b", "d"), ("c", "d")]
        first = self._order(graph)
        for _ in range(5):
            self.assertEqual(self._order(graph), first)

    def test_dependent_nodes_follow_their_parents(self):
        # ready set is always the lexicographic minimum: after c frees b,
        # b (not d) is next even though d was already ready.
        order = self._order([("d", "a"), ("b", "a"), ("c", "b")])
        self.assertEqual(order, ["c", "b", "d", "a"])

    def test_cycle_still_returns_none(self):
        self.assertIsNone(self._order([("a", "b"), ("b", "a")]))


class W4NovalidateProbe(unittest.TestCase):
    """W4 without LLM/bus: manual DSL -> validate -> compile -> triggers."""

    def test_onboarding_demo_end_to_end(self):
        text = (SRC_DIR / "workflows" / "examples" / "onboarding-demo.yaml").read_text(
            encoding="utf-8"
        )
        wf = load_text(text)
        self.assertTrue(is_valid(wf), [str(e) for e in validate(wf)])
        dag = compile_wf(wf)
        self.assertEqual(
            dag["adjacency"], {"setup": ["gate"], "darkmode": [], "gate": ["darkmode"]}
        )
        self.assertEqual(topological_order(wf), ["setup", "gate", "darkmode"])
        kinds = {next(iter(t)) for t in wf.triggers}
        self.assertEqual(kinds, {"cron", "event", "manual"})
        self.assertIn({"event": "file.arrived"}, wf.triggers)


if __name__ == "__main__":
    unittest.main()
