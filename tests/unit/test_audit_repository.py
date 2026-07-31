"""Phase 5 item [46]: append-only audit repository (FR-12).

The audit log is append-only: rows accrue sequence numbers from zero, events
are returned newest-last, and pagination slices a stable ordering without
mutating anything.
"""

import tempfile
import unittest
from pathlib import Path

from app.db import init_db
from storage.audit import AuditStore


class AuditRepositoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = init_db(Path(self._tmp.name) / "app.db")
        self.store = AuditStore(self.db)

    def test_append_assigns_monotonic_sequences(self):
        for n in range(3):
            self.store.append("run-1", "node.finished", {"n": n})
        rows = self.store.events("run-1")
        self.assertEqual([r["seq"] for r in rows], [0, 1, 2])

    def test_chains_are_isolated_per_execution(self):
        self.store.append("run-1", "node.finished", {"n": 1})
        self.store.append("run-2", "node.finished", {"n": 2})
        self.assertEqual([r["seq"] for r in self.store.events("run-1")], [0])
        self.assertEqual([r["seq"] for r in self.store.events("run-2")], [0])

    def test_count_and_pagination(self):
        for n in range(10):
            self.store.append("run-1", "node.finished", {"n": n})
        self.assertEqual(self.store.count("run-1"), 10)
        self.assertEqual(self.store.count(), 10)

        page = self.store.list_page(execution_id="run-1", limit=4, offset=4)
        self.assertEqual([r["seq"] for r in page], [4, 5, 6, 7])

    def test_events_are_ordered_by_sequence(self):
        self.store.append("run-1", "run.started", {"a": 1})
        self.store.append("run-1", "node.finished", {"b": 2})
        names = [r["event"] for r in self.store.events("run-1")]
        self.assertEqual(names, ["run.started", "node.finished"])

    def test_empty_chain_reads_clean(self):
        self.assertEqual(self.store.events("ghost"), [])
        self.assertEqual(self.store.count("ghost"), 0)
        self.assertEqual(self.store.list_page(execution_id="ghost"), [])


if __name__ == "__main__":
    unittest.main()
