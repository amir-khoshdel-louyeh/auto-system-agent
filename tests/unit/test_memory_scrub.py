"""Phase 7 item [65]: PII and secret scrubbing before persistence.

The scrubber keeps a plan's shape while removing identity and credentials:
placeholders replace matches, findings report rule names and counts (never the
values), and the raw value never survives the call.
"""

import json
import unittest

from memory.scrub import RULES, ScrubResult, contains_pii, scrub, scrub_plan


class ScrubTextTests(unittest.TestCase):
    def test_email_is_replaced(self):
        result = scrub("contact alice.k@example.com about it")
        self.assertNotIn("alice.k@example.com", result.text)
        self.assertIn("[email]", result.text)
        self.assertEqual(result.rules_hit(), ("email",))
        self.assertEqual(result.count, 1)

    def test_credentials_are_replaced(self):
        for secret, example in (
            ("sk-live-0123456789abcdef", "export KEY=sk-live-0123456789abcdef"),
            ("ghp_0123456789abcdefghij", "token ghp_0123456789abcdefghij"),
            # The AWS documentation example key, not a credential.
            ("AKIAIOSFODNN7EXAMPLE", "aws AKIAIOSFODNN7EXAMPLE"),  # secret-scan: allow
        ):
            with self.subTest(secret=secret):
                result = scrub(example)
                self.assertNotIn(secret, result.text)

    def test_password_assignment_is_replaced(self):
        for example in (
            "run setup --password hunter2 now",
            "export DB_PASSWORD=hunter2",
            "password: hunter2",
        ):
            with self.subTest(example=example):
                result = scrub(example)
                self.assertNotIn("hunter2", result.text)
                self.assertIn("[secret]", result.text)

    def test_bearer_token_is_replaced(self):
        result = scrub("Authorization: Bearer abcdefghijklmnopqrstuvwxyz")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", result.text)

    def test_home_paths_lose_the_username(self):
        result = scrub("write to /home/amir/Downloads/report.txt")
        self.assertNotIn("amir", result.text)
        self.assertIn("/home/[user]/Downloads/report.txt", result.text)

    def test_ips_are_replaced(self):
        result = scrub("connect to 192.168.1.24 and 2001:db8:0:0:0:0:0:1")
        self.assertNotIn("192.168.1.24", result.text)
        self.assertIn("[ip]", result.text)

    def test_phone_numbers_are_replaced(self):
        result = scrub("call +1 555 123 4567")
        self.assertNotIn("555 123 4567", result.text)
        self.assertIn("[phone]", result.text)

    def test_clean_text_is_untouched(self):
        result = scrub("mkdir -p demo-app/logs")
        self.assertEqual(result.text, "mkdir -p demo-app/logs")
        self.assertTrue(result.clean)
        self.assertEqual(result.count, 0)
        self.assertEqual(result.as_dict(), {"count": 0, "rules": []})

    def test_findings_report_rules_not_values(self):
        result = scrub("mail bob@example.com and 10.0.0.1")
        self.assertEqual(set(result.rules_hit()), {"email", "ipv4"})
        rendered = json.dumps(result.as_dict())
        self.assertNotIn("bob@example.com", rendered)
        self.assertNotIn("10.0.0.1", rendered)
        self.assertIn("placeholder", result.as_dict()["rules"][0])

    def test_every_rule_is_a_compiled_pattern_with_a_placeholder(self):
        for name, pattern, placeholder in RULES:
            with self.subTest(rule=name):
                self.assertTrue(hasattr(pattern, "search"))
                self.assertIn("[", placeholder)

    def test_placeholders_are_idempotent(self):
        once = scrub("mail bob@example.com").text
        twice = scrub(once)
        self.assertEqual(once, twice.text)
        self.assertTrue(twice.clean)


class ScrubPlanTests(unittest.TestCase):
    def test_plan_strings_are_scrubbed_recursively(self):
        plan = {
            "intent": "run_command",
            "steps": [
                {"id": "notify", "run": "mail -s hi alice@example.com"},
                {"id": "backup", "run": "cp /home/amir/x /tmp/x"},
            ],
            "count": 2,
        }
        scrubbed, result = scrub_plan(plan)
        self.assertEqual(scrubbed["count"], 2)
        self.assertNotIn("alice@example.com", json.dumps(scrubbed))
        self.assertNotIn("amir", json.dumps(scrubbed))
        self.assertEqual(set(result.rules_hit()), {"email", "home_path"})
        self.assertEqual(plan["steps"][1]["run"], "cp /home/amir/x /tmp/x", "input untouched")
        self.assertIn("[user]", scrubbed["steps"][1]["run"])

    def test_contains_pii_detects_leftovers(self):
        self.assertTrue(contains_pii("alice@example.com"))
        self.assertTrue(contains_pii("password=secret1"))
        self.assertFalse(contains_pii("mkdir -p demo-app"))

    def test_scrub_result_is_json_serialisable(self):
        result = scrub("mail bob@example.com")
        self.assertIsInstance(result, ScrubResult)
        self.assertIsInstance(json.dumps(result.as_dict()), str)


if __name__ == "__main__":
    unittest.main()
