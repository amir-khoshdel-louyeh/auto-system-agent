#!/usr/bin/env python3
"""Phase 10 item [93]: turn Bandit's CSV output into a valid JSON summary.

Bandit 1.9.4's `-f json` output is not always parseable (its `code` field can
emit raw control characters), and a machine-readable security artefact that a
tool cannot read is worse than none. Its `-f csv` output escapes correctly, so
the export step uses CSV and this script builds the JSON the SE package
includes — counts by severity/confidence plus one row per finding, without the
source snippet (the path, line and test id are enough to re-run Bandit).

Usage::

    bandit -r src -ll -ii -f csv > bandit.csv 2>/dev/null
    python scripts/bandit_summary.py bandit.csv bandit-summary.json

The summary records the invocation it summarises (``threshold``), because
"0 findings" is only meaningful next to the filter that produced it.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def _is_finding(row: dict[str, str]) -> bool:
    """True only for a real Bandit result row.

    Bandit logs to the same stream the report is written to (``[main] INFO``,
    ``[tester] WARNING nosec ...``, the progress bar), so a CSV captured with
    ``2>&1`` starts with log lines that a naive reader would count as findings.
    A real row always carries a Bandit test id (``B<digits>``) and a filename;
    anything else is noise and is dropped rather than reported.
    """
    test_id = row.get("test_id", "")
    return bool(row.get("filename")) and (
        len(test_id) > 1 and test_id[0] == "B" and test_id[1:].isdigit()
    )


def read_csv(path: str | Path) -> list[dict[str, str]]:
    """Rows of Bandit's CSV report (empty list for an empty report).

    Bandit mixes non-CSV lines into the stream the report is written to
    (``Working... 100%``, ``[main] INFO ...``, ``[tester] WARNING nosec ...``),
    so the header row is located first and every row that is not a real finding
    is skipped (see :func:`_is_finding`). Duplicates are collapsed, so the
    counts match what Bandit found rather than how often it printed it.
    """
    with Path(path).open(newline="", encoding="utf-8") as handle:
        table = [row for row in csv.reader(handle)]

    header: list[str] = []
    rows: list[dict[str, str]] = []
    for row in table:
        cells = [(cell or "").strip() for cell in row]
        if not header:
            if cells[:1] == ["filename"]:
                header = cells
            continue
        # ``strict=False``: a short row (a log line that happens to contain a
        # comma) is kept with missing keys and dropped by:func:`_is_finding`.
        rows.append(dict(zip(header, cells, strict=False)))

    findings: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        if not _is_finding(row):
            continue
        key = (row.get("filename", ""), row.get("line_number", ""), row.get("test_id", ""))
        if key in seen:
            continue
        seen.add(key)
        findings.append(row)
    return findings


#: The gate the export step runs Bandit with; recorded in the summary so
#: "0 findings" can never be read as "Bandit found nothing at any level".
DEFAULT_THRESHOLD = "bandit -r src -ll -ii (severity >= MEDIUM, confidence >= MEDIUM)"


def summarise(
    rows: list[dict[str, str]], *, tool: str = "bandit", threshold: str = DEFAULT_THRESHOLD
) -> dict[str, Any]:
    """Severity/confidence counts plus a trimmed finding list."""
    severity = Counter(row.get("issue_severity", "UNKNOWN") for row in rows)
    confidence = Counter(row.get("issue_confidence", "UNKNOWN") for row in rows)
    findings = [
        {
            "file": row.get("filename", ""),
            "line": int(row.get("line_number") or 0),
            "test_id": row.get("test_id", ""),
            "test_name": row.get("test_name", ""),
            "severity": row.get("issue_severity", ""),
            "confidence": row.get("issue_confidence", ""),
            "issue": row.get("issue_text", ""),
        }
        for row in rows
    ]
    return {
        "tool": tool,
        "threshold": threshold,
        "findings": len(findings),
        "by_severity": dict(sorted(severity.items())),
        "by_confidence": dict(sorted(confidence.items())),
        "medium_or_higher": sum(
            count for level, count in severity.items() if level in {"MEDIUM", "HIGH"}
        ),
        "results": findings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bandit CSV -> JSON summary")
    parser.add_argument("csv_path", help="Bandit CSV report (-f csv -o ...)")
    parser.add_argument("json_path", help="where to write the summary")
    args = parser.parse_args(argv)

    try:
        rows = read_csv(args.csv_path)
    except OSError as exc:
        print(f"error: cannot read {args.csv_path}: {exc}", file=sys.stderr)
        return 2
    summary = summarise(rows)
    target = Path(args.json_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(
        f"bandit summary written to {target}: {summary['findings']} finding(s),"
        f" {summary['medium_or_higher']} medium or higher ({summary['threshold']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
