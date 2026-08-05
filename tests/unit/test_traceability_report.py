"""Phase 10 item [91]: the traceability mapping must stay true.

The matrix in `docs/TRACEABILITY.md` and the generated report only mean
something if every row points at a module that exists and a test file that
contains tests. These checks run in the suite (fast, no pytest subprocess);
`python scripts/traceability_report.py --check` additionally proves each file
*collects* at least one test and refreshes `docs/reports/traceability.md`.
"""

import unittest

from scripts.traceability_report import (
    ALL,
    FR_IDS,
    FUNCTIONAL,
    NFR_IDS,
    NON_FUNCTIONAL,
    build,
    render,
    validate,
)


def collect(requirement_id: str):
    for requirement in ALL:
        if requirement.id == requirement_id:
            return requirement
    raise AssertionError(f"unknown requirement {requirement_id}")


class MappingTests(unittest.TestCase):
    def test_every_functional_requirement_is_mapped_once(self):
        ids = [requirement.id for requirement in FUNCTIONAL]
        self.assertEqual(ids, list(FR_IDS))

    def test_every_non_functional_area_is_mapped_once(self):
        ids = [requirement.id for requirement in NON_FUNCTIONAL]
        self.assertEqual(sorted(ids), sorted(NFR_IDS))

    def test_every_row_cites_existing_evidence(self):
        self.assertEqual(validate(), [], "the mapping points at missing evidence")

    def test_every_row_has_modules_and_tests_or_an_explicit_note(self):
        for requirement in ALL:
            with self.subTest(requirement=requirement.id):
                self.assertTrue(
                    requirement.modules or requirement.tests or requirement.note,
                    "a row with no evidence is not traceability",
                )

    def test_the_risky_requirements_cite_real_tests(self):
        # Spot-check the requirements the defense leans on hardest.
        for requirement_id, expected in (
            ("FR-07", "tests/unit/test_policy_adversarial.py"),
            ("FR-08", "tests/integration/test_approvals_api.py"),
            ("FR-12", "tests/unit/test_audit_chain.py"),
            ("FR-13", "tests/integration/test_replay.py"),
            ("FR-15", "tests/acceptance/test_memory_reuse_gate.py"),
        ):
            with self.subTest(requirement=requirement_id):
                self.assertIn(expected, collect(requirement_id).tests)

    def test_the_pending_area_is_marked_pending(self):
        self.assertTrue(collect("Usability").pending)
        self.assertEqual(
            [requirement.id for requirement in ALL if requirement.pending], ["Usability"]
        )


class ValidationTests(unittest.TestCase):
    def test_a_row_with_a_missing_test_file_is_reported(self):
        from dataclasses import replace

        broken = (replace(collect("FR-12"), tests=("tests/unit/test_does_not_exist.py",)),)
        problems = validate(broken)
        self.assertTrue(any("missing test file" in problem for problem in problems))

    def test_a_row_with_no_evidence_is_reported(self):
        from dataclasses import replace

        empty = (replace(collect("FR-12"), modules=(), tests=(), note=""),)
        self.assertTrue(any("no module, test or note" in problem for problem in validate(empty)))

    def test_a_dropped_requirement_is_reported(self):
        without_fr04 = tuple(requirement for requirement in ALL if requirement.id != "FR-04")
        self.assertTrue(any("FR-04" in problem for problem in validate(without_fr04)))


class RenderTests(unittest.TestCase):
    def test_the_report_lists_every_requirement_and_the_gaps(self):
        report = build(with_counts=False)
        text = render(report)
        for requirement in ALL:
            with self.subTest(requirement=requirement.id):
                self.assertIn(requirement.id, text)
        self.assertIn("## 3. Honest gaps", text)
        self.assertIn("Usability study not run", text)
        self.assertIn("docs/USABILITY_STUDY.md", text)
        self.assertIn("All cited modules and test files exist", text)


if __name__ == "__main__":
    unittest.main()
