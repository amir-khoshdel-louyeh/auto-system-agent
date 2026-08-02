"""Phase 6 item [57]: normalization and high-precision intent rules.

The rules must classify the labeled dataset's supported utterances without a
model or a network call, and must return None for requests that are not
automation at all (they fall through to help/unknown or the LLM adapter).
"""

import unittest

from orchestration.dataset import load_examples
from orchestration.normalize import normalize, prepare, strip_filler, tokens
from orchestration.rules import RULES, classify, match_normalized


class NormalizeTests(unittest.TestCase):
    def test_case_whitespace_and_punctuation_are_normalized(self):
        self.assertEqual(normalize("  Install   VLC!  "), "install vlc")

    def test_curly_quotes_become_straight(self):
        self.assertEqual(prepare("put ‘ok’ into “notes.md”"), "put 'ok' into \"notes.md\"")

    def test_prepare_preserves_case_for_slot_values(self):
        self.assertEqual(
            prepare("write the title Team Automation Hub"), "write the title Team Automation Hub"
        )

    def test_filler_is_removed_once(self):
        self.assertEqual(strip_filler("Please can you install vlc"), "install vlc")
        self.assertEqual(strip_filler("i would like to install docker"), "install docker")

    def test_double_politeness_is_removed_repeatedly(self):
        self.assertEqual(strip_filler("Hey, please make a folder"), "make a folder")

    def test_single_word_filler_becomes_empty(self):
        self.assertEqual(strip_filler("please"), "")

    def test_trailing_punctuation_is_trimmed(self):
        self.assertEqual(normalize("What is the uptime?"), "what is the uptime")

    def test_tokens_keep_order(self):
        self.assertEqual(tokens("Create  three folders"), ["Create", "three", "folders"])


class RuleTests(unittest.TestCase):
    def test_mkdir_is_a_folder_request(self):
        self.assertEqual(classify("mkdir reports").intent, "create_folder")
        self.assertEqual(classify("mkdir reports").confidence, 0.95)

    def test_run_verb_wins_over_other_words(self):
        # "run ls -la" must stay a command even though "ls" is a list verb.
        self.assertEqual(classify("run ls -la").intent, "run_command")
        self.assertEqual(classify("please execute: df -h").intent, "run_command")

    def test_workspace_beats_folder_words(self):
        match = classify("create a workspace called demo-app with three folders and a README")
        self.assertEqual(match.intent, "create_workspace")

    def test_exact_help_beats_everything(self):
        self.assertEqual(classify("what can you do").intent, "help")
        self.assertEqual(classify("Help!").intent, "help")

    def test_listing_beats_system_metric(self):
        self.assertEqual(classify("show the contents of ~/Downloads").intent, "list_directory")
        self.assertEqual(classify("list the files in /var/log").intent, "list_directory")

    def test_metric_requests_map_to_system_info(self):
        for text in (
            "how much disk space is free",
            "show me the memory usage",
            "what is the load average",
            "show the running processes",
            "when did this machine last reboot",
        ):
            with self.subTest(text=text):
                self.assertEqual(classify(text).intent, "system_info")

    def test_non_automation_requests_have_no_rule(self):
        for text in (
            "tell me a joke",
            "who wrote hamlet",
            "explain quantum computing",
            "what is 2+2",
        ):
            with self.subTest(text=text):
                self.assertIsNone(classify(text))

    def test_every_rule_has_a_reason_and_confidence(self):
        names = [rule.name for rule in RULES]
        self.assertEqual(len(names), len(set(names)), "rule names must be unique")
        for rule in RULES:
            with self.subTest(rule=rule.name):
                self.assertGreater(rule.confidence, 0.5)
                self.assertLessEqual(rule.confidence, 1.0)

    def test_match_normalized_accepts_pre_normalized_text(self):
        self.assertEqual(match_normalized("mkdir reports").intent, "create_folder")
        self.assertIsNone(match_normalized("banana"))


class DatasetClassificationTests(unittest.TestCase):
    """The corpus is the rule set's acceptance test (item [57] evidence)."""

    def _accuracy(self, split: str) -> float:
        examples = load_examples(split=split)
        correct = 0
        for example in examples:
            match = classify(example.text)
            predicted = match.intent if match else "unknown"
            correct += predicted == example.intent
        return correct / len(examples)

    def test_train_split_is_classified_accurately(self):
        accuracy = self._accuracy("train")
        self.assertGreaterEqual(accuracy, 0.98, f"train accuracy {accuracy:.3f}")

    def test_held_out_split_is_classified_accurately(self):
        accuracy = self._accuracy("test")
        self.assertGreaterEqual(accuracy, 0.90, f"test accuracy {accuracy:.3f}")

    def test_unsupported_requests_are_not_forced_into_an_intent(self):
        for example in load_examples():
            if example.intent != "unknown":
                continue
            with self.subTest(text=example.text):
                self.assertIsNone(classify(example.text))


if __name__ == "__main__":
    unittest.main()
