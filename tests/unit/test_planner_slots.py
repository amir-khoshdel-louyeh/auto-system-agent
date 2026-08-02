"""Phase 6 item [58]: deterministic slot extraction.

Extraction keeps the user's spelling for free-text values, reads the closed
vocabularies (metrics, apps) from the normalized form, and simply omits what
it cannot determine so the planner can ask instead of inventing.
"""

import unittest

from orchestration.dataset import load_examples
from orchestration.rules import classify
from orchestration.slots import extract_slots


def slots_for(text: str) -> dict:
    match = classify(text)
    intent = match.intent if match else "unknown"
    return extract_slots(intent, text)


class FolderAndWorkspaceTests(unittest.TestCase):
    def test_mkdir_and_keyword_lists(self):
        self.assertEqual(slots_for("mkdir reports")["folder_names"], ["reports"])
        self.assertEqual(
            slots_for("create three folders called reports, logs and backups")["folder_names"],
            ["reports", "logs", "backups"],
        )
        self.assertEqual(
            slots_for("set up folders: data, models, notebooks")["folder_names"],
            ["data", "models", "notebooks"],
        )

    def test_count_words_and_articles_are_not_names(self):
        self.assertEqual(
            slots_for("create a new folder named scratch")["folder_names"], ["scratch"]
        )
        self.assertEqual(
            slots_for("create a workspace called demo with three folders and a README").get(
                "folder_names", []
            ),
            [],
            "a trailing 'and a README' must not become a folder name",
        )

    def test_workspace_name_patterns(self):
        cases = {
            "create a workspace called demo-app with three folders": "demo-app",
            "set up the alpha workspace": "alpha",
            "scaffold a new workspace named project-x": "project-x",
            "create a workspace for the team called hub": "hub",
            "set up project workspace cosmic with two folders": "cosmic",
            "create the workspace onboarding-demo": "onboarding-demo",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(slots_for(text)["workspace_name"], expected)

    def test_workspace_folders_are_extracted_when_named(self):
        self.assertEqual(
            slots_for("scaffold workspace data-lab with folders data and models")["folder_names"],
            ["data", "models"],
        )


class FileAndAppTests(unittest.TestCase):
    def test_path_and_content_keep_original_spelling(self):
        slots = slots_for("generate a README.md with the title Team Automation Hub")
        self.assertEqual(slots["path"], "README.md")
        self.assertEqual(slots["content"], "Team Automation Hub")

    def test_quoted_content_wins(self):
        slots = slots_for("put 'hello' into demo-app/README.md")
        self.assertEqual(slots["path"], "demo-app/README.md")
        self.assertEqual(slots["content"], "hello")

    def test_inline_content_before_the_target(self):
        slots = slots_for("write hello world into greeting.txt")
        self.assertEqual(slots["path"], "greeting.txt")
        self.assertEqual(slots["content"], "hello world")

    def test_message_marker_and_trailing_target(self):
        slots = slots_for("save the message done to status.txt")
        self.assertEqual(slots["content"], "done")

    def test_content_marker_variants(self):
        self.assertEqual(
            slots_for("create notes.md containing meeting at ten")["content"], "meeting at ten"
        )
        self.assertEqual(
            slots_for("write the file report.txt with content summary of the run")["content"],
            "summary of the run",
        )

    def test_app_name_patterns(self):
        cases = {
            "install vlc": "vlc",
            "please install the app thunderbird": "thunderbird",
            "I need htop installed": "htop",
            "get me the app chromium": "chromium",
            "install vlc on this machine": "vlc",
            "I would like to install docker": "docker",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(slots_for(text)["app"], expected)


class CommandMetricDirectoryTests(unittest.TestCase):
    def test_command_verb_and_prefixes_are_stripped(self):
        cases = {
            "run ls -la": "ls -la",
            "execute: df -h": "df -h",
            "please execute the command git branch": "git branch",
            "run the command pwd": "pwd",
            "run 'whoami'": "whoami",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(slots_for(text)["command"], expected)

    def test_metric_vocabulary(self):
        cases = {
            "how much disk space is free": "disk",
            "show me the memory usage": "memory",
            "how many cores does this machine have": "cpu",
            "what is the load average": "load",
            "show the running processes": "processes",
            "when did this machine last reboot": "uptime",
            "which processes use the most cpu": "processes",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(slots_for(text)["metric"], expected)

    def test_directory_patterns(self):
        cases = {
            "list the files in ~/Downloads": "~/Downloads",
            "ls -la ~": "~",
            "list directory /var/log": "/var/log",
            "show what is in the reports folder": "reports",
            "list the contents of logs": "logs",
            "display the files in the archive directory": "archive",
            "list the working directory": ".",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(slots_for(text)["path"], expected)

    def test_unsupported_intents_have_no_slots(self):
        self.assertEqual(slots_for("tell me a joke"), {})
        self.assertEqual(slots_for("help"), {})


class DatasetSlotTests(unittest.TestCase):
    """The labeled corpus is the extractor's held-out evidence (item [63])."""

    def _accuracy(self, split: str) -> tuple[float, list[str]]:
        total = hit = 0
        misses: list[str] = []
        for example in load_examples(split=split):
            match = classify(example.text)
            intent = match.intent if match else "unknown"
            extracted = extract_slots(intent, example.text)
            for key, want in example.slots.items():
                total += 1
                have = extracted.get(key)
                if isinstance(want, list):
                    ok = [str(item).lower() for item in (have or [])] == [
                        str(item).lower() for item in want
                    ]
                else:
                    ok = str(have or "").lower() == str(want).lower()
                hit += ok
                if not ok:
                    misses.append(f"{example.text!r}.{key}: want {want!r}, got {have!r}")
        return (hit / total if total else 1.0), misses

    def test_train_slots_are_extracted(self):
        accuracy, misses = self._accuracy("train")
        self.assertGreaterEqual(accuracy, 0.98, misses)

    def test_held_out_slots_are_extracted(self):
        accuracy, misses = self._accuracy("test")
        self.assertGreaterEqual(accuracy, 0.90, misses)


if __name__ == "__main__":
    unittest.main()
