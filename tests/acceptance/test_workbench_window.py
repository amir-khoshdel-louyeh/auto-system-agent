"""Phase 11 item [110]: the real workbench window, driven on a display.

The view tests prove formatting without Tk. This one builds the actual widget
tree against a live API and walks every screen: the header and six tabs, the
line-numbered editor, validation findings, a dry run, save, run, timeline,
approvals, audit verification, replay and memory consent/search/erasure. It is
skipped on a headless host (CI), where the view tests remain the evidence.
"""

import os
import socket
import tempfile
import threading
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from unittest import mock

import uvicorn

from app.approvals import ApprovalService
from app.db import init_db
from app.main import create_app
from app.run_service import RunService
from app.settings import Settings
from launcher import wait_healthy
from memory.store import MemoryStore
from ui.api_client import WorkbenchClient
from ui.workbench import APPROVE, REJECT, WorkbenchView, WorkbenchWindow

HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
PASSWORD = "window-admin-pw"
SECRET = "window-secret-0123456789abcdef"

DSL = """\
workflow: demo
version: 1
triggers:
  - manual: true
nodes:
  - id: read
    type: shell
    run: echo hello
  - id: gate
    type: approval
    quorum: 1
edges:
  - [read, gate]
policy: {}
"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _fake_shell(node_id, node, ctx):
    return {"ran": node.get("params", {}).get("run", ""), "exit_code": 0, "stdout": "ok"}


def _button_labels(widget) -> set[str]:
    """Every ttk.Button label in the tree (proof the actions are reachable)."""
    labels: set[str] = set()
    for child in widget.winfo_children():
        if isinstance(child, ttk.Button):
            labels.add(str(child.cget("text")))
        labels |= _button_labels(child)
    return labels


class _LiveApi:
    """A uvicorn server on a background thread with a fake shell runner."""

    def __init__(self, settings: Settings) -> None:
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        app = create_app(settings)
        self.run_service = RunService(settings.sqlite_path, exec_fn=_fake_shell)
        app.state.run_service = self.run_service
        self.server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=self.port,
                log_level="info",
                access_log=True,
            )
        )
        self.crash: BaseException | None = None
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self) -> None:
        import traceback

        try:
            self.server.run()
        except BaseException as exc:  # noqa: BLE001 - reported in the failure
            self.crash = exc
            traceback.print_exc()

    def start(self) -> None:
        self.thread.start()
        if not wait_healthy(self.base_url, timeout=10):
            raise RuntimeError("test API did not start")

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


@unittest.skipUnless(HAS_DISPLAY, "needs a display (no X server in CI)")
class WorkbenchWindowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.settings = Settings(
            admin_user="admin",
            admin_password=PASSWORD,
            secret_key=SECRET,
            sqlite_path=str(Path(self._tmp.name) / "window.db"),
        )
        self.api = _LiveApi(self.settings)
        self.api.start()
        self.addCleanup(self.api.stop)
        self.db = init_db(self.settings.sqlite_path)
        self.client = WorkbenchClient(self.api.base_url, timeout=10)
        self.token = self.client.login("admin", PASSWORD)
        self.view = WorkbenchView(self.client)
        self.root = tk.Tk()
        self.addCleanup(self._destroy_root)
        self.window = WorkbenchWindow(self.view, self.root)
        self.root.update()

    def _destroy_root(self) -> None:
        try:
            self.root.destroy()
        except tk.TclError:  # pragma: no cover - already gone
            pass

    def _open(self, dsl: str) -> None:
        self.window._set_editor(dsl)
        self.root.update()

    def test_shell_has_six_screens_and_every_action(self):
        titles = [self.window.notebook.tab(tab, "text") for tab in self.window.notebook.tabs()]
        self.assertEqual(titles, ["Overview", "Workflow", "Runs", "Approvals", "Audit", "Memory"])

        labels = _button_labels(self.root)
        for expected in (
            "Validate",
            "Dry run",
            "Save",
            "Run",
            "Replay",
            "Audit",
            "Approve",
            "Reject",
            "Verify chain",
            "Find similar",
            "Erase…",
            "Refresh",
        ):
            self.assertIn(expected, labels)

        self.assertTrue(self.window.card_vars["runs"].get())
        self.assertNotEqual(self.window.card_vars["runs"].get(), "—")
        self.assertEqual(self.window.gutter.get("1.0", "end-1c").splitlines()[:3], ["1", "2", "3"])
        self.window.notebook.select(self.window.workflow_tab)
        self.root.update()
        self.assertGreater(self.window.editor.winfo_width(), 100)
        self.assertGreater(self.window.notebook.winfo_height(), 200)
        self.assertTrue(self.window.editor.winfo_ismapped())

    def test_workflow_to_audit_walkthrough(self):
        self._open(DSL)

        self.window.validate_workflow()
        self.root.update()
        self.assertEqual(self.window.validation_var.get(), "Valid")
        self.assertEqual(self.window.findings_tree.get_children(), ())

        self.window.dry_run_workflow()
        self.root.update()
        self.assertEqual(self.window.validation_var.get(), "Dry run ready")
        verdicts = [
            self.window.policy_tree.item(item, "values")[2]
            for item in self.window.policy_tree.get_children()
        ]
        self.assertEqual(set(verdicts), {"ALLOW"})
        self.assertIn("safe=True", self.window.summary_var.get())

        self.window.save_workflow()
        self.root.update()
        workflow_id = self.view.workflow_id
        self.assertTrue(workflow_id)
        self.assertIn("demo · v1", self.window.library_combo.get())
        self.assertIn("demo · v1", self.window.library_combo["values"])

        self.window.run_workflow()
        execution_id = self.view.execution_id
        # Wait on the run thread itself, then prove the screen shows the
        # persisted outcome; polling the UI for ``COMPLETED`` made the test
        # depend on scheduler load rather than on what the screen renders.
        self.assertTrue(
            self.api.run_service.join(execution_id, timeout=30),
            "the background run did not finish in 30s",
        )
        self.window.refresh_runs()
        self.root.update()
        row = next((run for run in self.view.run_rows if str(run["id"]) == execution_id), None)
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "COMPLETED")
        self.assertTrue(self.window.runs_tree.exists(execution_id))

        self.window._selected_run = ""
        self.window.open_run(execution_id)
        self.root.update()
        self.assertEqual(len(self.window.steps_tree.get_children()), 2)
        self.assertIn("COMPLETED", self.window.run_detail_var.get())

        self.window.update_audit_choices(select=execution_id)
        self.window.load_audit()
        self.root.update()
        if not self.window.audit_tree.get_children():
            self.fail(
                "audit empty: "
                f"status={self.window.status_var.get()!r} "
                f"ids={self.window.audit_ids} "
                f"current={self.window.audit_combo.current()} "
                f"choice={self.window.audit_combo.get()!r} "
                f"rows={len(self.view.run_rows)} err={self.view.status!r} "
                f"server_alive={self.api.thread.is_alive()} crash={self.api.crash!r}"
            )
        self.assertGreaterEqual(len(self.window.audit_tree.get_children()), 5)
        first = self.window.audit_tree.get_children()[0]
        self.assertEqual(self.window.audit_tree.item(first, "values")[1], "run.started")
        self.window.audit_tree.selection_set(first)
        self.root.update()
        self.assertIn("trigger", self.window.payload_text.get("1.0", "end"))

        self.window.verify_audit()
        self.root.update()
        self.assertIn("verified", self.window.audit_badge_var.get())

        ApprovalService(self.db).request(execution_id, "sign-off", quorum=1)
        self.window.show_approvals()
        self.root.update()
        self.assertEqual(len(self.window.approval_tree.get_children()), 1)
        self.assertNotIn("disabled", self.window.approve_button.state())
        self.window.decide(APPROVE)
        self.root.update()
        self.assertEqual(self.window.approval_detail["status"].get(), "APPROVED")
        self.assertEqual(len(self.window.approval_tree.get_children()), 1)

        self.window.notebook.select(self.window.runs_tab)
        self.window.runs_tree.selection_set(execution_id)
        with mock.patch("tkinter.messagebox.askyesno", return_value=True):
            self.window.replay_selected()
        self.assertTrue(self.view.last_replay["comparison"]["match"])
        self.assertEqual(len(self.view.run_rows), 2)

    def test_memory_consent_search_and_erasure(self):
        self.window.notebook.select(self.window.memory_tab)
        self.root.update()
        self.assertEqual(self.window.memory_consent_var.get(), "consent off")

        self.window.toggle_memory_consent()
        self.root.update()
        self.assertEqual(self.window.memory_consent_var.get(), "consent active")

        store = MemoryStore(self.db)
        item = store.save(
            {"steps": [{"id": "hello", "type": "shell", "run": "echo hello"}]},
            subject="admin",
            summary="say hello in the terminal",
            intent="greet",
        )
        store.record_episode(item.id, success=True)
        store.record_episode(item.id, success=True, rating=5)

        self.window.refresh_memory()
        self.root.update()
        self.assertTrue(self.window.memory_tree.exists(item.id))
        self.assertEqual(self.window.card_vars["memory_items"].get(), "1")
        self.assertEqual(self.window.card_vars["memory_reusable"].get(), "1")
        self.assertTrue(self.window.card_vars["memory_hit_rate"].get().endswith("%"))

        self.window.memory_tree.selection_set(item.id)
        self.root.update()
        self.assertIn("steps", self.window.memory_plan.get("1.0", "end"))
        self.assertNotIn("disabled", self.window.erase_button.state())
        self.assertEqual(len(self.window.memory_events_tree.get_children()), 3)

        self.window.memory_filter.set("say hello in the terminal")
        self.window.search_memory()
        self.root.update()
        self.assertIn("1 hit(s)", self.window.memory_summary_var.get())
        self.assertEqual(self.window.memory_tree.selection(), (item.id,))

        with mock.patch("tkinter.messagebox.askyesno", return_value=True):
            self.window.erase_selected_memory()
        self.root.update()
        self.assertFalse(self.window.memory_tree.exists(item.id))
        self.assertEqual(self.window.view.memory_items, [])

    def test_a_rejection_needs_a_reason(self):
        ApprovalService(self.db).request("run-reason", "deploy", quorum=1)
        self.window.show_approvals()
        self.root.update()
        self.window.reason_text.delete("1.0", "end")
        self.window.decide(REJECT)
        self.root.update()
        self.assertIn("needs a reason", self.window.status_var.get())
        self.assertEqual(self.window.approval_detail["status"].get(), "PENDING")
        self.window.reason_text.insert("1.0", "missing change ticket")
        self.window.decide(REJECT)
        self.root.update()
        self.assertEqual(self.window.approval_detail["status"].get(), "REJECTED")


if __name__ == "__main__":
    unittest.main()
