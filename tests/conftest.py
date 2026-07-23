"""Pytest bootstrap for the whole test tree.

Adds the repository's `src/` package root to `sys.path` so every test can
import first-level packages (`app`, `engine`, `safety`, `bus`, `storage`,
`orchestration`, `ui`, ...) exactly like the API image does via
PYTHONPATH=/code/src. Runs before any test module is imported, which lets the
per-file sys.path hacks in older tests be deleted.

Phase 0 item [5]: tests/ structure for unit, integration and acceptance
tests (proposal §8.2).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
