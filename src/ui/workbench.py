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

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from .api_client import ApiError
from .theme import COLORS, STATE_COLORS, apply_theme, card

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

    def workflows(self) -> dict[str, Any]: ...

    def workflow_version(self, workflow_id: str, version: int) -> dict[str, Any]: ...

    def executions(self, limit: int = 50) -> dict[str, Any]: ...

    def metrics(self) -> dict[str, Any]: ...

    def me(self) -> dict[str, Any]: ...

    def timeline(self, execution_id: str) -> dict[str, Any]: ...

    def approvals(self, execution_id: str = "") -> dict[str, Any]: ...

    def decide(
        self, execution_id: str, node_id: str, verdict: str, *, reason: str = ""
    ) -> dict[str, Any]: ...

    def audit(self, execution_id: str, *, limit: int = 100) -> dict[str, Any]: ...

    def verify(self, execution_id: str) -> dict[str, Any]: ...

    def replay(self, execution_id: str) -> dict[str, Any]: ...


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
    workflow_rows: list[dict[str, Any]] = field(default_factory=list)
    metrics_data: dict[str, Any] = field(default_factory=dict)
    profile_data: dict[str, Any] = field(default_factory=dict)
    workflow_name: str = ""
    finding_rows: list[dict[str, Any]] = field(default_factory=list)
    dry_run_rows: list[dict[str, Any]] = field(default_factory=list)
    run_rows: list[dict[str, Any]] = field(default_factory=list)
    step_rows: list[dict[str, Any]] = field(default_factory=list)
    approval_rows: list[dict[str, Any]] = field(default_factory=list)
    audit_rows: list[dict[str, Any]] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)
    last_replay: dict[str, Any] = field(default_factory=dict)

    # -- workflow ---------------------------------------------------------
    def validate(self, dsl: str, dsl_type: str = "yaml") -> list[str]:
        """Validate DSL and return one line per finding (or the OK line)."""
        report = self.client.validate(dsl, dsl_type)
        self.last_report = report
        self.finding_rows = list(report.get("errors", []))
        self.dry_run_rows = []
        if report.get("valid"):
            self.status = "valid"
            self.workflow_name = str(report.get("name", ""))
            return [f"OK {report.get('name', '?')} v{report.get('version', 0)}"]
        self.status = "invalid"
        return _findings(report) or ["invalid workflow"]

    def dry_run(self, dsl: str, *, workspace_root: str = "") -> list[str]:
        """Dry-run DSL and return the per-node verdict lines + summary."""
        report = self.client.dry_run(dsl, workspace_root=workspace_root)
        self.last_report = report
        if not report.get("valid"):
            self.status = "invalid"
            self.finding_rows = list(report.get("errors", []))
            self.dry_run_rows = []
            return _findings(report) or ["invalid workflow"]
        self.finding_rows = []
        self.dry_run_rows = list(report.get("nodes", []))
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
            self.finding_rows = list(report.get("errors", []))
            return _findings(report) or ["invalid workflow"]
        self.finding_rows = []
        self.workflow_id = str(report.get("workflow_id", ""))
        self.version = int(report.get("version", 0) or 0)
        self.workflow_name = str(report.get("name", self.workflow_name))
        self.status = f"saved v{self.version}"
        return [f"Saved {report.get('name', '?')} v{self.version} as {self.workflow_id[:8]}"]

    def library(self) -> list[str]:
        """Published workflows + versions for the library listbox."""
        report = self.client.workflows()
        self.workflow_rows = report.get("workflows", [])
        self.status = f"{len(self.workflow_rows)} workflow(s)"
        return [
            f"{workflow.get('name', '?')} · v{version['version']}"
            for workflow in self.workflow_rows
            for version in workflow.get("versions", [])
        ]

    def load(self, workflow_id: str, version: int) -> str:
        """Fetch one immutable version and remember it; returns its DSL text."""
        report = self.client.workflow_version(workflow_id, version)
        self.workflow_id = str(report.get("workflow_id", workflow_id))
        self.version = int(report.get("version", version) or 0)
        self.workflow_name = str(report.get("name", ""))
        self.last_report = report
        self.status = f"loaded {report.get('name', '?')} v{self.version}"
        return str(report.get("dsl_text", ""))

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
        self.run_rows = list(rows)
        self.run_ids = [str(row.get("id", "")) for row in rows]
        self.status = f"{len(rows)} run(s)"
        return [
            f"{row.get('started_at', '')}  {row.get('status', ''):<9}  "
            f"v{row.get('version', 0)}  {_workflow_label(row)}  "
            f"{str(row.get('id', ''))[:8]}  {row.get('triggered_by', '')}"
            for row in rows
        ]

    def filter_runs(self, status: str = "", needle: str = "") -> list[dict[str, Any]]:
        """Client-side filter over the last run list (no extra request)."""
        text = needle.strip().lower()
        kept: list[dict[str, Any]] = []
        for row in self.run_rows:
            if status and row.get("status") != status:
                continue
            haystack = " ".join(str(value) for value in row.values()).lower()
            if text and text not in haystack:
                continue
            kept.append(row)
        return kept

    def replay(self, execution_id: str = "") -> list[str]:
        """Re-run the pinned version of a completed execution (FR-13)."""
        target = execution_id or self.execution_id
        if not target:
            raise ValueError("select a run to replay")
        report = self.client.replay(target)
        self.last_replay = report
        comparison = report.get("comparison", {}) or {}
        match = bool(comparison.get("match"))
        self.status = "replay matched" if match else "replay differs"
        return [
            f"replay {str(report.get('replay_execution_id', ''))[:8]} of {target[:8]}: "
            f"{report.get('status', '')}",
            f"match={match} compared={comparison.get('compared', 0)} "
            f"mismatched={len(comparison.get('mismatched', []))} "
            f"missing={len(comparison.get('missing', []))} "
            f"unexpected={len(comparison.get('unexpected', []))}",
        ]

    def timeline(self, execution_id: str) -> list[str]:
        """Step transitions for one run."""
        report = self.client.timeline(execution_id)
        self.step_rows = list(report.get("steps", []))
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

    def metrics(self) -> list[str]:
        """Aggregate run metrics for the overview cards."""
        report = self.client.metrics()
        self.metrics_data = report
        self.status = f"{report.get('total', 0)} run(s)"
        return [
            f"runs={report.get('total', 0)}",
            f"success_rate={report.get('success_rate', 0)}%",
            f"p95={report.get('p95_latency_ms', 0)} ms",
            f"retries={report.get('retries', 0)}",
            f"approvals={report.get('approval_count', 0)}",
        ]

    def profile(self) -> str:
        """The signed-in identity, formatted for the header chip."""
        report = self.client.me()
        self.profile_data = report
        return f"{report.get('username', '?')} · {report.get('role', '')}"

    def pending_approvals(self) -> int:
        """How many listed approval requests are still waiting."""
        return sum(1 for row in self.approval_rows if row.get("status") == "PENDING")

    # -- approvals --------------------------------------------------------
    def approvals(self, execution_id: str = "") -> list[str]:
        """Approval rows, remembering (execution, node) ids for the buttons."""
        report = self.client.approvals(execution_id)
        rows = report.get("requests", [])
        self.approval_rows = list(rows)
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
        self.audit_rows = list(report.get("events", []))
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
        self.verification = dict(report)
        if report.get("ok"):
            return f"chain OK — {report.get('checked', 0)} event(s) verified"
        return (
            f"chain BROKEN at seq {report.get('first_broken_seq')}: "
            f"{report.get('reason', 'unknown')}"
        )


# ---------------------------------------------------------------------------
# Tk front end (needs a display; only formatting/state above is unit-tested)
# ---------------------------------------------------------------------------

#: Starter document for "New" — valid, runnable and self-explanatory.
STARTER_DSL = """\
workflow: my-workflow
version: 1
triggers:
  - manual: true
nodes:
  - id: hello
    type: shell
    run: echo "hello from Auto System Agent"
edges: []
policy: {}
"""


def _pretty_payload(raw: Any) -> str:
    """Pretty-print an audit payload for the detail pane."""
    try:
        value = json.loads(str(raw or "{}"))
    except ValueError:
        return str(raw or "")
    return json.dumps(value, indent=2, sort_keys=True)


class LoginDialog:
    """Modal sign-in shown when the launcher could not obtain a token."""

    def __init__(self, root: Any, client: Any) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.client = client
        self.ok = False
        self.window = tk.Toplevel(root)
        self.window.title("Sign in · Auto System Agent")
        self.window.configure(bg=COLORS["bg"])
        self.window.resizable(False, False)
        self.user_var = tk.StringVar(value=os.environ.get("APP_ADMIN_USER", "admin"))
        self.password_var = tk.StringVar(value=os.environ.get("APP_ADMIN_PASSWORD", ""))
        self.error_var = tk.StringVar(value="")

        outer = card(self.window)
        outer.pack(fill="both", expand=True, padx=18, pady=18)
        body = ttk.Frame(outer, style="Surface.TFrame")
        body.pack(fill="both", expand=True, padx=24, pady=22)
        ttk.Label(body, text="Sign in", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            body,
            text="Authenticate against the automation API to open the workbench.",
            style="CardLabel.TLabel",
        ).pack(anchor="w", pady=(2, 14))
        ttk.Label(body, text="API", style="CardLabel.TLabel").pack(anchor="w")
        ttk.Label(body, text=str(getattr(client, "base_url", "")), style="Surface.TLabel").pack(
            anchor="w"
        )
        ttk.Label(body, text="Username", style="CardLabel.TLabel").pack(anchor="w", pady=(14, 3))
        self.user_entry = ttk.Entry(body, textvariable=self.user_var, width=36)
        self.user_entry.pack(fill="x")
        ttk.Label(body, text="Password", style="CardLabel.TLabel").pack(anchor="w", pady=(10, 3))
        self.password_entry = ttk.Entry(body, textvariable=self.password_var, show="•", width=36)
        self.password_entry.pack(fill="x")
        tk.Label(
            body,
            textvariable=self.error_var,
            bg=COLORS["surface"],
            fg=COLORS["danger"],
            anchor="w",
            justify="left",
            wraplength=360,
        ).pack(anchor="w", pady=(10, 0))
        actions = ttk.Frame(body, style="Surface.TFrame")
        actions.pack(fill="x", pady=(16, 0))
        ttk.Button(actions, text="Quit", command=self.cancel).pack(side="right")
        ttk.Button(actions, text="Sign in", style="Accent.TButton", command=self.submit).pack(
            side="right", padx=(0, 8)
        )

        self.window.bind("<Return>", lambda _event: self.submit())
        self.window.bind("<Escape>", lambda _event: self.cancel())
        self.window.protocol("WM_DELETE_WINDOW", self.cancel)
        (self.password_entry if self.password_var.get() else self.user_entry).focus_set()
        self._center(root)
        self.window.grab_set()

    def _center(self, root: Any) -> None:
        self.window.update_idletasks()
        width = self.window.winfo_reqwidth()
        height = self.window.winfo_reqheight()
        x = max(0, (self.window.winfo_screenwidth() - width) // 2)
        y = max(0, (self.window.winfo_screenheight() - height) // 3)
        self.window.geometry(f"+{x}+{y}")

    def submit(self) -> None:
        try:
            self.client.login(self.user_var.get().strip(), self.password_var.get())
        except ApiError as exc:
            self.error_var.set(str(exc))
            return
        self.ok = True
        self.window.destroy()

    def cancel(self) -> None:
        self.ok = False
        self.window.destroy()

    def show(self) -> bool:
        self.window.wait_window()
        return self.ok


class WorkbenchWindow:
    """The application shell: header, five screens, status bar and menus."""

    #: Poll interval while a run is still RUNNING.
    POLL_MS = 2500

    def __init__(self, view: WorkbenchView, root: Any, *, dsl_text: str = "") -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.view = view
        self.root = root
        self.family, self.mono = apply_theme(root)
        self._selected_run = ""
        self._connected = False
        self._library_choices: list[tuple[str, str, int]] = []
        self.audit_ids: list[str] = []
        self.card_vars: dict[str, Any] = {}
        self.status_label: Any = None

        width = min(1280, max(960, root.winfo_screenwidth() - 80))
        height = min(840, max(640, root.winfo_screenheight() - 120))
        root.title("Auto System Agent · Workbench")
        root.geometry(
            f"{width}x{height}+{max(0, (root.winfo_screenwidth() - width) // 2)}"
            f"+{max(0, (root.winfo_screenheight() - height) // 2 - 20)}"
        )
        root.minsize(min(1080, width), min(700, height))

        self.status_var = tk.StringVar(value="starting…")
        self.connection_var = tk.StringVar(value="● connecting…")
        self.user_var = tk.StringVar(value="signed in")
        self.api_var = tk.StringVar(value=f"API · {getattr(view.client, 'base_url', '')}")
        self.workflow_var = tk.StringVar(value="no workflow saved")
        self.validation_var = tk.StringVar(value="Not validated")
        self.summary_var = tk.StringVar(value="")
        self.run_detail_var = tk.StringVar(value="Select a run to see its timeline.")
        self.audit_badge_var = tk.StringVar(value="not verified")

        self._build_header()
        self._build_menus()
        self._build_statusbar()
        self._build_tabs(dsl_text)
        self._bind_shortcuts()
        self.refresh_all(quiet=True)
        root.after(self.POLL_MS, self._poll)

    # -- chrome -----------------------------------------------------------
    def _build_header(self) -> None:
        tk = self.tk
        header = tk.Frame(self.root, bg=COLORS["header"], height=82)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)
        left = tk.Frame(header, bg=COLORS["header"])
        left.pack(side="left", padx=22, pady=14)
        tk.Label(
            left,
            text="Auto System Agent",
            bg=COLORS["header"],
            fg=COLORS["header_text"],
            font=(self.family, 18, "bold"),
        ).pack(anchor="w")
        tk.Label(
            left,
            text="Governed automation workbench",
            bg=COLORS["header"],
            fg=COLORS["header_muted"],
            font=(self.family, 10),
        ).pack(anchor="w")
        right = tk.Frame(header, bg=COLORS["header"])
        right.pack(side="right", padx=22)
        self.connection_label = tk.Label(
            right,
            textvariable=self.connection_var,
            bg=COLORS["header"],
            fg=COLORS["header_muted"],
            font=(self.family, 10),
        )
        self.connection_label.pack(side="left", padx=(0, 18))
        tk.Label(
            right,
            textvariable=self.user_var,
            bg=COLORS["header"],
            fg=COLORS["header_text"],
            font=(self.family, 10),
        ).pack(side="left", padx=(0, 14))
        self.ttk.Button(
            right, text="Refresh", style="HeaderAction.TButton", command=self.refresh_all
        ).pack(side="left")

    def _build_statusbar(self) -> None:
        tk = self.tk
        bar = tk.Frame(
            self.root,
            bg=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        bar.pack(side="bottom", fill="x")
        inner = tk.Frame(bar, bg=COLORS["surface"])
        inner.pack(fill="x", padx=18, pady=6)
        self.status_label = tk.Label(
            inner,
            textvariable=self.status_var,
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=(self.family, 9),
            anchor="w",
        )
        self.status_label.pack(side="left")
        tk.Label(
            inner,
            text="Ctrl+S save · Ctrl+R run · F5 refresh",
            bg=COLORS["surface"],
            fg=COLORS["faint"],
            font=(self.family, 9),
        ).pack(side="right", padx=(12, 0))
        tk.Label(
            inner,
            textvariable=self.api_var,
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=(self.family, 9),
        ).pack(side="right")

    def _build_menus(self) -> None:
        tk = self.tk
        bar = tk.Menu(self.root)
        file_menu = tk.Menu(bar, tearoff=0)
        file_menu.add_command(label="New workflow", accelerator="Ctrl+N", command=self.new_workflow)
        file_menu.add_command(
            label="Save and publish", accelerator="Ctrl+S", command=self.save_workflow
        )
        file_menu.add_command(label="Run", accelerator="Ctrl+R", command=self.run_workflow)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", accelerator="Ctrl+Q", command=self.root.destroy)
        bar.add_cascade(label="File", menu=file_menu)

        run_menu = tk.Menu(bar, tearoff=0)
        run_menu.add_command(label="Refresh everything", accelerator="F5", command=self.refresh_all)
        run_menu.add_command(label="Replay selected run", command=self.replay_selected)
        run_menu.add_command(label="Audit selected run", command=self.audit_selected)
        bar.add_cascade(label="Run", menu=run_menu)

        view_menu = tk.Menu(bar, tearoff=0)
        view_menu.add_command(
            label="Overview", command=lambda: self.notebook.select(self.overview_tab)
        )
        view_menu.add_command(
            label="Workflow", command=lambda: self.notebook.select(self.workflow_tab)
        )
        view_menu.add_command(label="Runs", command=lambda: self.notebook.select(self.runs_tab))
        view_menu.add_command(
            label="Approvals", command=lambda: self.notebook.select(self.approvals_tab)
        )
        view_menu.add_command(label="Audit", command=lambda: self.notebook.select(self.audit_tab))
        bar.add_cascade(label="View", menu=view_menu)

        help_menu = tk.Menu(bar, tearoff=0)
        help_menu.add_command(label="Keyboard shortcuts", command=self.show_shortcuts)
        help_menu.add_command(label="About", command=self.show_about)
        bar.add_cascade(label="Help", menu=help_menu)
        self.root.configure(menu=bar)

    # -- tabs -------------------------------------------------------------
    def _build_tabs(self, dsl_text: str) -> None:
        ttk = self.ttk
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=18, pady=(14, 0))
        self.overview_tab = ttk.Frame(self.notebook)
        self.workflow_tab = ttk.Frame(self.notebook)
        self.runs_tab = ttk.Frame(self.notebook)
        self.approvals_tab = ttk.Frame(self.notebook)
        self.audit_tab = ttk.Frame(self.notebook)
        for frame, label in (
            (self.overview_tab, "Overview"),
            (self.workflow_tab, "Workflow"),
            (self.runs_tab, "Runs"),
            (self.approvals_tab, "Approvals"),
            (self.audit_tab, "Audit"),
        ):
            self.notebook.add(frame, text=label)
        self._build_overview(self.overview_tab)
        self._build_workflow(self.workflow_tab, dsl_text)
        self._build_runs(self.runs_tab)
        self._build_approvals(self.approvals_tab)
        self._build_audit(self.audit_tab)

    def _make_tree(
        self,
        parent: Any,
        *,
        columns: tuple[str, ...],
        headings: tuple[str, ...],
        widths: tuple[int, ...],
        height: int = 10,
    ) -> Any:
        """A bordered Treeview with a scrollbar and status tags."""
        holder = self.ttk.Frame(parent, style="Surface.TFrame")
        holder.pack(fill="both", expand=True)
        tree = self.ttk.Treeview(
            holder, columns=columns, show="headings", height=height, selectmode="browse"
        )
        for column, heading, width in zip(columns, headings, widths, strict=False):
            tree.heading(column, text=heading, anchor="w")
            tree.column(column, width=width, minwidth=60, anchor="w", stretch=True)
        scroll = self.ttk.Scrollbar(holder, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        for state, colour in STATE_COLORS.items():
            tree.tag_configure(state, foreground=colour)
        return tree

    # -- overview ---------------------------------------------------------
    def _build_overview(self, parent: Any) -> None:
        ttk, tk = self.ttk, self.tk
        page = ttk.Frame(parent)
        page.pack(fill="both", expand=True, padx=18, pady=16)
        head = ttk.Frame(page)
        head.pack(fill="x")
        ttk.Label(head, text="Overview", style="Title.TLabel").pack(side="left")
        ttk.Button(head, text="Refresh", command=self.refresh_all).pack(side="right")
        ttk.Label(
            page,
            text="Live state of the governed automation platform.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 14))

        cards = ttk.Frame(page)
        cards.pack(fill="x")
        for key, title, hint in (
            ("runs", "Executions", "durable runs"),
            ("success", "Success rate", "completed ÷ total"),
            ("p95", "p95 latency", "wall-clock, all runs"),
            ("pending", "Pending approvals", "waiting for a human"),
        ):
            frame = card(cards)
            frame.pack(side="left", fill="x", expand=True, padx=(0, 10))
            inner = ttk.Frame(frame, style="Surface.TFrame")
            inner.pack(fill="both", expand=True, padx=16, pady=12)
            var = tk.StringVar(value="—")
            ttk.Label(inner, textvariable=var, style="CardValue.TLabel").pack(anchor="w")
            ttk.Label(inner, text=title, style="Section.TLabel").pack(anchor="w", pady=(4, 0))
            ttk.Label(inner, text=hint, style="CardLabel.TLabel").pack(anchor="w")
            self.card_vars[key] = var

        body = ttk.Frame(page)
        body.pack(fill="both", expand=True, pady=(16, 0))
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        ttk.Label(left, text="Recent executions", style="SectionHeading.TLabel").pack(
            anchor="w", pady=(0, 6)
        )
        self.overview_runs = self._make_tree(
            left,
            columns=("status", "workflow", "version", "started", "id"),
            headings=("Status", "Workflow", "Version", "Started", "Execution"),
            widths=(110, 260, 70, 190, 140),
            height=9,
        )
        self.overview_runs.bind(
            "<<TreeviewSelect>>", lambda _event: self.open_run_from(self.overview_runs)
        )

        right = ttk.Frame(body)
        right.pack(side="left", fill="y", padx=(16, 0))
        actions = card(right)
        actions.pack(fill="x")
        inner = ttk.Frame(actions, style="Surface.TFrame")
        inner.pack(fill="both", padx=16, pady=14)
        ttk.Label(inner, text="Quick actions", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        for label, command in (
            ("Author a workflow", lambda: self.notebook.select(self.workflow_tab)),
            ("Browse runs", lambda: self.notebook.select(self.runs_tab)),
            ("Review approvals", self.show_approvals),
            ("Verify an audit chain", self.show_audit),
        ):
            ttk.Button(inner, text=label, command=command).pack(fill="x", pady=2)
        ttk.Label(right, text="Tip: F5 refreshes every screen.", style="Muted.TLabel").pack(
            anchor="w", pady=(10, 0)
        )

    # -- workflow ---------------------------------------------------------
    def _build_workflow(self, parent: Any, dsl_text: str) -> None:
        ttk, tk = self.ttk, self.tk
        page = ttk.Frame(parent)
        page.pack(fill="both", expand=True, padx=18, pady=14)
        toolbar = ttk.Frame(page)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="New", command=self.new_workflow).pack(side="left")
        ttk.Button(toolbar, text="Open…", command=self.focus_library).pack(side="left", padx=(6, 0))
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=12)
        ttk.Button(toolbar, text="Validate", command=self.validate_workflow).pack(side="left")
        ttk.Button(toolbar, text="Dry run", command=self.dry_run_workflow).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(toolbar, text="Save", command=self.save_workflow).pack(side="left", padx=(12, 0))
        ttk.Button(toolbar, text="Run", style="Accent.TButton", command=self.run_workflow).pack(
            side="left", padx=(6, 0)
        )
        ttk.Label(toolbar, textvariable=self.workflow_var, style="Muted.TLabel").pack(side="right")

        library = ttk.Frame(page)
        library.pack(fill="x", pady=(10, 0))
        ttk.Label(library, text="Library", style="Muted.TLabel").pack(side="left")
        self.library_combo = ttk.Combobox(library, state="readonly", width=48)
        self.library_combo.pack(side="left", padx=(8, 8))
        ttk.Button(library, text="Open", command=self.load_selected).pack(side="left")
        ttk.Button(library, text="Refresh", command=self.refresh_library).pack(
            side="left", padx=(6, 0)
        )

        paned = ttk.Panedwindow(page, orient="horizontal")
        paned.pack(fill="both", expand=True, pady=(12, 0))
        editor_card = card(paned)
        paned.add(editor_card, weight=3)
        side = ttk.Panedwindow(paned, orient="vertical")
        paned.add(side, weight=2)

        head = ttk.Frame(editor_card, style="Surface.TFrame")
        head.pack(fill="x", padx=14, pady=(12, 6))
        ttk.Label(head, text="Workflow DSL", style="Section.TLabel").pack(side="left")
        ttk.Label(head, text="YAML / JSON", style="CardLabel.TLabel").pack(side="right")
        body = ttk.Frame(editor_card, style="Surface.TFrame")
        body.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        self.gutter = tk.Text(
            body,
            width=4,
            padx=8,
            pady=8,
            takefocus=0,
            bd=0,
            relief="flat",
            bg=COLORS["surface_alt"],
            fg=COLORS["faint"],
            font=(self.mono, 10),
            state="disabled",
            cursor="arrow",
        )
        self.gutter.grid(row=0, column=0, sticky="ns")
        self.editor = tk.Text(
            body,
            wrap="none",
            undo=True,
            bd=0,
            relief="flat",
            bg=COLORS["surface"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            selectbackground=COLORS["accent_pale"],
            font=(self.mono, 10),
            padx=12,
            pady=8,
        )
        self.editor.grid(row=0, column=1, sticky="nsew")
        vscroll = ttk.Scrollbar(body, orient="vertical", command=self._scroll_editor)
        vscroll.grid(row=0, column=2, sticky="ns")
        hscroll = ttk.Scrollbar(body, orient="horizontal", command=self.editor.xview)
        hscroll.grid(row=1, column=1, sticky="ew")
        self.editor.configure(
            yscrollcommand=lambda first, last: self._sync_editor_scroll(first, last, vscroll),
            xscrollcommand=hscroll.set,
        )
        self.editor.bind("<<Modified>>", lambda _event: self._on_editor_modified())

        footer = ttk.Frame(editor_card, style="Surface.TFrame")
        footer.pack(fill="x", padx=14, pady=(0, 10))
        ttk.Label(
            footer,
            text="Ctrl+Enter validate · Ctrl+S save · Ctrl+R run",
            style="CardLabel.TLabel",
        ).pack(side="left")
        self.line_count_var = tk.StringVar(value="")
        ttk.Label(footer, textvariable=self.line_count_var, style="CardLabel.TLabel").pack(
            side="right"
        )

        validation_card = card(side)
        side.add(validation_card, weight=2)
        vhead = ttk.Frame(validation_card, style="Surface.TFrame")
        vhead.pack(fill="x", padx=14, pady=(12, 4))
        ttk.Label(vhead, text="Validation", style="Section.TLabel").pack(side="left")
        ttk.Label(vhead, textvariable=self.validation_var, style="CardLabel.TLabel").pack(
            side="right"
        )
        self.findings_tree = self._make_tree(
            validation_card,
            columns=("location", "stage", "message"),
            headings=("Location", "Stage", "Message"),
            widths=(80, 90, 340),
            height=6,
        )

        policy_card = card(side)
        side.add(policy_card, weight=3)
        phead = ttk.Frame(policy_card, style="Surface.TFrame")
        phead.pack(fill="x", padx=14, pady=(12, 4))
        ttk.Label(phead, text="Policy preview", style="Section.TLabel").pack(side="left")
        ttk.Label(phead, textvariable=self.summary_var, style="CardLabel.TLabel").pack(side="right")
        self.policy_tree = self._make_tree(
            policy_card,
            columns=("node", "type", "verdict", "explanation"),
            headings=("Node", "Type", "Verdict", "Explanation"),
            widths=(100, 80, 110, 300),
            height=7,
        )

        self._set_editor(dsl_text or STARTER_DSL)

    # -- runs -------------------------------------------------------------
    def _build_runs(self, parent: Any) -> None:
        ttk, tk = self.ttk, self.tk
        page = ttk.Frame(parent)
        page.pack(fill="both", expand=True, padx=18, pady=14)
        toolbar = ttk.Frame(page)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Refresh", command=self.refresh_runs).pack(side="left")
        ttk.Button(toolbar, text="Replay", command=self.replay_selected).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(toolbar, text="Audit", command=self.audit_selected).pack(
            side="left", padx=(6, 0)
        )
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=12)
        ttk.Label(toolbar, text="Status", style="Muted.TLabel").pack(side="left")
        self.status_filter = tk.StringVar(value="All")
        self.status_filter_box = ttk.Combobox(
            toolbar,
            textvariable=self.status_filter,
            state="readonly",
            width=12,
            values=("All", "RUNNING", "COMPLETED", "FAILED", "BLOCKED"),
        )
        self.status_filter_box.pack(side="left", padx=(6, 10))
        self.status_filter_box.bind("<<ComboboxSelected>>", lambda _event: self.render_runs())
        ttk.Label(toolbar, text="Search", style="Muted.TLabel").pack(side="left")
        self.run_search = tk.StringVar()
        search = ttk.Entry(toolbar, textvariable=self.run_search, width=26)
        search.pack(side="left", padx=(6, 0))
        search.bind("<KeyRelease>", lambda _event: self.render_runs())

        paned = ttk.Panedwindow(page, orient="vertical")
        paned.pack(fill="both", expand=True, pady=(12, 0))
        top = card(paned)
        paned.add(top, weight=3)
        top_head = ttk.Frame(top, style="Surface.TFrame")
        top_head.pack(fill="x", padx=14, pady=(12, 4))
        ttk.Label(top_head, text="Executions", style="Section.TLabel").pack(side="left")
        ttk.Label(top_head, text="Newest first · select to open", style="CardLabel.TLabel").pack(
            side="right"
        )
        self.runs_tree = self._make_tree(
            top,
            columns=("status", "workflow", "version", "trigger", "started", "finished", "id"),
            headings=(
                "Status",
                "Workflow",
                "Version",
                "Trigger",
                "Started",
                "Finished",
                "Execution",
            ),
            widths=(110, 230, 70, 110, 180, 180, 140),
            height=9,
        )
        self.runs_tree.bind("<<TreeviewSelect>>", lambda _event: self.open_run_from(self.runs_tree))

        bottom = card(paned)
        paned.add(bottom, weight=2)
        bhead = ttk.Frame(bottom, style="Surface.TFrame")
        bhead.pack(fill="x", padx=14, pady=(12, 4))
        ttk.Label(bhead, text="Timeline", style="Section.TLabel").pack(side="left")
        ttk.Label(bhead, textvariable=self.run_detail_var, style="CardLabel.TLabel").pack(
            side="right"
        )
        self.steps_tree = self._make_tree(
            bottom,
            columns=("node", "state", "attempt", "error"),
            headings=("Node", "State", "Attempt", "Error"),
            widths=(160, 120, 70, 420),
            height=8,
        )

    # -- approvals --------------------------------------------------------
    def _build_approvals(self, parent: Any) -> None:
        ttk, tk = self.ttk, self.tk
        page = ttk.Frame(parent)
        page.pack(fill="both", expand=True, padx=18, pady=14)
        toolbar = ttk.Frame(page)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Refresh", command=self.refresh_approvals).pack(side="left")
        ttk.Label(
            toolbar,
            text="Gated steps wait here; every decision is recorded on the audit chain.",
            style="Muted.TLabel",
        ).pack(side="left", padx=(12, 0))

        paned = ttk.Panedwindow(page, orient="horizontal")
        paned.pack(fill="both", expand=True, pady=(12, 0))
        left = card(paned)
        paned.add(left, weight=3)
        lhead = ttk.Frame(left, style="Surface.TFrame")
        lhead.pack(fill="x", padx=14, pady=(12, 4))
        ttk.Label(lhead, text="Requests", style="Section.TLabel").pack(side="left")
        self.approval_tree = self._make_tree(
            left,
            columns=("status", "node", "quorum", "execution", "expires"),
            headings=("Status", "Node", "Quorum", "Execution", "Expires"),
            widths=(110, 140, 70, 130, 190),
            height=10,
        )
        self.approval_tree.bind("<<TreeviewSelect>>", lambda _event: self._show_selected_approval())

        detail = card(paned)
        paned.add(detail, weight=2)
        inner = ttk.Frame(detail, style="Surface.TFrame")
        inner.pack(fill="both", expand=True, padx=16, pady=14)
        ttk.Label(inner, text="Decision", style="Section.TLabel").pack(anchor="w")
        self.approval_detail = {
            key: tk.StringVar(value="—")
            for key in ("execution", "node", "quorum", "status", "expires")
        }
        for label, key in (
            ("Execution", "execution"),
            ("Node", "node"),
            ("Quorum", "quorum"),
            ("Status", "status"),
            ("Expires", "expires"),
        ):
            row = ttk.Frame(inner, style="Surface.TFrame")
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=label, style="CardLabel.TLabel", width=10).pack(side="left")
            ttk.Label(row, textvariable=self.approval_detail[key], style="Surface.TLabel").pack(
                side="left"
            )
        ttk.Label(inner, text="Reason", style="CardLabel.TLabel").pack(anchor="w", pady=(12, 4))
        self.reason_text = tk.Text(
            inner,
            height=4,
            wrap="word",
            bd=0,
            relief="flat",
            bg=COLORS["surface_alt"],
            fg=COLORS["text"],
            padx=8,
            pady=6,
            font=(self.family, 10),
        )
        self.reason_text.pack(fill="x")
        buttons = ttk.Frame(inner, style="Surface.TFrame")
        buttons.pack(fill="x", pady=(12, 0))
        self.reject_button = ttk.Button(
            buttons, text="Reject", style="Danger.TButton", command=lambda: self.decide(REJECT)
        )
        self.reject_button.pack(side="right")
        self.approve_button = ttk.Button(
            buttons, text="Approve", style="Accent.TButton", command=lambda: self.decide(APPROVE)
        )
        self.approve_button.pack(side="right", padx=(0, 8))
        self._set_decide_enabled(False)

    # -- audit ------------------------------------------------------------
    def _build_audit(self, parent: Any) -> None:
        ttk, tk = self.ttk, self.tk
        page = ttk.Frame(parent)
        page.pack(fill="both", expand=True, padx=18, pady=14)
        toolbar = ttk.Frame(page)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="Run", style="Muted.TLabel").pack(side="left")
        self.audit_combo = ttk.Combobox(toolbar, state="readonly", width=58)
        self.audit_combo.pack(side="left", padx=(8, 8))
        ttk.Button(toolbar, text="Load", command=self.load_audit).pack(side="left")
        ttk.Button(toolbar, text="Verify chain", command=self.verify_audit).pack(
            side="left", padx=(6, 0)
        )
        self.audit_badge = tk.Label(
            toolbar,
            textvariable=self.audit_badge_var,
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=(self.family, 10, "bold"),
        )
        self.audit_badge.pack(side="right")

        paned = ttk.Panedwindow(page, orient="horizontal")
        paned.pack(fill="both", expand=True, pady=(12, 0))
        events_card = card(paned)
        paned.add(events_card, weight=3)
        ehead = ttk.Frame(events_card, style="Surface.TFrame")
        ehead.pack(fill="x", padx=14, pady=(12, 4))
        ttk.Label(ehead, text="Hash chain", style="Section.TLabel").pack(side="left")
        ttk.Label(ehead, text="seq · event · hash", style="CardLabel.TLabel").pack(side="right")
        self.audit_tree = self._make_tree(
            events_card,
            columns=("seq", "event", "hash", "created"),
            headings=("Seq", "Event", "Hash", "Created"),
            widths=(70, 180, 180, 190),
            height=12,
        )
        self.audit_tree.bind("<<TreeviewSelect>>", lambda _event: self._show_selected_event())

        payload_card = card(paned)
        paned.add(payload_card, weight=2)
        phead = ttk.Frame(payload_card, style="Surface.TFrame")
        phead.pack(fill="x", padx=14, pady=(12, 4))
        ttk.Label(phead, text="Event payload", style="Section.TLabel").pack(side="left")
        self.payload_text = tk.Text(
            payload_card,
            wrap="word",
            bd=0,
            relief="flat",
            bg=COLORS["surface_alt"],
            fg=COLORS["text"],
            padx=12,
            pady=8,
            font=(self.mono, 9),
            state="disabled",
        )
        self.payload_text.pack(fill="both", expand=True, padx=14, pady=(0, 12))

    # -- editor helpers ---------------------------------------------------
    def _scroll_editor(self, *args: Any) -> None:
        self.editor.yview(*args)
        self.gutter.yview(*args)

    def _sync_editor_scroll(self, first: str, last: str, scrollbar: Any) -> None:
        scrollbar.set(first, last)
        self.gutter.yview_moveto(first)

    def _on_editor_modified(self) -> None:
        if self.editor.edit_modified():
            self._refresh_gutter()

    def _refresh_gutter(self) -> None:
        count = int(self.editor.index("end-1c").split(".")[0])
        self.gutter.configure(state="normal")
        self.gutter.delete("1.0", "end")
        self.gutter.insert("1.0", "\n".join(str(number) for number in range(1, count + 1)))
        self.gutter.configure(state="disabled")
        self.gutter.yview_moveto(self.editor.yview()[0])
        self.line_count_var.set(f"{count} lines")
        self.editor.edit_modified(False)

    def _set_editor(self, text: str) -> None:
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", text)
        self.editor.edit_reset()
        self.editor.edit_modified(False)
        self._refresh_gutter()
        self.editor.see("1.0")

    def _current_dsl(self) -> str:
        return self.editor.get("1.0", "end-1c") + "\n"

    # -- status / errors --------------------------------------------------
    def _set_status(self, message: str, *, error: bool = False) -> None:
        self.status_var.set(message)
        if self.status_label is not None:
            self.status_label.configure(fg=COLORS["danger"] if error else COLORS["muted"])

    def _set_connection(self, ok: bool) -> None:
        self._connected = ok
        if ok:
            self.connection_var.set("● API connected")
            self.connection_label.configure(fg="#4ade80")
        else:
            self.connection_var.set("● API offline")
            self.connection_label.configure(fg="#f87171")

    def _api(self, fn: Any, *args: Any, quiet: bool = False, **kwargs: Any) -> Any:
        """Call the API through the view, turning failures into status + dialog."""
        from tkinter import messagebox

        # A replay re-executes the DAG; the watch cursor is what tells the
        # operator the click landed instead of a frozen window.
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        try:
            result = fn(*args, **kwargs)
        except ApiError as exc:
            # status 0 means the request never reached the API (transport or
            # JSON failure); an HTTP status means the API answered and refused.
            self._set_connection(exc.status != 0)
            self._set_status(str(exc), error=True)
            if not quiet:
                messagebox.showerror("Request failed", str(exc), parent=self.root)
            return None
        except ValueError as exc:
            self._set_connection(True)
            self._set_status(str(exc), error=True)
            if not quiet:
                messagebox.showerror("Cannot continue", str(exc), parent=self.root)
            return None
        finally:
            self.root.configure(cursor="")
        self._set_connection(True)
        return result

    # -- refresh actions --------------------------------------------------
    def refresh_all(self, quiet: bool = False) -> None:
        profile = self._api(self.view.profile, quiet=quiet)
        if profile is not None:
            self.user_var.set(profile)
        if self._api(self.view.metrics, quiet=quiet) is not None:
            self._render_metrics()
        if self._api(self.view.runs, quiet=quiet) is not None:
            self.render_runs()
            self.render_overview_runs()
            self.update_audit_choices()
        if self._api(self.view.approvals, quiet=quiet) is not None:
            self.render_approvals()
        if self._api(self.view.library, quiet=quiet) is not None:
            self.render_library()
        self._render_metrics()
        self._reload_selected_timeline()
        if self._connected:
            self._set_status(
                f"{len(self.view.run_rows)} run(s) · "
                f"{self.view.pending_approvals()} pending approval(s)"
            )

    def _render_metrics(self) -> None:
        data = self.view.metrics_data
        self.card_vars["runs"].set(str(data.get("total", "—")))
        rate = data.get("success_rate")
        self.card_vars["success"].set(f"{rate:g}%" if isinstance(rate, (int, float)) else "—")
        p95 = data.get("p95_latency_ms")
        self.card_vars["p95"].set(f"{p95:g} ms" if isinstance(p95, (int, float)) else "—")
        self.card_vars["pending"].set(str(self.view.pending_approvals()))

    def render_overview_runs(self) -> None:
        self.overview_runs.delete(*self.overview_runs.get_children())
        for row in self.view.run_rows[:8]:
            self.overview_runs.insert(
                "",
                "end",
                iid=str(row.get("id", "")),
                values=(
                    row.get("status", ""),
                    row.get("workflow_name") or str(row.get("workflow_id", ""))[:8],
                    f"v{row.get('version', 0)}",
                    row.get("started_at", ""),
                    str(row.get("id", ""))[:12],
                ),
                tags=(str(row.get("status", "")),),
            )

    def render_runs(self, select: str = "", *, preserve: bool = True) -> None:
        current = select or (self.runs_tree.selection()[0] if self.runs_tree.selection() else "")
        self.runs_tree.delete(*self.runs_tree.get_children())
        status = "" if self.status_filter.get() == "All" else self.status_filter.get()
        rows = self.view.filter_runs(status=status, needle=self.run_search.get())
        for row in rows:
            self.runs_tree.insert(
                "",
                "end",
                iid=str(row.get("id", "")),
                values=(
                    row.get("status", ""),
                    row.get("workflow_name") or str(row.get("workflow_id", ""))[:8],
                    f"v{row.get('version', 0)}",
                    row.get("triggered_by", ""),
                    row.get("started_at", ""),
                    row.get("finished_at", "") or "—",
                    str(row.get("id", ""))[:12],
                ),
                tags=(str(row.get("status", "")),),
            )
        if current and self.runs_tree.exists(current):
            self.runs_tree.selection_set(current)
            self.runs_tree.see(current)
        elif preserve and rows:
            first = str(rows[0].get("id", ""))
            if first:
                self.runs_tree.selection_set(first)
                self.runs_tree.see(first)

    def refresh_runs(self) -> None:
        if self._api(self.view.runs) is None:
            return
        self._selected_run = ""
        self.render_runs(preserve=False)
        self.render_overview_runs()
        self.update_audit_choices()
        self._set_status(f"{len(self.view.run_rows)} run(s)")

    def open_run_from(self, tree: Any) -> None:
        selection = tree.selection()
        if not selection:
            return
        execution_id = selection[0]
        if tree is self.overview_runs:
            self.notebook.select(self.runs_tab)
            if self.runs_tree.exists(execution_id):
                self.runs_tree.selection_set(execution_id)
            else:
                self.render_runs(select=execution_id)
            self._selected_run = ""
        self.open_run(execution_id)

    def _reload_selected_timeline(self) -> None:
        """Reload the open run after a refresh, even if its selection is unchanged."""
        execution_id = self._selected_run
        if execution_id:
            self._selected_run = ""
            self.open_run(execution_id)

    def open_run(self, execution_id: str) -> None:
        if not execution_id or execution_id == self._selected_run:
            return
        if self._api(self.view.timeline, execution_id, quiet=True) is None:
            return
        self._selected_run = execution_id
        self.steps_tree.delete(*self.steps_tree.get_children())
        for step in self.view.step_rows:
            self.steps_tree.insert(
                "",
                "end",
                values=(
                    step.get("node_id", ""),
                    step.get("state", ""),
                    step.get("attempt", 1),
                    step.get("error", ""),
                ),
                tags=(str(step.get("state", "")),),
            )
        run = next((row for row in self.view.run_rows if str(row.get("id")) == execution_id), {})
        self.run_detail_var.set(
            f"{run.get('workflow_name') or execution_id[:8]} · "
            f"{run.get('status', '')} · v{run.get('version', 0)}"
        )
        self._set_status(f"run {execution_id[:8]} · {len(self.view.step_rows)} step(s)")

    def _selected_run_id(self) -> str:
        selection = self.runs_tree.selection()
        return selection[0] if selection else ""

    def replay_selected(self) -> None:
        from tkinter import messagebox

        execution_id = self._selected_run_id()
        if not execution_id:
            self._set_status("select a run first", error=True)
            return
        lines = self._api(self.view.replay, execution_id)
        if lines is None:
            return
        self._set_status(lines[1])
        replay_id = str(self.view.last_replay.get("replay_execution_id", ""))
        if self._api(self.view.runs, quiet=True) is not None:
            self.render_runs(select=replay_id)
            self.render_overview_runs()
            self.update_audit_choices()
        messagebox.showinfo("Replay finished", "\n".join(lines), parent=self.root)

    # -- workflow actions -------------------------------------------------
    def _remember_workflow_label(self) -> None:
        if self.view.workflow_id:
            self.workflow_var.set(
                f"{self.view.workflow_name or 'workflow'} · v{self.view.version} · "
                f"{self.view.workflow_id[:8]}"
            )
        elif self.view.workflow_name:
            self.workflow_var.set(f"{self.view.workflow_name} · unsaved")

    def new_workflow(self) -> None:
        self.view.workflow_id = ""
        self.view.version = 0
        self.view.workflow_name = ""
        self.view.finding_rows = []
        self.view.dry_run_rows = []
        self._set_editor(STARTER_DSL)
        self.workflow_var.set("new · unsaved")
        self.validation_var.set("Not validated")
        self.summary_var.set("")
        self.render_findings()
        self.render_policy(clear=True)
        self._set_status("new workflow — validate, save, then run")

    def focus_library(self) -> None:
        self.notebook.select(self.workflow_tab)
        self.library_combo.focus_set()

    def validate_workflow(self) -> None:
        lines = self._api(self.view.validate, self._current_dsl())
        if lines is None:
            return
        self.render_findings()
        self.render_policy(clear=True)
        valid = self.view.status == "valid"
        self.validation_var.set("Valid" if valid else "Invalid")
        self._remember_workflow_label()
        self._set_status(
            lines[0] if len(lines) == 1 else f"{len(lines)} finding(s)", error=not valid
        )

    def dry_run_workflow(self) -> None:
        lines = self._api(self.view.dry_run, self._current_dsl())
        if lines is None:
            return
        self.render_findings()
        self.render_policy()
        if self.view.status == "invalid":
            self.validation_var.set("Invalid")
            self._set_status(lines[0], error=True)
            return
        ready = bool(self.view.last_report.get("safe_to_run"))
        self.validation_var.set("Dry run ready" if ready else "Dry run blocked")
        counts = self.view.last_report.get("counts", {})
        self.summary_var.set(
            " · ".join(f"{verdict} {count}" for verdict, count in sorted(counts.items()))
            + f" · safe={ready}"
        )
        self._set_status(self.validation_var.get(), error=not ready)

    def save_workflow(self) -> None:
        lines = self._api(self.view.save, self._current_dsl())
        if lines is None:
            return
        self._remember_workflow_label()
        self._set_status(lines[0])
        self.refresh_library()

    def run_workflow(self) -> None:
        lines = self._api(self.view.run)
        if lines is None:
            return
        self._set_status(lines[0])
        self.notebook.select(self.runs_tab)
        self._selected_run = ""
        if self._api(self.view.runs, quiet=True) is not None:
            self.render_runs(select=self.view.execution_id)
            self.render_overview_runs()
            self.update_audit_choices()

    def render_findings(self) -> None:
        self.findings_tree.delete(*self.findings_tree.get_children())
        for error in self.view.finding_rows:
            location = f"{error.get('line', 1)}:{error.get('column', 1)}"
            self.findings_tree.insert(
                "",
                "end",
                values=(location, error.get("stage", ""), error.get("message", "")),
                tags=("FAILED",),
            )

    def render_policy(self, *, clear: bool = False) -> None:
        self.policy_tree.delete(*self.policy_tree.get_children())
        if clear:
            self.summary_var.set("")
            return
        for node in self.view.dry_run_rows:
            verdict = str(node.get("verdict", ""))
            explanation = (node.get("explanation") or {}).get("headline", "")
            self.policy_tree.insert(
                "",
                "end",
                values=(
                    node.get("node_id", ""),
                    node.get("node_type", ""),
                    verdict,
                    explanation,
                ),
                tags=(verdict,),
            )

    # -- library ----------------------------------------------------------
    def render_library(self) -> None:
        self._library_choices = []
        for workflow in self.view.workflow_rows:
            for version in workflow.get("versions", []):
                label = f"{workflow.get('name', '?')} · v{version['version']}"
                self._library_choices.append(
                    (label, str(workflow.get("id", "")), int(version["version"]))
                )
        self.library_combo.configure(values=[choice[0] for choice in self._library_choices])
        if self._library_choices and not self.library_combo.get():
            self.library_combo.current(0)

    def refresh_library(self) -> None:
        if self._api(self.view.library) is None:
            return
        self.render_library()

    def load_selected(self) -> None:
        index = self.library_combo.current()
        if index < 0 or index >= len(self._library_choices):
            self._set_status("choose a saved workflow first", error=True)
            return
        label, workflow_id, version = self._library_choices[index]
        dsl_text = self._api(self.view.load, workflow_id, version)
        if dsl_text is None:
            return
        self._set_editor(dsl_text)
        self._remember_workflow_label()
        self.validation_var.set("Loaded")
        self.summary_var.set("")
        self.render_findings()
        self.render_policy(clear=True)
        self._set_status(f"loaded {label}")

    # -- approvals --------------------------------------------------------
    def refresh_approvals(self) -> None:
        if self._api(self.view.approvals) is None:
            return
        self.render_approvals()
        self._set_status(
            f"{len(self.view.approval_rows)} approval(s) · {self.view.pending_approvals()} pending"
        )

    def render_approvals(self) -> None:
        current = self.approval_tree.selection()[0] if self.approval_tree.selection() else ""
        self.approval_tree.delete(*self.approval_tree.get_children())
        for row in self.view.approval_rows:
            iid = f"{row.get('execution_id', '')}::{row.get('node_id', '')}"
            self.approval_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    row.get("status", ""),
                    row.get("node_id", ""),
                    row.get("quorum", 1),
                    str(row.get("execution_id", ""))[:12],
                    row.get("expires_at", ""),
                ),
                tags=(str(row.get("status", "")),),
            )
        target = current if current and self.approval_tree.exists(current) else ""
        if not target and self.view.approval_rows:
            first_row = self.view.approval_rows[0]
            target = f"{first_row.get('execution_id', '')}::{first_row.get('node_id', '')}"
        if target:
            self.approval_tree.selection_set(target)
            self.approval_tree.see(target)
            self._show_approval(target)

    def _show_selected_approval(self) -> None:
        selection = self.approval_tree.selection()
        if selection:
            self._show_approval(selection[0])

    def _show_approval(self, iid: str) -> None:
        execution_id, _, node_id = iid.partition("::")
        row = next(
            (
                request
                for request in self.view.approval_rows
                if str(request.get("execution_id")) == execution_id
                and str(request.get("node_id")) == node_id
            ),
            None,
        )
        if row is None:
            return
        self.approval_detail["execution"].set(execution_id[:12])
        self.approval_detail["node"].set(str(row.get("node_id", "")))
        self.approval_detail["quorum"].set(str(row.get("quorum", 1)))
        self.approval_detail["status"].set(str(row.get("status", "")))
        self.approval_detail["expires"].set(str(row.get("expires_at", "")))
        self._set_decide_enabled(row.get("status") == "PENDING")

    def _set_decide_enabled(self, enabled: bool) -> None:
        for button in (self.approve_button, self.reject_button):
            if enabled:
                button.state(["!disabled"])
            else:
                button.state(["disabled"])

    def decide(self, verdict: str) -> None:
        selection = self.approval_tree.selection()
        if not selection:
            self._set_status("select an approval first", error=True)
            return
        execution_id, _, node_id = selection[0].partition("::")
        reason = self.reason_text.get("1.0", "end-1c").strip()
        message = self._api(self.view.decide, execution_id, node_id, verdict, reason=reason)
        if message is None:
            return
        self.reason_text.delete("1.0", "end")
        self._set_status(message)
        self.refresh_approvals()

    # -- audit ------------------------------------------------------------
    def update_audit_choices(self, select: str = "") -> None:
        self.audit_ids = [str(row.get("id", "")) for row in self.view.run_rows]
        self.audit_combo.configure(
            values=[
                f"{str(row.get('id', ''))[:12]} · {row.get('status', '')} · "
                f"{row.get('workflow_name') or ''} v{row.get('version', 0)}"
                for row in self.view.run_rows
            ]
        )
        if select and select in self.audit_ids:
            self.audit_combo.current(self.audit_ids.index(select))
        elif self.audit_combo.current() < 0 and self.audit_ids:
            self.audit_combo.current(0)

    def load_audit(self) -> None:
        index = self.audit_combo.current()
        if index < 0 or index >= len(self.audit_ids):
            self._set_status("select a run to audit", error=True)
            return
        execution_id = self.audit_ids[index]
        if self._api(self.view.audit, execution_id) is None:
            return
        self.audit_tree.delete(*self.audit_tree.get_children())
        for event in self.view.audit_rows:
            self.audit_tree.insert(
                "",
                "end",
                iid=str(event.get("seq", "")),
                values=(
                    f"#{event.get('seq')}",
                    event.get("event", ""),
                    str(event.get("hash", ""))[:20],
                    event.get("created_at", ""),
                ),
            )
        self.audit_badge_var.set("not verified")
        self.audit_badge.configure(fg=COLORS["muted"])
        self._set_status(f"{len(self.view.audit_rows)} event(s) for {execution_id[:8]}")

    def verify_audit(self) -> None:
        index = self.audit_combo.current()
        if index < 0 or index >= len(self.audit_ids):
            self._set_status("select a run to verify", error=True)
            return
        execution_id = self.audit_ids[index]
        message = self._api(self.view.verify, execution_id)
        if message is None:
            return
        ok = bool(self.view.verification.get("ok"))
        self.audit_badge_var.set("✓ chain verified" if ok else "✗ chain broken")
        self.audit_badge.configure(fg=COLORS["success"] if ok else COLORS["danger"])
        self._set_status(message, error=not ok)

    def _show_selected_event(self) -> None:
        selection = self.audit_tree.selection()
        if not selection:
            return
        event = next(
            (row for row in self.view.audit_rows if str(row.get("seq")) == selection[0]), None
        )
        if event is None:
            return
        self.payload_text.configure(state="normal")
        self.payload_text.delete("1.0", "end")
        self.payload_text.insert("1.0", _pretty_payload(event.get("payload")))
        self.payload_text.configure(state="disabled")

    def show_approvals(self) -> None:
        self.notebook.select(self.approvals_tab)
        self.refresh_approvals()

    def show_audit(self) -> None:
        self.notebook.select(self.audit_tab)
        self.update_audit_choices(select=self._selected_run_id())
        self.load_audit()

    def audit_selected(self) -> None:
        selection = self.runs_tree.selection()
        if not selection:
            self._set_status("select a run first", error=True)
            return
        self.show_audit()

    # -- misc -------------------------------------------------------------
    def _poll(self) -> None:
        try:
            if any(str(row.get("status")) == "RUNNING" for row in self.view.run_rows):
                if self._api(self.view.runs, quiet=True) is not None:
                    self.render_runs(preserve=True)
                    self.render_overview_runs()
                    self.update_audit_choices()
                    self._reload_selected_timeline()
        finally:
            self.root.after(self.POLL_MS, self._poll)

    def show_shortcuts(self) -> None:
        from tkinter import messagebox

        messagebox.showinfo(
            "Keyboard shortcuts",
            "Ctrl+N  new workflow\nCtrl+S  save and publish\nCtrl+R  run\n"
            "Ctrl+Enter  validate\nF5  refresh every screen\nCtrl+Q  quit",
            parent=self.root,
        )

    def show_about(self) -> None:
        from tkinter import messagebox

        messagebox.showinfo(
            "About Auto System Agent",
            "Auto System Agent — governed automation workbench\n\n"
            "The AI proposes; deterministic project-owned logic disposes;\n"
            "the repository remembers.\n\n"
            f"API: {getattr(self.view.client, 'base_url', '')}",
            parent=self.root,
        )

    def _bind_shortcuts(self) -> None:
        def shortcut(action: Any) -> Any:
            action()
            return "break"

        self.root.bind("<Control-n>", lambda _event: shortcut(self.new_workflow))
        self.root.bind("<Control-s>", lambda _event: shortcut(self.save_workflow))
        self.root.bind("<Control-r>", lambda _event: shortcut(self.run_workflow))
        self.root.bind("<Control-Return>", lambda _event: shortcut(self.validate_workflow))
        self.root.bind("<F5>", lambda _event: shortcut(self.refresh_all))
        self.root.bind("<Control-q>", lambda _event: shortcut(self.root.destroy))


def build_window(view: WorkbenchView, root: Any, *, dsl_text: str = "") -> WorkbenchWindow:
    """Build the five-tab workbench on ``root`` (requires a display)."""
    return WorkbenchWindow(view, root, dsl_text=dsl_text)


def run_workbench(base_url: str, token: str = "") -> None:  # pragma: no cover - needs a display
    """Open the workbench against ``base_url`` with a ready token.

    The launcher (item [105]) has already started the API and logged in, so
    this is the only call the entrypoint needs to make. Without a token the
    application asks for credentials in its own sign-in dialog, so the GUI is
    also usable on its own (``python -m ui.workbench``).
    """
    import tkinter as tk

    from .api_client import WorkbenchClient

    client = WorkbenchClient(base_url, token)
    if client.token:
        try:
            client.me()
        except ApiError as exc:
            if exc.status == 401:
                client.token = ""
    root = tk.Tk()
    apply_theme(root)
    if not client.token:
        root.withdraw()
        if not LoginDialog(root, client).show():
            root.destroy()
            return
        root.deiconify()
    WorkbenchWindow(WorkbenchView(client), root)
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
