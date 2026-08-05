#!/usr/bin/env python3
"""Phase 10 item [90]: validate every Mermaid diagram in the documentation.

A diagram that does not parse is worse than no diagram: it renders as an
error box in the review and hides the architecture it was meant to explain.
This script extracts every ```mermaid block from the Markdown files, renders
it with the Mermaid CLI when that is available, and fails otherwise.

`mmdc` comes from Node (`npx -y @mermaid-js/mermaid-cli`) and needs a
Chromium/Chrome binary, so it is a *documentation* check rather than a test
suite dependency: without Node the script reports `SKIP` and exits 0, and with
`--strict` it exits 2 instead, which is what the export step uses.

Usage::

    python scripts/validate_diagrams.py                # docs/*.md
    python scripts/validate_diagrams.py --strict       # fail when mmdc is missing
    python scripts/validate_diagrams.py docs/A.md      # specific files
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLOCK = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)


def docs(paths: list[str] | None = None) -> list[Path]:
    """Markdown files to scan (default: everything under docs/)."""
    if paths:
        return [Path(p) for p in paths]
    return sorted((ROOT / "docs").rglob("*.md"))


def extract(path: Path) -> list[str]:
    """Every mermaid block in one document."""
    return BLOCK.findall(path.read_text(encoding="utf-8"))


def renderer() -> str | None:
    """The Mermaid CLI, when Node and the package are reachable."""
    if not shutil.which("npx"):
        return None
    probe = subprocess.run(
        ["npx", "-y", "@mermaid-js/mermaid-cli", "--version"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    return "npx" if probe.returncode == 0 else None


def validate(files: list[Path], *, tool: str, workdir: Path) -> list[str]:
    """Render each block; returns a list of failures."""
    failures: list[str] = []
    for path in files:
        for index, block in enumerate(extract(path), start=1):
            source = workdir / f"{path.stem}-{index}.mmd"
            source.write_text(block, encoding="utf-8")
            result = subprocess.run(
                [tool, "-y", "@mermaid-js/mermaid-cli", "-i", str(source), "-o", f"{source}.svg"],
                capture_output=True,
                text=True,
                timeout=300,
            )
            status = "OK" if result.returncode == 0 else "FAIL"
            print(f"  {status:<4} {path.relative_to(ROOT)} #{index}")
            if result.returncode != 0:
                first_error = next(
                    (
                        line
                        for line in (result.stdout + result.stderr).splitlines()
                        if "Error" in line or "error" in line
                    ),
                    "unknown mermaid error",
                )
                failures.append(f"{path.relative_to(ROOT)} #{index}: {first_error.strip()}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="validate mermaid diagrams in the docs")
    parser.add_argument("paths", nargs="*", help="markdown files (default: docs/**/*.md)")
    parser.add_argument("--strict", action="store_true", help="fail when the mermaid CLI is absent")
    args = parser.parse_args(argv)

    files = docs(args.paths or None)
    total = sum(len(extract(path)) for path in files)
    print(f"mermaid blocks: {total} in {len(files)} document(s)")
    if not total:
        print("nothing to validate")
        return 0

    tool = renderer()
    if tool is None:
        message = "mermaid CLI unavailable (needs node + npx): diagrams not validated"
        if args.strict:
            print(f"error: {message}", file=sys.stderr)
            return 2
        print(f"SKIP: {message}")
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        failures = validate(files, tool=tool, workdir=Path(tmp))
    if failures:
        for failure in failures:
            print(f"error: {failure}", file=sys.stderr)
        return 1
    print(f"all {total} diagram(s) rendered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
