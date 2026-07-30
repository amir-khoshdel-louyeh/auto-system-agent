"""Phase 3 item [27]: workflow + immutable version persistence (FR-03/FR-05).

Covers the persistence invariants from §9: a workflow is created once per
name, every publication pins one ``(workflow_id, version)`` row, and an
already-published version can never be overwritten with new content.
"""

import tempfile
import unittest
from pathlib import Path

from app.db import init_db
from app.models import ImmutableVersionError, WorkflowStore, dsl_hash
from engine.dsl.loader import load_text

EXAMPLE = (
    Path(__file__).resolve().parents[2] / "src" / "workflows" / "examples" / "onboarding-demo.yaml"
)


class WorkflowStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = init_db(Path(self._tmp.name) / "app.db")
        self.store = WorkflowStore(self.db)
        self.text = EXAMPLE.read_text(encoding="utf-8")
        self.workflow = load_text(self.text)

    def test_publish_creates_workflow_version_and_triggers(self):
        row = self.store.publish(self.workflow, created_by="owner")
        self.assertEqual(row["version"], self.workflow.version)
        self.assertEqual(row["dsl_hash"], dsl_hash(self.text))
        self.assertEqual(row["published_by"], "owner")

        workflow = self.store.get_by_name(self.workflow.name)
        self.assertIsNotNone(workflow)
        self.assertEqual(row["workflow_id"], workflow["id"])

        kinds = {t["kind"] for t in self.store.list_triggers(workflow["id"])}
        self.assertEqual(kinds, {"manual", "cron", "event"})

    def test_republishing_identical_text_is_idempotent(self):
        first = self.store.publish(self.workflow, created_by="owner")
        second = self.store.publish(self.workflow, created_by="owner")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.store.list_versions(first["workflow_id"])), 1)

    def test_republishing_changed_text_under_same_version_is_rejected(self):
        first = self.store.publish(self.workflow, created_by="owner")
        with self.assertRaises(ImmutableVersionError):
            self.store.publish_version(
                first["workflow_id"], first["version"], "workflow: changed\nversion: 3\n"
            )
        # the original content is untouched
        stored = self.store.get_version(first["workflow_id"], first["version"])
        self.assertEqual(stored["dsl_hash"], dsl_hash(self.text))

    def test_a_new_version_is_allowed_and_kept_separately(self):
        first = self.store.publish(self.workflow, created_by="owner")
        second = self.store.publish_version(first["workflow_id"], 4, "workflow: minimal\n")
        self.assertNotEqual(first["id"], second["id"])
        versions = [v["version"] for v in self.store.list_versions(first["workflow_id"])]
        self.assertEqual(versions, [3, 4])

    def test_same_name_maps_to_one_workflow(self):
        a = self.store.ensure_workflow("shared")
        b = self.store.ensure_workflow("shared")
        self.assertEqual(a, b)
        self.assertEqual(len(self.store.list_workflows()), 1)

    def test_version_must_be_positive(self):
        wid = self.store.ensure_workflow("w")
        for bad in (0, -1):
            with self.subTest(version=bad):
                with self.assertRaises(ValueError):
                    self.store.publish_version(wid, bad, "workflow: w\n")

    def test_empty_name_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.ensure_workflow("  ")

    def test_hash_is_content_stable(self):
        self.assertEqual(dsl_hash(self.text), dsl_hash(self.text))
        self.assertNotEqual(dsl_hash(self.text), dsl_hash(self.text + "\n# note"))


if __name__ == "__main__":
    unittest.main()
