"""Phase 9 item [81]: the threat model must cite code that exists.

A threat model rots the moment its evidence moves. This test parses every
backticked repository path in `docs/THREAT_MODEL.md`, requires the file to
exist, and — when the citation names a symbol (`path::symbol`) — requires
that symbol to appear in the file. It also checks the model is complete
enough to review: every STRIDE letter and every trust boundary is present,
and no row is left as a TODO.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "THREAT_MODEL.md"

#: Backticked tokens that are repository paths rather than prose/commands.
PATH_PREFIXES = ("src/", "tests/", "scripts/", "docs/", ".github/")
PATH_FILES = ("compose.yml", "Dockerfile.api")

CITATION = re.compile(r"`([^`]+)`")
STRIDE_LETTERS = ("**S**", "**T**", "**R**", "**I**", "**D**", "**E**")


class ThreatModelDocTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")
        cls.lines = cls.text.splitlines()

    def citations(self) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        for token in CITATION.findall(self.text):
            candidate = token.split()[0] if token else ""
            if "::" in candidate:
                path, symbol = candidate.split("::", 1)
                if path.startswith(PATH_PREFIXES) or path in PATH_FILES:
                    found.append((path, symbol))
                continue
            if candidate.startswith(PATH_PREFIXES) or candidate in PATH_FILES:
                found.append((candidate, ""))
        return found

    def test_the_document_exists(self):
        self.assertTrue(DOC.is_file(), f"missing {DOC}")

    def test_every_cited_path_exists(self):
        missing = [path for path, _ in self.citations() if not (ROOT / path).exists()]
        self.assertEqual(missing, [], f"threat model cites missing paths: {missing}")

    def test_every_cited_symbol_is_in_its_file(self):
        wrong: list[str] = []
        for path, symbol in self.citations():
            if not symbol:
                continue
            target = ROOT / path
            if not target.exists():
                continue  # reported by the existence test
            if symbol.split("(")[0].strip() not in target.read_text(encoding="utf-8"):
                wrong.append(f"{path}::{symbol}")
        self.assertEqual(wrong, [], f"cited symbols not found: {wrong}")

    def test_all_stride_letters_are_analysed(self):
        for letter in STRIDE_LETTERS:
            with self.subTest(letter=letter):
                self.assertGreaterEqual(self.text.count(letter), 4, f"{letter} barely analysed")

    def test_every_trust_boundary_is_covered(self):
        for boundary in ("B1", "B2", "B3", "B4", "B5"):
            with self.subTest(boundary=boundary):
                self.assertIn(f"### 3.{boundary[-1]}", self.text)

    def test_residual_risks_are_listed_with_a_compensating_control(self):
        section = self.text.split("## 5. Residual risks")[1].split("## 6.")[0]
        rows = [
            line for line in section.splitlines() if line.startswith("| ") and "---" not in line
        ]
        self.assertGreaterEqual(len(rows) - 1, 5, "residual risks need a real table")
        for row in rows[1:]:
            cells = [cell.strip() for cell in row.strip("|").split("|")]
            self.assertEqual(len(cells), 4, f"row needs risk/why/control/owner action: {row}")
            self.assertTrue(all(cells), f"empty cell in residual row: {row}")

    def test_no_row_is_left_as_a_placeholder(self):
        for line in self.lines:
            if line.startswith("|"):
                self.assertNotIn("TODO", line)
                self.assertNotIn("TBD", line)
                self.assertNotIn("N/A", line)


if __name__ == "__main__":
    unittest.main()
