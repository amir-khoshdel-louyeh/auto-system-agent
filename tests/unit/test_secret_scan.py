"""Phase 9 item [82]: the secret scanner itself and the repo it guards.

The scanner is only trustworthy if it fires on real-looking credentials,
respects an inline review marker, keeps the generic `secret_key = "…"` rule
out of the fixture-heavy test tree, and — the case that matters on every run
— reports the checked-in repository as clean. The fixtures below exercise a
planted key; the repository scan is the real assertion.
"""

import tempfile
import unittest
from pathlib import Path

from scripts.secret_scan import (
    ALLOW_MARKER,
    RULES,
    appliable_rules,
    iter_files,
    line_is_allowed,
    main,
    masked,
    scan,
    scan_text,
)

#: Deliberately planted, well-known non-secret examples.
AWS_EXAMPLE = "AKIA" + "IOSFODNN7EXAMPLE"
GITHUB_EXAMPLE = "ghp_" + "0" * 36
JWT_EXAMPLE = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhZG1pbiJ9.c2lnbmF0dXJl"  # secret-scan: allow - dummy JWT fixture
PRIVATE_KEY_EXAMPLE = (
    "-----BEGIN RSA PRIVATE KEY-----"  # secret-scan: allow - header only, no key body
)


class RuleTests(unittest.TestCase):
    def names(self, text: str, path: str = "src/app/mod.py") -> list[str]:
        return [finding.rule for finding in scan_text(text, path)]

    def test_each_high_signal_rule_fires(self):
        self.assertIn("aws_access_key", self.names(f"key = {AWS_EXAMPLE}"))
        self.assertIn("github_token", self.names(f"token = {GITHUB_EXAMPLE}"))
        self.assertIn("jwt", self.names(f"Bearer {JWT_EXAMPLE}"))
        self.assertIn("private_key", self.names(PRIVATE_KEY_EXAMPLE))
        self.assertIn("stripe_secret", self.names('key = "sk_live_' + "a" * 24 + '"'))
        self.assertIn("google_api_key", self.names("key = AIza" + "a" * 35))

    def test_line_numbers_point_at_the_hit(self):
        findings = scan_text(f"safe = 1\nkey = {AWS_EXAMPLE}\n", "src/app/mod.py")
        self.assertEqual(findings[0].line, 2)

    def test_excerpt_never_reveals_the_whole_secret(self):
        excerpt = masked(AWS_EXAMPLE)
        self.assertNotIn(AWS_EXAMPLE, excerpt)
        self.assertTrue(excerpt.startswith("AKIA"))

    def test_evidence_is_reported_once_per_line(self):
        findings = scan_text(f"key = {AWS_EXAMPLE} and {GITHUB_EXAMPLE}", "src/app/mod.py")
        self.assertEqual(len(findings), 1)

    def test_placeholder_values_are_not_flagged(self):
        self.assertEqual(self.names("secret_key = 'insecure-secret-key-change-this'"), [])

    def test_inline_review_marker_suppresses_a_hit(self):
        line = f"key = {AWS_EXAMPLE}  # {ALLOW_MARKER} - documented example"
        self.assertEqual(scan_text(line, "src/app/mod.py"), [])
        self.assertTrue(line_is_allowed(line))

    def test_generic_assignment_rule_skips_fixtures_but_not_source(self):
        line = 'secret_key = "rate-limit-secret-0123456789abcd"'
        self.assertEqual(
            [f.rule for f in scan_text(line, "tests/integration/test_api.py")],
            [],
        )
        self.assertIn("secret_assignment", [f.rule for f in scan_text(line, "src/app/mod.py")])

    def test_scope_helper_keeps_high_signal_rules_everywhere(self):
        test_rules = {rule.name for rule in appliable_rules("tests/unit/test_x.py")}
        app_rules = {rule.name for rule in appliable_rules("src/app/x.py")}
        self.assertIn("aws_access_key", test_rules)
        self.assertNotIn("secret_assignment", test_rules)
        self.assertEqual(app_rules, {rule.name for rule in RULES})


class RepositoryTests(unittest.TestCase):
    def test_the_repository_has_no_findings(self):
        findings = scan()
        self.assertEqual(
            findings,
            [],
            "checked-in secrets found: "
            + ", ".join(f"{f.path}:{f.line} ({f.rule})" for f in findings),
        )

    def test_reference_material_is_not_scanned(self):
        self.assertNotIn("source_MIT", " ".join(str(path) for path in iter_files()))

    def test_planted_secret_in_a_temporary_tree_is_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.py").write_text(f"KEY = '{AWS_EXAMPLE}'\n", encoding="utf-8")
            findings = scan(root=root)
        self.assertEqual([finding.rule for finding in findings], ["aws_access_key"])

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            clean = Path(tmp) / "clean.py"
            clean.write_text("VALUE = 1\n", encoding="utf-8")
            self.assertEqual(main([str(clean)]), 0)
            dirty = Path(tmp) / "dirty.py"
            dirty.write_text(f"KEY = '{AWS_EXAMPLE}'\n", encoding="utf-8")
            self.assertEqual(main([str(dirty)]), 1)


if __name__ == "__main__":
    unittest.main()
