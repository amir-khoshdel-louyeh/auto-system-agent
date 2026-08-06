"""Phase 8 item [77]: Tkinter workbench for workflow, run, approval and audit.

The client stays a client: every screen renders a report the API produced and
every button calls one endpoint. To keep that testable without a display, the
module is split in two:

- :class:`WorkbenchView` — pure state + formatting. It takes any object with
  the :class:`WorkbenchClient` methods (a fake in tests) and turns responses
  into the exact lines a listbox shows. All behavior lives here.
- :func:`build_window` — the Tk widget tree. It only binds buttons to the
  view and copies its lines into listboxes/text boxes, so a widget bug cannot
  invent a verdict and the view can be exercised headlessly.

Screens: **Workflow** (validate + dry-run with per-node explanations),
**Runs** (execution list + step timeline), **Approvals** (quorum, decisions)
and **Audit** (hash-chain page + verify).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

APPROVE = "APPROVED"
REJECT = "REJECTED"


def _workflow_label(report: dict[str, Any]) -> str:
    """The name the API resolved, or the id when it could not."""
    return str(report.get("workflow_name") or report.get("workflow_id") or "")


def _findings(report: dict[str, Any]) -> list[str]:
    """Render the frozen `{stage, message, line, column}` findings as lines."""
    return [
        f"{error.get('stage')}:{error.get('line')}:{error.get('column')} — {error.get('message')}"
        for error in report.get("errors", [])
    ]


class WorkbenchApi(Protocol):
    """The slice of the client the screens depend on."""

    def validate(self, dsl: str, dsl_type: str = "yaml") -> dict[str, Any]: ...

    def dry_run(
        self, dsl: str, *, dsl_type: str = "yaml", workspace_root: str = ""
    ) -> dict[str, Any]: ...

    def save(self, dsl: str, dsl_type: str = "yaml") -> dict[str, Any]: ...

    def run(self, workflow_id: str, version: int, *, trigger: str = "manual") -> dict: ...

    def executions(self, limit: int = 50) -> dict[str, Any]: ...

    def timeline(self, execution_id: str) -> dict[str, Any]: ...

    def approvals(self, execution_id: str = "") -> dict[str, Any]: ...

    def decide(
        self, execution_id: str, node_id: str, verdict: str, *, reason: str = ""
    ) -> dict[str, Any]: ...

    def audit(self, execution_id: str, *, limit: int = 100) -> dict[str, Any]: ...

    def verify(self, execution_id: str) -> dict[str, Any]: ...


@dataclass
class WorkbenchView:
    """Screen state + formatting, free of Tk so it is unit-testable."""

    client: WorkbenchApi
    status: str = ""
    run_ids: list[str] = field(default_factory=list)
    approval_ids: list[tuple[str, str]] = field(default_factory=list)
    last_report: dict[str, Any] = field(default_factory=dict)
    workflow_id: str = ""
    version: int = 0
    execution_id: str = ""

    # -- workflow ---------------------------------------------------------
    def validate(self, dsl: str, dsl_type: str = "yaml") -> list[str]:
        """Validate DSL and return one line per finding (or the OK line)."""
        report = self.client.validate(dsl, dsl_type)
        self.last_report = report
        if report.get("valid"):
            self.status = "valid"
            return [f"OK {report.get('name', '?')} v{report.get('version', 0)}"]
        self.status = "invalid"
        return _findings(report) or ["invalid workflow"]

    def dry_run(self, dsl: str, *, workspace_root: str = "") -> list[str]:
        """Dry-run DSL and return the per-node verdict lines + summary."""
        report = self.client.dry_run(dsl, workspace_root=workspace_root)
        self.last_report = report
        if not report.get("valid"):
            self.status = "invalid"
            return _findings(report) or ["invalid workflow"]
        self.status = "ready" if report.get("safe_to_run") else "blocked"
        lines = [
            f"{node['node_id']} [{node['verdict']}] {node['explanation']['headline']}"
            for node in report.get("nodes", [])
        ]
        counts = report.get("counts", {})
        lines.append(
            "summary: "
            + ", ".join(f"{verdict}={count}" for verdict, count in sorted(counts.items()))
            + f" | safe_to_run={report.get('safe_to_run')}"
            + f" | persisted={report.get('persisted')} dispatched={report.get('dispatched')}"
        )
        return lines

    def save(self, dsl: str, dsl_type: str = "yaml") -> list[str]:
        """Publish the document and remember the version the API returned."""
        report = self.client.save(dsl, dsl_type)
        self.last_report = report
        if not report.get("valid"):
            self.status = "invalid"
            return _findings(report) or ["invalid workflow"]
        self.workflow_id = str(report.get("workflow_id", ""))
        self.version = int(report.get("version", 0) or 0)
        self.status = f"saved v{self.version}"
        return [f"Saved {report.get('name', '?')} v{self.version} as {self.workflow_id[:8]}"]

    def run(self, workflow_id: str = "", version: int = 0) -> list[str]:
        """Start the saved version (or an explicit one) and remember the run id."""
        target = workflow_id or self.workflow_id
        target_version = version or self.version
        if not target or not target_version:
            raise ValueError("save a workflow before running it")
        report = self.client.run(target, target_version)
        self.execution_id = str(report.get("execution_id", ""))
        self.status = str(report.get("status", ""))
        return [
            f"run {self.execution_id[:8]}: {self.status} "
            f"({str(report.get('workflow_id', target))[:8]} v{report.get('version', target_version)})"
        ]

    # -- runs -------------------------------------------------------------
    def runs(self, limit: int = 50) -> list[str]:
        """Execution rows (newest first), remembering their ids."""
        report = self.client.executions(limit)
        rows = report.get("executions", [])
        self.run_ids = [str(row.get("id", "")) for row in rows]
        self.status = f"{len(rows)} run(s)"
        return [
            f"{row.get('started_at', '')}  {row.get('status', ''):<9}  "
            f"v{row.get('version', 0)}  {_workflow_label(row)}  "
            f"{str(row.get('id', ''))[:8]}  {row.get('triggered_by', '')}"
            for row in rows
        ]

    def timeline(self, execution_id: str) -> list[str]:
        """Step transitions for one run."""
        report = self.client.timeline(execution_id)
        lines = [
            f"{report.get('status', '')} {_workflow_label(report)} "
            f"v{report.get('version', 0)} — {report.get('started_at', '')}"
        ]
        for step in report.get("steps", []):
            suffix = f" — {step['error']}" if step.get("error") else ""
            lines.append(
                f"  {step.get('node_id', '')} [{step.get('state', '')}] "
                f"attempt {step.get('attempt', 1)}{suffix}"
            )
        return lines

    # -- approvals --------------------------------------------------------
    def approvals(self, execution_id: str = "") -> list[str]:
        """Approval rows, remembering (execution, node) ids for the buttons."""
        report = self.client.approvals(execution_id)
        rows = report.get("requests", [])
        self.approval_ids = [
            (str(row.get("execution_id", "")), str(row.get("node_id", ""))) for row in rows
        ]
        self.status = f"{len(rows)} approval(s)"
        return [
            f"{row.get('status', ''):<9} quorum {row.get('quorum', 1)}  "
            f"{str(row.get('execution_id', ''))[:8]}/{row.get('node_id', '')}  "
            f"expires {row.get('expires_at', '')}"
            for row in rows
        ]

    def decide(
        self,
        execution_id: str,
        node_id: str,
        verdict: str,
        *,
        reason: str = "",
    ) -> str:
        """Cast one decision and report the resulting request status."""
        if verdict not in (APPROVE, REJECT):
            raise ValueError(f"unknown verdict {verdict!r}")
        report = self.client.decide(execution_id, node_id, verdict, reason=reason)
        request = report.get("request", {})
        self.status = str(request.get("status", ""))
        return (
            f"{execution_id[:8]}/{node_id}: {verdict} → {request.get('status', '?')} "
            f"(quorum {request.get('quorum', 1)})"
        )

    # -- audit ------------------------------------------------------------
    def audit(self, execution_id: str) -> list[str]:
        """One page of the hash chain, oldest first."""
        report = self.client.audit(execution_id)
        lines = [f"{report.get('total', 0)} event(s) for {execution_id[:8]}"]
        for event in report.get("events", []):
            lines.append(
                f"  #{event.get('seq')} {event.get('event', '')} "
                f"{str(event.get('payload', ''))[:80]} hash {str(event.get('hash', ''))[:12]}"
            )
        return lines

    def verify(self, execution_id: str) -> str:
        """Verify the chain and report the first broken link if any."""
        report = self.client.verify(execution_id)
        if report.get("ok"):
            return f"chain OK — {report.get('checked', 0)} event(s) verified"
        return (
            f"chain BROKEN at seq {report.get('first_broken_seq')}: "
            f"{report.get('reason', 'unknown')}"
        )


def build_window(view: WorkbenchView, root: Any, *, dsl_text: str = "") -> Any:
    """Build the four-tab Tkinter window and return it (requires a display)."""
    import tkinter as tk
    from tkinter import ttk

    notebook = ttk.Notebook(root)

    workflow_tab = ttk.Frame(notebook)
    dsl_entry = tk.Text(workflow_tab, height=14, width=90)
    dsl_entry.insert("1.0", dsl_text)
    workflow_list = tk.Listbox(workflow_tab, height=12, width=100)
    status_var = tk.StringVar(value=view.status)

    def _fill(widget: Any, lines: list[str]) -> None:
        widget.delete(0, tk.END)
        for line in lines:
            widget.insert(tk.END, line)

    def on_validate() -> None:
        _fill(workflow_list, view.validate(dsl_entry.get("1.0", "end")))
        status_var.set(view.status)

    def on_dry_run() -> None:
        _fill(workflow_list, view.dry_run(dsl_entry.get("1.0", "end")))
        status_var.set(view.status)

    def on_save() -> None:
        _fill(workflow_list, view.save(dsl_entry.get("1.0", "end")))
        status_var.set(view.status)

    def on_run() -> None:
        try:
            _fill(workflow_list, view.run())
        except ValueError as exc:
            _fill(workflow_list, [str(exc)])
            status_var.set(view.status)
            return
        # A started run must be visible without switching tabs and pressing
        # Refresh: the Runs list is pointed at the newly durable row.
        _fill(run_list, view.runs())
        status_var.set(view.status)

    ttk.Button(workflow_tab, text="Validate", command=on_validate).pack(anchor="w")
    ttk.Button(workflow_tab, text="Dry run", command=on_dry_run).pack(anchor="w")
    ttk.Button(workflow_tab, text="Save", command=on_save).pack(anchor="w")
    ttk.Button(workflow_tab, text="Run", command=on_run).pack(anchor="w")
    ttk.Label(workflow_tab, textvariable=status_var).pack(anchor="w")
    dsl_entry.pack(fill="both", expand=True)
    workflow_list.pack(fill="both", expand=True)
    notebook.add(workflow_tab, text="Workflow")

    runs_tab = ttk.Frame(notebook)
    run_list = tk.Listbox(runs_tab, height=14, width=100)
    timeline_list = tk.Listbox(runs_tab, height=12, width=100)

    def on_refresh_runs() -> None:
        _fill(run_list, view.runs())
        status_var.set(view.status)

    def on_open_run() -> None:
        selection = run_list.curselection()
        if selection and selection[0] < len(view.run_ids):
            _fill(timeline_list, view.timeline(view.run_ids[selection[0]]))

    ttk.Button(runs_tab, text="Refresh", command=on_refresh_runs).pack(anchor="w")
    ttk.Button(runs_tab, text="Open timeline", command=on_open_run).pack(anchor="w")
    run_list.pack(fill="both", expand=True)
    timeline_list.pack(fill="both", expand=True)
    notebook.add(runs_tab, text="Runs")

    approvals_tab = ttk.Frame(notebook)
    approval_list = tk.Listbox(approvals_tab, height=14, width=100)
    reason_entry = ttk.Entry(approvals_tab, width=60)
    decision_label = ttk.Label(approvals_tab, text="")

    def _decide(verdict: str) -> None:
        selection = approval_list.curselection()
        if not selection or selection[0] >= len(view.approval_ids):
            decision_label.configure(text="select a request first")
            return
        execution_id, node_id = view.approval_ids[selection[0]]
        decision_label.configure(
            text=view.decide(execution_id, node_id, verdict, reason=reason_entry.get())
        )

    def on_refresh_approvals() -> None:
        _fill(approval_list, view.approvals())

    ttk.Button(approvals_tab, text="Refresh", command=on_refresh_approvals).pack(anchor="w")
    approval_list.pack(fill="both", expand=True)
    ttk.Label(approvals_tab, text="Reason").pack(anchor="w")
    reason_entry.pack(fill="x")
    ttk.Button(approvals_tab, text="Approve", command=lambda: _decide(APPROVE)).pack(anchor="w")
    ttk.Button(approvals_tab, text="Reject", command=lambda: _decide(REJECT)).pack(anchor="w")
    decision_label.pack(anchor="w")
    notebook.add(approvals_tab, text="Approvals")

    audit_tab = ttk.Frame(notebook)
    audit_list = tk.Listbox(audit_tab, height=16, width=110)
    verify_label = ttk.Label(audit_tab, text="")

    def on_load_audit() -> None:
        if view.run_ids:
            _fill(audit_list, view.audit(view.run_ids[0]))

    def on_verify() -> None:
        if view.run_ids:
            verify_label.configure(text=view.verify(view.run_ids[0]))

    ttk.Button(audit_tab, text="Load (selected run)", command=on_load_audit).pack(anchor="w")
    ttk.Button(audit_tab, text="Verify chain", command=on_verify).pack(anchor="w")
    audit_list.pack(fill="both", expand=True)
    verify_label.pack(anchor="w")
    notebook.add(audit_tab, text="Audit")

    notebook.pack(fill="both", expand=True)
    return notebook


def run_workbench(base_url: str, token: str = "") -> None:  # pragma: no cover - needs a display
    """Open the workbench against ``base_url`` with a ready token.

    The launcher (item [105]) has already started the API and logged in, so
    this is the only call the entrypoint needs to make.
    """
    import tkinter as tk

    from .api_client import WorkbenchClient

    root = tk.Tk()
    root.title("Auto System Agent — workbench")
    build_window(WorkbenchView(WorkbenchClient(base_url, token)), root)
    root.mainloop()


def main() -> None:  # pragma: no cover - interactive entrypoint
    """Launch the workbench against ``--base-url`` with a bearer token."""
    import argparse

    from .api_client import WorkbenchClient

    parser = argparse.ArgumentParser(description="Auto System Agent workbench")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    args = parser.parse_args()

    client = WorkbenchClient(args.base_url, args.token)
    if args.user:
        client.login(args.user, args.password)
    run_workbench(args.base_url, client.token)


if __name__ == "__main__":  # pragma: no cover - interactive entrypoint
    main()
