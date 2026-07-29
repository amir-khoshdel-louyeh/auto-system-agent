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
