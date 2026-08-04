#!/usr/bin/env python3
"""Phase 9 item [82]: scan tracked files for credentials before they ship.

High-signal formats only (private keys, cloud and provider tokens, JWTs), so
the scanner stays quiet enough to run on every push and CI run. Two scopes:

- ``all``  — applies to every text file, including ``tests/``: a real cloud
  key in a fixture is still a leak;
- ``app``  — applies only to non-test paths: the generic
  ``secret_key = "<24+ chars>"`` shape would otherwise match the deliberate
  fixture passwords every integration test needs.

A hit is suppressed by an explicit review marker on the line
(``# secret-scan: allow``) or by containing a known non-secret placeholder
(``insecure-secret-key-change-this``, ``example``, ``placeholder``, …), so
suppression is visible in review rather than hidden in a config file.

Usage::

    python scripts/secret_scan.py            # scan the repository
    python scripts/secret_scan.py PATH ...   # scan specific files/directories

Exits 1 when anything is found. The matched text is never printed in full —
only a masked excerpt and its line — so a run cannot itself leak the secret.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Inline, reviewable suppression marker.
ALLOW_MARKER = "secret-scan: allow"

#: Values that are deliberately not secrets (dev defaults and placeholders).
ALLOW_SUBSTRINGS = (
    "insecure-secret-key-change-this",
    "change-me",
    "changeme",
    "placeholder",
    "redacted",
    "example",
    "[token]",
)

SKIP_DIRS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "data",
        "htmlcov",
        "node_modules",
        "source_MIT",  # read-only reference material, never shipped
    }
)

SKIP_SUFFIXES = frozenset(
    {
        ".db",
        ".gz",
        ".ico",
        ".jpeg",
        ".jpg",
        ".pdf",
        ".png",
        ".pyc",
        ".so",
        ".sqlite",
        ".whl",
        ".zip",
    }
)


@dataclass(frozen=True)
class Rule:
    """One detection pattern and where it applies."""

    name: str
    pattern: re.Pattern[str]
    scope: str = "all"  # "all" | "app"


@dataclass(frozen=True)
class Finding:
    """One match: where it is, which rule fired, and a masked excerpt."""

    path: str
    line: int
    rule: str
    excerpt: str


RULES: tuple[Rule, ...] = (
    Rule(
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ),
    Rule(
        "aws_access_key",
        re.compile(r"\b(?:AKIA|ASIA|A3T[A-Z0-9])[A-Z0-9]{16}\b"),
    ),
    Rule(
        "github_token",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    ),
    Rule(
        "slack_token",
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{12,}\b"),
    ),
    Rule(
        "google_api_key",
        re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ),
    Rule(
        "stripe_secret",
        re.compile(r"\bsk_live_[A-Za-z0-9]{20,}\b"),
    ),
    Rule(
        "openai_key",
        re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"),
    ),
    Rule(
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{6,}\b"),
    ),
    Rule(
        "secret_assignment",
        re.compile(
            r"(?i)\b(?:secret_key|api_secret|access_key|client_secret)\b\s*[:=]\s*"
            r"[\"'][A-Za-z0-9+/=_-]{24,}[\"']"
        ),
        scope="app",
    ),
)


def is_test_path(path: str) -> bool:
    """True for the test tree (fixtures live there legitimately)."""
    return path.startswith("tests/") or "/tests/" in path


def appliable_rules(path: str) -> tuple[Rule, ...]:
    """Rules that apply to ``path`` (the generic-assignment rule skips tests)."""
    if is_test_path(path):
        return tuple(rule for rule in RULES if rule.scope == "all")
    return RULES


def masked(match: str) -> str:
    """A short, non-reversible excerpt of a hit."""
    head = match[:4]
    return f"{head}…[{len(match)} chars]"


def line_is_allowed(line: str) -> bool:
    if ALLOW_MARKER in line:
        return True
    return any(marker in line for marker in ALLOW_SUBSTRINGS)


def iter_files(root: Path = ROOT, paths: list[str] | None = None) -> list[Path]:
    """Tracked text files (``git ls-files``), or an explicit list of paths."""
    if paths:
        found: list[Path] = []
        for raw in paths:
            candidate = Path(raw)
            candidate = candidate if candidate.is_absolute() else root / raw
            if candidate.is_dir():
                found.extend(sorted(p for p in candidate.rglob("*") if p.is_file()))
            elif candidate.is_file():
                found.append(candidate)
        return [p for p in found if not _skipped(p, root)]

    try:
        listing = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):  # not a git checkout
        return [
            path for path in sorted(root.rglob("*")) if path.is_file() and not _skipped(path, root)
        ]
    files = [root / name for name in listing.split("\0") if name]
    return [path for path in files if path.is_file() and not _skipped(path, root)]


def _skipped(path: Path, root: Path) -> bool:
    if path.suffix.lower() in SKIP_SUFFIXES:
        return True
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    return any(part in SKIP_DIRS for part in relative.parts)


def scan_text(text: str, path: str, *, rules: tuple[Rule, ...] | None = None) -> list[Finding]:
    """Findings in one document; ``path`` is used for scope and reporting."""
    active = rules if rules is not None else appliable_rules(path)
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line_is_allowed(line):
            continue
        for rule in active:
            match = rule.pattern.search(line)
            if match:
                findings.append(
                    Finding(path=path, line=number, rule=rule.name, excerpt=masked(match.group(0)))
                )
                break
    return findings


def scan(root: Path = ROOT, paths: list[str] | None = None) -> list[Finding]:
    """Scan every tracked file under ``root`` (or the given paths)."""
    findings: list[Finding] = []
    for path in iter_files(root, paths):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            relative = path.relative_to(root) if path.is_absolute() else path
        except ValueError:  # an explicit path outside the repository
            relative = path
        findings.extend(scan_text(text, str(relative)))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="scan tracked files for credentials")
    parser.add_argument("paths", nargs="*", help="files or directories (default: repository)")
    args = parser.parse_args(argv)

    findings = scan(paths=args.paths or None)
    if not findings:
        print(
            f"secret scan: clean ({len(iter_files(paths=args.paths or None))} files, {len(RULES)} rules)"
        )
        return 0
    for finding in findings:
        print(f"{finding.path}:{finding.line}: {finding.rule}: {finding.excerpt}")
    print(f"secret scan: {len(findings)} finding(s)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
