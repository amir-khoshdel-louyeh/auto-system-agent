"""DSL + validator fixtures + W4-novalidate (proposal phase 2, items 18-24)."""

import unittest
from pathlib import Path

from engine.dsl.compiler import compile as compile_wf  # noqa: E402
from engine.dsl.loader import load_text  # noqa: E402
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
        self.assertEqual(kinds, {"cron", "event", "webhook"})
        self.assertIn({"event": "file.arrived"}, wf.triggers)


if __name__ == "__main__":
    unittest.main()
