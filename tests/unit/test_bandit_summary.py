"""Phase 10 item [93]: the security summary must count what Bandit reported."""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.bandit_summary import main, read_csv, summarise

CSV = """filename,test_name,test_id,issue_severity,issue_confidence,issue_cwe,issue_text,line_number,col_offset,end_col_offset,line_range,more_info\r
src/sandbox/runner.py,hardcoded_tmp_directory,B108,LOW,MEDIUM,CWE-377,Usage of /tmp,50,16,22,[50],https://example.invalid/b108\r
src/app/limits.py,try_except_continue,B112,LOW,LOW,CWE-703,Try except continue,10,1,5,[10],https://example.invalid/b112\r
src/legacy/run.py,subprocess_popen_with_shell_equals_true,B602,HIGH,HIGH,CWE-78,shell=True,77,1,9,[77],https://example.invalid/b602\r
"""


class SummaryTests(unittest.TestCase):
    def write(self, text: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "bandit.csv"
        path.write_text(text, encoding="utf-8")
        return path

    def test_counts_match_the_rows(self):
        summary = summarise(read_csv(self.write(CSV)))
        self.assertEqual(summary["findings"], 3)
        self.assertEqual(summary["by_severity"], {"HIGH": 1, "LOW": 2})
        self.assertEqual(summary["medium_or_higher"], 1)
        self.assertEqual(summary["results"][2]["test_id"], "B602")
        self.assertEqual(summary["results"][2]["line"], 77)

    def test_log_lines_and_duplicates_are_not_findings(self):
        noisy = (
            CSV
            + "tests: None\n"
            + "[main]\tINFO\trunning on Python 3.14.8\n"
            + "[tester]\tWARNING\tnosec encountered (B108), but no failed test on "
            + "file src/sandbox/runner.py:50\n"
            + CSV.splitlines()[1]
            + "\n"  # the same B108 finding, printed twice
        )
        summary = summarise(read_csv(self.write(noisy)))
        self.assertEqual(summary["findings"], 3)
        self.assertEqual(summary["by_severity"], {"HIGH": 1, "LOW": 2})
        for finding in summary["results"]:
            self.assertTrue(finding["test_id"].startswith("B"))
            self.assertTrue(finding["file"].startswith("src/"))

    def test_an_empty_report_is_a_clean_summary(self):
        summary = summarise(read_csv(self.write("")))
        self.assertEqual(summary["findings"], 0)
        self.assertEqual(summary["medium_or_higher"], 0)
        self.assertEqual(summary["results"], [])

    def test_the_summary_states_the_threshold_it_counts(self):
        summary = summarise(read_csv(self.write(CSV)))
        self.assertIn("-ll -ii", summary["threshold"])

    def test_the_summary_omits_the_source_snippet(self):
        summary = summarise(read_csv(self.write(CSV)))
        for finding in summary["results"]:
            self.assertNotIn("code", finding)
            self.assertTrue(finding["file"])

    def test_cli_writes_valid_json(self):
        target = Path(tempfile.mkdtemp()) / "nested" / "summary.json"
        self.assertEqual(main([str(self.write(CSV)), str(target)]), 0)
        payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(payload["findings"], 3)

    def test_cli_reports_a_missing_file(self):
        self.assertEqual(main(["/nonexistent/bandit.csv", "/tmp/x.json"]), 2)


if __name__ == "__main__":
    unittest.main()
