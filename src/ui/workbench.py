"""Phase 8 item [77]: Tkinter workbench for workflow, run, approval and audit.

The client stays a client: every screen renders a report the API produced and
every button calls one endpoint. To keep that testable without a display, the
module is split in two:

- :class:`WorkbenchView` — pure state + formatting. It takes any object with
  the :class:`WorkbenchClient` methods (a fake in tests) and turns responses
  into the exact lines a table shows. All behavior lives here.
- :func:`build_window` — the Tk widget tree. It only binds buttons to the
  view and copies its rows into tables, so a widget bug cannot invent a
  verdict and the view can be exercised headlessly.

Screens: **Overview** (metrics, recent runs, activity), **Workflow** (editor,
validate/dry-run/save/run, policy preview), **Runs** (filterable executions +
timeline), **Approvals** (quorum, decisions), **Audit** (hash chain + verify)
and **Memory** (consent, stored plans, similarity search, erasure).
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

    def memory_consent(self) -> dict[str, Any]: ...

    def set_memory_consent(self, granted: bool) -> dict[str, Any]: ...

    def memory_items(self, state: str = "") -> dict[str, Any]: ...

    def memory_item(self, item_id: str) -> dict[str, Any]: ...

    def memory_search(
        self, query: str, *, top_k: int = 5, risk: str = "MEDIUM"
    ) -> dict[str, Any]: ...

    def memory_metrics(self) -> dict[str, Any]: ...

    def erase_memory_item(self, item_id: str) -> dict[str, Any]: ...


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
    memory_consent_data: dict[str, Any] = field(default_factory=dict)
    memory_items: list[dict[str, Any]] = field(default_factory=list)
    memory_detail: dict[str, Any] = field(default_factory=dict)
    memory_metrics_data: dict[str, Any] = field(default_factory=dict)
    memory_hits: list[dict[str, Any]] = field(default_factory=list)

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

    # -- memory -----------------------------------------------------------
    def can(self, permission: str) -> bool:
        """Whether the signed-in role has ``permission`` (unknown => allow)."""
        granted = self.profile_data.get("permissions")
        if not granted:
            return True
        return permission in granted

    def memory(self) -> list[str]:
        """Consent, stored plans and reuse metrics for the Memory screen."""
        self.memory_consent_data = dict(self.client.memory_consent())
        self.memory_items = list(self.client.memory_items().get("items", []))
        self.memory_metrics_data = dict(self.client.memory_metrics())
        self.status = f"{len(self.memory_items)} memory item(s)"
        return [
            f"{item.get('state', '')} {item.get('intent') or 'untitled'} "
            f"risk {item.get('risk', '?')} reputation {float(item.get('reputation', 0.0)):.2f} "
            f"{str(item.get('id', ''))[:8]}"
            for item in self.memory_items
        ]

    def reusable_memory(self) -> int:
        """How many stored plans may be reused without a sandbox proof."""
        return sum(1 for item in self.memory_items if item.get("reusable"))

    def set_memory_consent(self, granted: bool) -> str:
        """Grant or revoke the caller's own consent and report the new state."""
        report = self.client.set_memory_consent(granted)
        self.memory_consent_data = dict(report)
        self.status = "memory consent active" if report.get("active") else "memory consent off"
        return (
            "Memory consent granted — new plans may be stored."
            if report.get("active")
            else "Memory consent revoked — nothing new will be stored."
        )

    def load_memory_item(self, item_id: str) -> str:
        """Fetch one stored plan and return its canonical body for the pane."""
        report = self.client.memory_item(item_id)
        self.memory_detail = dict(report)
        return _pretty_payload(report.get("plan", {}))

    def filter_memory(self, state: str = "", needle: str = "") -> list[dict[str, Any]]:
        """Client-side filter over the loaded memory list (no request)."""
        text = needle.strip().lower()
        kept: list[dict[str, Any]] = []
        for item in self.memory_items:
            if state and item.get("state") != state:
                continue
            haystack = " ".join(str(value) for value in item.values()).lower()
            if text and text not in haystack:
                continue
            kept.append(item)
        return kept

    def search_memory(self, query: str) -> list[str]:
        """Similarity search; remembers the ranked hits for the result line."""
        if not query.strip():
            raise ValueError("type what the plan should do, then search")
        report = self.client.memory_search(query.strip())
        self.memory_hits = list(report.get("hits", []))
        self.status = f"{len(self.memory_hits)} memory hit(s)"
        return [
            f"#{hit.get('rank')} {str(hit.get('id', ''))[:8]} "
            f"score={float(hit.get('score', 0.0)):.3f} {hit.get('state', '')} "
            f"{hit.get('intent', '')}"
            for hit in self.memory_hits
        ]

    def erase_memory(self, item_id: str) -> str:
        """Erase one stored plan and drop it from the loaded list."""
        if not item_id:
            raise ValueError("select a memory item to erase")
        self.client.erase_memory_item(item_id)
        self.memory_items = [item for item in self.memory_items if item.get("id") != item_id]
        self.memory_detail = {}
        self.status = f"erased {item_id[:8]}"
        return f"Erased memory item {item_id[:8]}. The consent history is kept."


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
    """Pretty-print an audit payload or memory plan for a detail pane."""
    value = raw
    if isinstance(value, str):
        try:
            value = json.loads(value or "{}")
        except ValueError:
            return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, sort_keys=True)
    return str(value or "")


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
    """The application shell: header, six screens, status bar and menus."""

    #: Poll interval while a run is still RUNNING.
    POLL_MS = 2500

    #: Product label shown in the header and the About dialog.
    VERSION = "v1.0.0-rc1 · post-RC"

    def __init__(self, view: WorkbenchView, root: Any, *, dsl_text: str = "") -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.view = view
        self.root = root
        self.family, self.mono = apply_theme(root)
        self._selected_run = ""
        self._selected_memory = ""
        self._connected = False
        self._dirty = False
        self._busy = 0
        self._toasts: list[Any] = []
        self._empty: dict[Any, Any] = {}
        self._activity: list[str] = []
        self._library_choices: list[tuple[str, str, int]] = []
        self.audit_ids: list[str] = []
        self.card_vars: dict[str, Any] = {}
        self.status_label: Any = None
        self.toast_host: Any = None
        self._poll_job: str = ""

        width = min(1280, max(960, root.winfo_screenwidth() - 80))
        height = min(840, max(640, root.winfo_screenheight() - 120))
        root.title("Auto System Agent · Workbench")
        root.geometry(
            f"{width}x{height}+{max(0, (root.winfo_screenwidth() - width) // 2)}"
            f"+{max(0, (root.winfo_screenheight() - height) // 2 - 20)}"
        )
        root.minsize(min(1120, width), min(720, height))
        root.report_callback_exception = self._on_tk_error

        self.status_var = tk.StringVar(value="starting…")
        self.connection_var = tk.StringVar(value="● connecting…")
        self.user_var = tk.StringVar(value="signed in")
        self.initials_var = tk.StringVar(value="··")
        self.api_var = tk.StringVar(value=f"API · {getattr(view.client, 'base_url', '')}")
        self.workflow_var = tk.StringVar(value="no workflow saved")
        self.dirty_var = tk.StringVar(value="")
        self.validation_var = tk.StringVar(value="Not validated")
        self.summary_var = tk.StringVar(value="")
        self.run_detail_var = tk.StringVar(value="Select a run to see its timeline.")
        self.runs_count_var = tk.StringVar(value="")
        self.approval_badge_var = tk.StringVar(value="no requests")
        self.audit_badge_var = tk.StringVar(value="not verified")
        self.payload_title_var = tk.StringVar(value="Select an event")
        self.memory_consent_var = tk.StringVar(value="checking consent…")
        self.memory_summary_var = tk.StringVar(value="")
        self.memory_title_var = tk.StringVar(value="Select a stored plan")

        self._build_header()
        self._build_menus()
        self._build_statusbar()
        self._build_tabs(dsl_text)
        self._bind_shortcuts()
        self.refresh_all(quiet=True)
        self._poll_job = root.after(self.POLL_MS, self._poll)
        root.bind("<Destroy>", self._on_destroy, add="+")

    # -- chrome -----------------------------------------------------------
    def _build_header(self) -> None:
        tk = self.tk
        header = tk.Frame(self.root, bg=COLORS["header"], height=80)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)
        tk.Frame(self.root, bg=COLORS["header_border"], height=1).pack(fill="x")

        left = tk.Frame(header, bg=COLORS["header"])
        left.pack(side="left", padx=22, pady=14)
        brand = tk.Frame(left, bg=COLORS["accent"], width=36, height=36)
        brand.pack(side="left", padx=(0, 12), pady=2)
        brand.pack_propagate(False)
        tk.Label(
            brand,
            text="A",
            bg=COLORS["accent"],
            fg="#ffffff",
            font=(self.family, 16, "bold"),
        ).pack(expand=True)
        titles = tk.Frame(left, bg=COLORS["header"])
        titles.pack(side="left")
        tk.Label(
            titles,
            text="Auto System Agent",
            bg=COLORS["header"],
            fg=COLORS["header_text"],
            font=(self.family, 17, "bold"),
        ).pack(anchor="w")
        tk.Label(
            titles,
            text=f"Governed automation workbench · {self.VERSION}",
            bg=COLORS["header"],
            fg=COLORS["header_muted"],
            font=(self.family, 9),
        ).pack(anchor="w")

        right = tk.Frame(header, bg=COLORS["header"])
        right.pack(side="right", padx=22)
        self.ttk.Button(
            right, text="Refresh", style="HeaderAction.TButton", command=self.refresh_all
        ).pack(side="left", padx=(12, 0))
        self.user_chip = tk.Frame(right, bg=COLORS["header_border"], padx=8, pady=4)
        self.user_chip.pack(side="left", padx=(0, 0))
        tk.Label(
            self.user_chip,
            textvariable=self.initials_var,
            bg=COLORS["accent_soft"],
            fg=COLORS["accent"],
            font=(self.family, 8, "bold"),
            width=3,
        ).pack(side="left", padx=(0, 8))
        tk.Label(
            self.user_chip,
            textvariable=self.user_var,
            bg=COLORS["header_border"],
            fg=COLORS["header_text"],
            font=(self.family, 9),
        ).pack(side="left")
        self.connection_label = tk.Label(
            right,
            textvariable=self.connection_var,
            bg=COLORS["header_border"],
            fg=COLORS["header_muted"],
            font=(self.family, 9),
            padx=10,
            pady=4,
        )
        self.connection_label.pack(side="left", padx=(0, 8))

    def _build_statusbar(self) -> None:
        tk, ttk = self.tk, self.ttk
        bar = tk.Frame(self.root, bg=COLORS["surface"])
        bar.pack(side="bottom", fill="x")
        tk.Frame(self.root, bg=COLORS["border"], height=1).pack(side="bottom", fill="x")
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
        self.busy_bar = ttk.Progressbar(inner, mode="indeterminate", length=90)
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
        ).pack(side="right", padx=(12, 0))

        # Transient success notices stack above the status bar instead of
        # interrupting the operator with a modal dialog.
        self.toast_host = tk.Frame(self.root, bg=COLORS["bg"])
        self.toast_host.place(relx=1.0, rely=1.0, anchor="se", x=-24, y=-44)

    def _toast(self, message: str, *, kind: str = "success", timeout: int = 4200) -> None:
        """Show a short-lived, click-to-dismiss notice in the bottom-right."""
        if self.toast_host is None:
            return
        tk = self.tk
        accent = {
            "success": COLORS["success"],
            "info": COLORS["accent"],
            "warning": COLORS["warning"],
            "error": COLORS["danger"],
        }.get(kind, COLORS["accent"])
        frame = tk.Frame(self.toast_host, bg=COLORS["toast"])
        frame.pack(side="bottom", pady=(6, 0), anchor="e")
        tk.Frame(frame, bg=accent, width=4).pack(side="left", fill="y")
        label = tk.Label(
            frame,
            text=message,
            bg=COLORS["toast"],
            fg=COLORS["toast_text"],
            font=(self.family, 9),
            justify="left",
            anchor="w",
            wraplength=340,
            padx=12,
            pady=9,
        )
        label.pack(side="left")
        self._toasts.append(frame)

        def dismiss(_event: Any = None) -> None:
            if frame in self._toasts:
                self._toasts.remove(frame)
                frame.destroy()

        for widget in (frame, label):
            widget.bind("<Button-1>", dismiss)
        self.root.after(timeout, dismiss)

    def _begin_busy(self) -> None:
        self._busy += 1
        if self._busy == 1:
            self.busy_bar.pack(side="left", padx=(12, 0))
            self.busy_bar.start(14)

    def _end_busy(self) -> None:
        self._busy = max(0, self._busy - 1)
        if self._busy == 0:
            self.busy_bar.stop()
            self.busy_bar.pack_forget()

    def _on_destroy(self, event: Any) -> None:
        """Cancel the pending poll so teardown does not log a dead callback."""
        if event.widget is not self.root or not self._poll_job:
            return
        try:
            self.root.after_cancel(self._poll_job)
        except self.tk.TclError:  # pragma: no cover - root already gone
            pass
        self._poll_job = ""

    def _on_tk_error(self, exc_type: Any, exc: Any, tb: Any) -> None:
        """Last-resort handler: report an unexpected Tk callback failure."""
        import traceback
        from tkinter import messagebox

        traceback.print_exception(exc_type, exc, tb)
        self._set_status(f"unexpected error: {exc}", error=True)
        self._toast(f"Unexpected error: {exc}", kind="error", timeout=6000)
        if self.root.winfo_exists():
            messagebox.showerror("Unexpected error", str(exc), parent=self.root)

    def _build_menus(self) -> None:
        tk = self.tk
        bar = tk.Menu(self.root)
        file_menu = tk.Menu(bar, tearoff=0)
        file_menu.add_command(label="New workflow", accelerator="Ctrl+N", command=self.new_workflow)
        file_menu.add_command(label="Open workflow…", command=self.focus_library)
        file_menu.add_separator()
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
        for index, (label, attribute) in enumerate(
            (
                ("Overview", "overview_tab"),
                ("Workflow", "workflow_tab"),
                ("Runs", "runs_tab"),
                ("Approvals", "approvals_tab"),
                ("Audit", "audit_tab"),
                ("Memory", "memory_tab"),
            ),
            start=1,
        ):
            view_menu.add_command(
                label=label,
                accelerator=f"Alt+{index}",
                command=lambda tab=attribute: self.notebook.select(getattr(self, tab)),
            )
        view_menu.add_separator()
        view_menu.add_command(
            label="Focus run search", accelerator="Ctrl+F", command=self.focus_run_search
        )
        bar.add_cascade(label="View", menu=view_menu)

        help_menu = tk.Menu(bar, tearoff=0)
        help_menu.add_command(label="Keyboard shortcuts", command=self.show_shortcuts)
        help_menu.add_command(label="About Auto System Agent", command=self.show_about)
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
        self.memory_tab = ttk.Frame(self.notebook)
        for frame, label in (
            (self.overview_tab, "Overview"),
            (self.workflow_tab, "Workflow"),
            (self.runs_tab, "Runs"),
            (self.approvals_tab, "Approvals"),
            (self.audit_tab, "Audit"),
            (self.memory_tab, "Memory"),
        ):
            self.notebook.add(frame, text=label)
        self._build_overview(self.overview_tab)
        self._build_workflow(self.workflow_tab, dsl_text)
        self._build_runs(self.runs_tab)
        self._build_approvals(self.approvals_tab)
        self._build_audit(self.audit_tab)
        self._build_memory(self.memory_tab)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _on_tab_changed(self, _event: Any = None) -> None:
        """Load the memory screen on first visit instead of at startup."""
        try:
            current = self.notebook.nametowidget(self.notebook.select())
        except (KeyError, self.tk.TclError):  # pragma: no cover - startup race
            return
        if current is self.memory_tab and not self.view.memory_metrics_data:
            self.refresh_memory()

    def _make_tree(
        self,
        parent: Any,
        *,
        columns: tuple[str, ...],
        headings: tuple[str, ...],
        widths: tuple[int, ...],
        height: int = 10,
        empty: str = "Nothing to show yet",
        expand: bool = True,
    ) -> Any:
        """A bordered Treeview with a scrollbar, status colours and empty state."""
        tk = self.tk
        holder = tk.Frame(
            parent,
            bg=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        holder.pack(fill="both", expand=expand)
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
        label = tk.Label(
            holder,
            text=empty,
            bg=COLORS["surface"],
            fg=COLORS["faint"],
            font=(self.family, 10),
            justify="center",
        )
        self._empty[tree] = label
        label.place(relx=0.5, rely=0.45, anchor="center")
        return tree

    def _set_empty(self, tree: Any, shown: bool) -> None:
        """Show or hide the empty-state caption drawn over a table."""
        label = self._empty.get(tree)
        if label is None:
            return
        if shown:
            label.place(relx=0.5, rely=0.45, anchor="center")
            label.lift()
        else:
            label.place_forget()

    def _insert_row(
        self,
        tree: Any,
        *,
        values: tuple[Any, ...],
        state: str = "",
        iid: str = "",
    ) -> str:
        """Insert one striped row whose text colour follows its state."""
        index = len(tree.get_children())
        tag = f"state::{state or 'NONE'}::{index % 2}"
        tree.tag_configure(
            tag,
            foreground=STATE_COLORS.get(state, COLORS["text"]),
            background=COLORS["row_alt"] if index % 2 else COLORS["surface"],
        )
        options: dict[str, Any] = {"values": values, "tags": (tag,)}
        if iid:
            options["iid"] = iid
        inserted = tree.insert("", "end", **options)
        self._set_empty(tree, False)
        return inserted

    # -- shared chrome ----------------------------------------------------
    def _screen_header(self, page: Any, title: str, subtitle: str) -> Any:
        """Title, one-line purpose and a right-aligned action slot."""
        ttk = self.ttk
        head = ttk.Frame(page)
        head.pack(fill="x")
        ttk.Label(head, text=title, style="Title.TLabel").pack(side="left")
        actions = ttk.Frame(head)
        actions.pack(side="right")
        ttk.Label(page, text=subtitle, style="Subtitle.TLabel").pack(anchor="w", pady=(1, 12))
        return actions

    # -- overview ---------------------------------------------------------
    def _build_overview(self, parent: Any) -> None:
        ttk, tk = self.ttk, self.tk
        page = ttk.Frame(parent)
        page.pack(fill="both", expand=True, padx=18, pady=16)
        actions = self._screen_header(
            page,
            "Overview",
            "Live state of the governed automation platform.",
        )
        ttk.Button(actions, text="Refresh", style="Ghost.TButton", command=self.refresh_all).pack(
            side="right"
        )
        ttk.Button(
            actions,
            text="Author workflow",
            style="Accent.TButton",
            command=lambda: self.notebook.select(self.workflow_tab),
        ).pack(side="right", padx=(0, 8))

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
            empty="No executions yet\nSave a workflow, then press Run.",
        )
        self.overview_runs.bind(
            "<<TreeviewSelect>>", lambda _event: self.open_run_from(self.overview_runs)
        )

        right = ttk.Frame(body)
        right.pack(side="left", fill="y", padx=(16, 0))
        activity = card(right)
        activity.pack(fill="x")
        activity_inner = ttk.Frame(activity, style="Surface.TFrame")
        activity_inner.pack(fill="both", padx=16, pady=14)
        ttk.Label(activity_inner, text="Recent activity", style="Section.TLabel").pack(anchor="w")
        self.activity_var = tk.StringVar(value="no runs yet")
        ttk.Label(activity_inner, textvariable=self.activity_var, style="CardLabel.TLabel").pack(
            anchor="w", pady=(1, 8)
        )
        self.activity_canvas = tk.Canvas(
            activity_inner,
            height=26,
            bg=COLORS["surface"],
            highlightthickness=0,
            bd=0,
        )
        self.activity_canvas.pack(fill="x")
        self.activity_canvas.bind("<Configure>", lambda _event: self._draw_activity())

        actions_card = card(right)
        actions_card.pack(fill="x", pady=(12, 0))
        inner = ttk.Frame(actions_card, style="Surface.TFrame")
        inner.pack(fill="both", padx=16, pady=14)
        ttk.Label(inner, text="Quick actions", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        for label, command in (
            ("Author a workflow", lambda: self.notebook.select(self.workflow_tab)),
            ("Browse runs", lambda: self.notebook.select(self.runs_tab)),
            ("Review approvals", self.show_approvals),
            ("Verify an audit chain", self.show_audit),
            ("Browse memory", lambda: self.notebook.select(self.memory_tab)),
        ):
            ttk.Button(inner, text=label, style="Ghost.TButton", command=command).pack(
                fill="x", pady=2
            )
        ttk.Label(right, text="Tip: F5 refreshes every screen.", style="Muted.TLabel").pack(
            anchor="w", pady=(10, 0)
        )

    # -- workflow ---------------------------------------------------------
    def _build_workflow(self, parent: Any, dsl_text: str) -> None:
        ttk, tk = self.ttk, self.tk
        page = ttk.Frame(parent)
        page.pack(fill="both", expand=True, padx=18, pady=14)
        head_actions = self._screen_header(
            page,
            "Workflow",
            "Author, validate and publish an immutable version, then run it.",
        )
        ttk.Button(head_actions, text="New", style="Ghost.TButton", command=self.new_workflow).pack(
            side="right", padx=(0, 6)
        )
        ttk.Button(
            head_actions, text="Open…", style="Ghost.TButton", command=self.focus_library
        ).pack(side="right", padx=(0, 6))
        ttk.Button(
            head_actions, text="Run", style="Accent.TButton", command=self.run_workflow
        ).pack(side="right", padx=(0, 8))

        toolbar = ttk.Frame(page)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Validate", command=self.validate_workflow).pack(side="left")
        ttk.Button(toolbar, text="Dry run", command=self.dry_run_workflow).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(toolbar, text="Save", command=self.save_workflow).pack(side="left", padx=(6, 0))
        tk.Label(
            toolbar,
            textvariable=self.dirty_var,
            bg=COLORS["bg"],
            fg=COLORS["warning"],
            font=(self.family, 9, "bold"),
        ).pack(side="left", padx=(10, 0))
        ttk.Label(toolbar, textvariable=self.workflow_var, style="Muted.TLabel").pack(side="right")

        library = ttk.Frame(page)
        library.pack(fill="x", pady=(10, 0))
        ttk.Label(library, text="Published library", style="Muted.TLabel").pack(side="left")
        self.library_combo = ttk.Combobox(library, state="readonly", width=48)
        self.library_combo.pack(side="left", padx=(8, 8))
        ttk.Button(library, text="Open", style="Ghost.TButton", command=self.load_selected).pack(
            side="left"
        )
        ttk.Button(
            library, text="Refresh", style="Ghost.TButton", command=self.refresh_library
        ).pack(side="left", padx=(6, 0))

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
        self.editor.bind("<Tab>", self._indent_editor)
        self.editor.bind("<ISO_Left_Tab>", self._dedent_editor)
        self.editor.bind("<Shift-Tab>", self._dedent_editor)
        self.editor.bind("<KeyRelease>", self._highlight_current_line, add="+")
        self.editor.bind("<ButtonRelease-1>", self._highlight_current_line, add="+")
        self.editor.tag_configure("current_line", background=COLORS["accent_soft"])

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
        self.validation_badge = tk.Label(
            vhead,
            textvariable=self.validation_var,
            bg=COLORS["surface_alt"],
            fg=COLORS["muted"],
            font=(self.family, 9, "bold"),
            padx=8,
            pady=2,
        )
        self.validation_badge.pack(side="right")
        self.findings_tree = self._make_tree(
            validation_card,
            columns=("location", "stage", "message"),
            headings=("Location", "Stage", "Message"),
            widths=(80, 90, 340),
            height=6,
            empty="No findings — nothing validated yet.",
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
            empty="Run a dry run to preview the policy verdict of each node.",
        )

        self._set_editor(dsl_text or STARTER_DSL)

    # -- runs -------------------------------------------------------------
    def _build_runs(self, parent: Any) -> None:
        ttk, tk = self.ttk, self.tk
        page = ttk.Frame(parent)
        page.pack(fill="both", expand=True, padx=18, pady=14)
        head_actions = self._screen_header(
            page,
            "Runs",
            "Every execution with its durable step timeline; replay re-runs the pinned version.",
        )
        ttk.Button(
            head_actions, text="Refresh", style="Ghost.TButton", command=self.refresh_runs
        ).pack(side="right")
        ttk.Button(
            head_actions, text="Replay", style="Ghost.TButton", command=self.replay_selected
        ).pack(side="right", padx=(0, 6))
        ttk.Button(
            head_actions, text="Audit", style="Ghost.TButton", command=self.audit_selected
        ).pack(side="right", padx=(0, 6))

        toolbar = ttk.Frame(page)
        toolbar.pack(fill="x")
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
        self.run_search_entry = ttk.Entry(toolbar, textvariable=self.run_search, width=26)
        self.run_search_entry.pack(side="left", padx=(6, 0))
        self.run_search_entry.bind("<KeyRelease>", lambda _event: self.render_runs())
        ttk.Button(
            toolbar, text="Clear", style="Ghost.TButton", command=self.clear_run_filters
        ).pack(side="left", padx=(6, 0))
        ttk.Label(toolbar, textvariable=self.runs_count_var, style="Muted.TLabel").pack(
            side="right"
        )

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
            empty="No executions yet.\nSave a workflow, then press Run.",
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
            empty="Select a run to inspect its steps.",
        )

    # -- approvals --------------------------------------------------------
    def _build_approvals(self, parent: Any) -> None:
        ttk, tk = self.ttk, self.tk
        page = ttk.Frame(parent)
        page.pack(fill="both", expand=True, padx=18, pady=14)
        head_actions = self._screen_header(
            page,
            "Approvals",
            "Gated steps wait here; every decision is final and lands on the audit chain.",
        )
        ttk.Button(
            head_actions, text="Refresh", style="Ghost.TButton", command=self.refresh_approvals
        ).pack(side="right")

        paned = ttk.Panedwindow(page, orient="horizontal")
        paned.pack(fill="both", expand=True, pady=(12, 0))
        left = card(paned)
        paned.add(left, weight=3)
        lhead = ttk.Frame(left, style="Surface.TFrame")
        lhead.pack(fill="x", padx=14, pady=(12, 4))
        ttk.Label(lhead, text="Requests", style="Section.TLabel").pack(side="left")
        self.approval_badge = tk.Label(
            lhead,
            textvariable=self.approval_badge_var,
            bg=COLORS["surface_alt"],
            fg=COLORS["muted"],
            font=(self.family, 9, "bold"),
            padx=8,
            pady=2,
        )
        self.approval_badge.pack(side="right")
        self.approval_tree = self._make_tree(
            left,
            columns=("status", "node", "quorum", "execution", "expires"),
            headings=("Status", "Node", "Quorum", "Execution", "Expires"),
            widths=(110, 140, 70, 130, 190),
            height=10,
            empty="No approval requests.\nGated steps appear here while they wait.",
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
        ttk.Label(inner, text="Reason (required to reject)", style="CardLabel.TLabel").pack(
            anchor="w", pady=(12, 4)
        )
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
        head_actions = self._screen_header(
            page,
            "Audit",
            "Append-only hash chain per execution; verify proves no event was rewritten.",
        )
        ttk.Button(
            head_actions,
            text="Verify chain",
            style="Accent.TButton",
            command=self.verify_audit,
        ).pack(side="right")
        toolbar = ttk.Frame(page)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="Run", style="Muted.TLabel").pack(side="left")
        self.audit_combo = ttk.Combobox(toolbar, state="readonly", width=58)
        self.audit_combo.pack(side="left", padx=(8, 8))
        ttk.Button(toolbar, text="Load", style="Ghost.TButton", command=self.load_audit).pack(
            side="left"
        )
        self.audit_badge = tk.Label(
            toolbar,
            textvariable=self.audit_badge_var,
            bg=COLORS["surface_alt"],
            fg=COLORS["muted"],
            font=(self.family, 9, "bold"),
            padx=8,
            pady=2,
        )
        self.audit_badge.pack(side="right")

        paned = ttk.Panedwindow(page, orient="horizontal")
        paned.pack(fill="both", expand=True, pady=(12, 0))
        events_card = card(paned)
        paned.add(events_card, weight=3)
        ehead = ttk.Frame(events_card, style="Surface.TFrame")
        ehead.pack(fill="x", padx=14, pady=(12, 4))
        ttk.Label(ehead, text="Hash chain", style="Section.TLabel").pack(side="left")
        self.copy_hash_button = ttk.Button(
            ehead,
            text="Copy hash",
            style="Ghost.TButton",
            command=self.copy_event_hash,
            width=11,
        )
        self.copy_hash_button.pack(side="right")
        self.copy_hash_button.state(["disabled"])
        self.audit_tree = self._make_tree(
            events_card,
            columns=("seq", "event", "hash", "created"),
            headings=("Seq", "Event", "Hash", "Created"),
            widths=(70, 180, 180, 190),
            height=12,
            empty="Load a run to inspect its audit chain.",
        )
        self.audit_tree.bind("<<TreeviewSelect>>", lambda _event: self._show_selected_event())

        payload_card = card(paned)
        paned.add(payload_card, weight=2)
        phead = ttk.Frame(payload_card, style="Surface.TFrame")
        phead.pack(fill="x", padx=14, pady=(12, 4))
        ttk.Label(phead, text="Event payload", style="Section.TLabel").pack(side="left")
        ttk.Label(phead, textvariable=self.payload_title_var, style="CardLabel.TLabel").pack(
            side="right"
        )
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
        self.payload_text.configure(state="normal")
        self.payload_text.insert("1.0", "Select an event to inspect its payload.")
        self.payload_text.configure(state="disabled")

    # -- memory -----------------------------------------------------------
    def _build_memory(self, parent: Any) -> None:
        ttk, tk = self.ttk, self.tk
        page = ttk.Frame(parent)
        page.pack(fill="both", expand=True, padx=18, pady=14)
        head_actions = self._screen_header(
            page,
            "Memory",
            "Consent-gated reuse: scrubbed plans stay quarantined until a sandbox proof.",
        )
        ttk.Button(
            head_actions, text="Refresh", style="Ghost.TButton", command=self.refresh_memory
        ).pack(side="right")

        consent = card(page)
        consent.pack(fill="x")
        consent_inner = ttk.Frame(consent, style="Surface.TFrame")
        consent_inner.pack(fill="x", padx=16, pady=9)
        ttk.Label(consent_inner, text="Consent", style="Section.TLabel").pack(side="left")
        self.memory_consent_pill = tk.Label(
            consent_inner,
            textvariable=self.memory_consent_var,
            bg=COLORS["surface_alt"],
            fg=COLORS["muted"],
            font=(self.family, 9, "bold"),
            padx=8,
            pady=2,
        )
        self.memory_consent_pill.pack(side="left", padx=(10, 10))
        ttk.Label(
            consent_inner,
            text="Storing a plan requires your own memory:store consent.",
            style="CardLabel.TLabel",
        ).pack(side="left")
        self.consent_button = ttk.Button(
            consent_inner, text="Grant consent", command=self.toggle_memory_consent
        )
        self.consent_button.pack(side="right")

        cards = ttk.Frame(page)
        cards.pack(fill="x", pady=(12, 0))
        for key, title, hint in (
            ("items", "Stored plans", "scrubbed & quarantined"),
            ("reusable", "Reusable", "PUBLIC only"),
            ("hits", "Reuse hits", "answered from memory"),
            ("hit_rate", "Hit rate", "no generation needed"),
        ):
            frame = card(cards)
            frame.pack(side="left", fill="x", expand=True, padx=(0, 10))
            inner = ttk.Frame(frame, style="Surface.TFrame")
            inner.pack(fill="both", expand=True, padx=16, pady=12)
            var = tk.StringVar(value="—")
            ttk.Label(inner, textvariable=var, style="CardValue.TLabel").pack(anchor="w")
            ttk.Label(inner, text=title, style="Section.TLabel").pack(anchor="w", pady=(4, 0))
            ttk.Label(inner, text=hint, style="CardLabel.TLabel").pack(anchor="w")
            self.card_vars[f"memory_{key}"] = var

        filters = ttk.Frame(page)
        filters.pack(fill="x", pady=(12, 0))
        ttk.Label(filters, text="State", style="Muted.TLabel").pack(side="left")
        self.memory_state_filter = tk.StringVar(value="All")
        state_box = ttk.Combobox(
            filters,
            textvariable=self.memory_state_filter,
            state="readonly",
            width=14,
            values=("All", "QUARANTINED", "SANDBOX", "PUBLIC"),
        )
        state_box.pack(side="left", padx=(6, 12))
        state_box.bind("<<ComboboxSelected>>", lambda _event: self.render_memory())
        ttk.Label(filters, text="Filter", style="Muted.TLabel").pack(side="left")
        self.memory_filter = tk.StringVar()
        self.memory_filter_entry = ttk.Entry(filters, textvariable=self.memory_filter, width=34)
        self.memory_filter_entry.pack(side="left", padx=(6, 0))
        self.memory_filter_entry.bind("<KeyRelease>", lambda _event: self.render_memory())
        ttk.Button(
            filters, text="Find similar", style="Accent.TButton", command=self.search_memory
        ).pack(side="right")
        ttk.Label(filters, textvariable=self.memory_summary_var, style="Muted.TLabel").pack(
            side="right", padx=(0, 12)
        )

        paned = ttk.Panedwindow(page, orient="horizontal")
        paned.pack(fill="both", expand=True, pady=(10, 0))
        left = card(paned)
        paned.add(left, weight=3)
        lhead = ttk.Frame(left, style="Surface.TFrame")
        lhead.pack(fill="x", padx=14, pady=(12, 4))
        ttk.Label(lhead, text="Stored plans", style="Section.TLabel").pack(side="left")
        self.memory_count_var = tk.StringVar(value="")
        ttk.Label(lhead, textvariable=self.memory_count_var, style="CardLabel.TLabel").pack(
            side="right"
        )
        self.memory_tree = self._make_tree(
            left,
            columns=("state", "intent", "risk", "reputation", "reuses", "summary"),
            headings=("State", "Intent", "Risk", "Reputation", "Reused", "Summary"),
            widths=(100, 150, 70, 90, 70, 260),
            height=11,
            empty="No stored plans.\nGrant consent, then store a plan through the API.",
        )
        self.memory_tree.bind("<<TreeviewSelect>>", lambda _event: self._show_selected_memory())

        detail = card(paned)
        paned.add(detail, weight=2)
        inner = ttk.Frame(detail, style="Surface.TFrame")
        inner.pack(fill="both", expand=True, padx=16, pady=12)
        dhead = ttk.Frame(inner, style="Surface.TFrame")
        dhead.pack(fill="x")
        ttk.Label(dhead, text="Stored plan", style="Section.TLabel").pack(side="left")
        self.erase_button = ttk.Button(
            dhead, text="Erase…", style="Ghost.TButton", command=self.erase_selected_memory
        )
        self.erase_button.pack(side="right")
        self.erase_button.state(["disabled"])
        ttk.Label(
            inner,
            textvariable=self.memory_title_var,
            style="CardLabel.TLabel",
        ).pack(anchor="w", pady=(1, 8))

        meta = ttk.Frame(inner, style="Surface.TFrame")
        meta.pack(fill="x")
        self.memory_meta: dict[str, Any] = {}
        for index, (key, label) in enumerate(
            (
                ("state", "State"),
                ("risk", "Risk"),
                ("intent", "Intent"),
                ("reputation", "Reputation"),
                ("reuse_count", "Reused"),
                ("pii_findings", "PII scrubbed"),
                ("created_by", "Owner"),
                ("created_at", "Created"),
            )
        ):
            row, column = divmod(index, 2)
            cell = ttk.Frame(meta, style="Surface.TFrame")
            cell.grid(row=row, column=column, sticky="w", padx=(0, 14), pady=2)
            ttk.Label(cell, text=label, style="CardLabel.TLabel", width=12).pack(side="left")
            var = tk.StringVar(value="—")
            self.memory_meta[key] = var
            ttk.Label(cell, textvariable=var, style="Surface.TLabel").pack(side="left")

        ttk.Label(inner, text="Canonical plan", style="CardLabel.TLabel").pack(
            anchor="w", pady=(10, 4)
        )
        plan_holder = ttk.Frame(inner, style="Surface.TFrame")
        plan_holder.pack(fill="both", expand=True)
        self.memory_plan = tk.Text(
            plan_holder,
            wrap="none",
            bd=0,
            relief="flat",
            bg=COLORS["surface_alt"],
            fg=COLORS["text"],
            padx=10,
            pady=8,
            font=(self.mono, 9),
            state="disabled",
        )
        plan_scroll = ttk.Scrollbar(plan_holder, orient="vertical", command=self.memory_plan.yview)
        self.memory_plan.configure(yscrollcommand=plan_scroll.set)
        self.memory_plan.pack(side="left", fill="both", expand=True)
        plan_scroll.pack(side="right", fill="y")
        self.memory_plan.configure(state="normal")
        self.memory_plan.insert("1.0", "Select a stored plan to inspect its canonical body.")
        self.memory_plan.configure(state="disabled")

        ttk.Label(inner, text="History", style="CardLabel.TLabel").pack(anchor="w", pady=(10, 4))
        self.memory_events_tree = self._make_tree(
            inner,
            columns=("event", "detail", "created"),
            headings=("Event", "Detail", "Created"),
            widths=(110, 320, 170),
            height=4,
            empty="No history yet.",
            expand=False,
        )

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
            self._mark_dirty()

    def _indent_editor(self, _event: Any = None) -> str:
        """Tab inserts the two spaces the YAML DSL uses (no tab characters)."""
        self.editor.insert("insert", "  ")
        return "break"

    def _dedent_editor(self, _event: Any = None) -> str:
        """Shift+Tab removes up to one indent level from the current line."""
        line = self.editor.index("insert").split(".")[0]
        prefix = self.editor.get(f"{line}.0", f"{line}.2")
        remove = len(prefix) - len(prefix.lstrip(" "))
        if remove:
            self.editor.delete(f"{line}.0", f"{line}.{remove}")
        return "break"

    def _highlight_current_line(self, _event: Any = None) -> None:
        """A faint band behind the cursor line, like a code editor."""
        self.editor.tag_remove("current_line", "1.0", "end")
        line = self.editor.index("insert").split(".")[0]
        if self.editor.compare(f"{line}.end", ">", f"{line}.0"):
            self.editor.tag_add("current_line", f"{line}.0", f"{line}.end")

    def _mark_dirty(self) -> None:
        if self._dirty:
            return
        self._dirty = True
        self.dirty_var.set("● unsaved changes")
        self._update_title()

    def _mark_clean(self) -> None:
        self._dirty = False
        self.dirty_var.set("")
        self._update_title()

    def _update_title(self) -> None:
        if not self.root.winfo_exists():
            return
        marker = "● " if self._dirty else ""
        if self.view.workflow_name:
            version = f" v{self.view.version}" if self.view.version else ""
            self.root.title(f"{marker}{self.view.workflow_name}{version} · Auto System Agent")
        else:
            self.root.title(f"{marker}Auto System Agent · Workbench")

    def _set_validation_badge(self, text: str, kind: str = "muted") -> None:
        """Colour the validation chip by outcome (ok / warn / bad / muted)."""
        self.validation_var.set(text)
        background, foreground = {
            "ok": (COLORS["success_bg"], COLORS["success"]),
            "warn": (COLORS["warning_bg"], COLORS["warning"]),
            "bad": (COLORS["danger_bg"], COLORS["danger"]),
        }.get(kind, (COLORS["surface_alt"], COLORS["muted"]))
        self.validation_badge.configure(bg=background, fg=foreground)

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
        self._mark_clean()

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
        if not quiet:
            self._begin_busy()
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        try:
            result = fn(*args, **kwargs)
        except ApiError as exc:
            # status 0 means the request never reached the API (transport or
            # JSON failure); an HTTP status means the API answered and refused.
            self._set_connection(exc.status != 0)
            if exc.status == 401:
                self._set_status("session expired — restart the launcher to sign in", error=True)
                self._toast("Your session expired. Restart python main.py.", kind="error")
            else:
                self._set_status(str(exc), error=True)
            if not quiet:
                messagebox.showerror("Request failed", str(exc), parent=self.root)
            return None
        except ValueError as exc:
            self._set_connection(True)
            self._set_status(str(exc), error=True)
            self._toast(str(exc), kind="warning")
            if not quiet:
                messagebox.showerror("Cannot continue", str(exc), parent=self.root)
            return None
        finally:
            self.root.configure(cursor="")
            if not quiet:
                self._end_busy()
        self._set_connection(True)
        return result

    # -- refresh actions --------------------------------------------------
    def refresh_all(self, quiet: bool = False) -> None:
        profile = self._api(self.view.profile, quiet=quiet)
        if profile is not None:
            self.user_var.set(profile)
            username = str(self.view.profile_data.get("username", ""))
            self.initials_var.set((username[:2] or "··").upper())
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
            self._insert_row(
                self.overview_runs,
                iid=str(row.get("id", "")),
                values=(
                    row.get("status", ""),
                    row.get("workflow_name") or str(row.get("workflow_id", ""))[:8],
                    f"v{row.get('version', 0)}",
                    row.get("started_at", ""),
                    str(row.get("id", ""))[:12],
                ),
                state=str(row.get("status", "")),
            )
        self._set_empty(self.overview_runs, not self.view.run_rows)

        statuses = [str(row.get("status", "")) for row in self.view.run_rows]
        self._activity = statuses[::-1][-20:]
        completed = sum(1 for status in statuses if status == "COMPLETED")
        failed = sum(1 for status in statuses if status == "FAILED")
        running = sum(1 for status in statuses if status == "RUNNING")
        self.activity_var.set(
            f"{completed} completed · {failed} failed · {running} running"
            if statuses
            else "no runs yet"
        )
        self._draw_activity()

    def _draw_activity(self) -> None:
        """One coloured cell per recent run — an honest status strip."""
        canvas = self.activity_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 200)
        statuses = self._activity[-20:]
        if not statuses:
            canvas.create_text(
                2,
                13,
                text="Nothing has run yet.",
                anchor="w",
                fill=COLORS["faint"],
                font=(self.family, 8),
            )
            return
        gap = 2
        cell = min(14.0, (width - gap * (len(statuses) - 1)) / len(statuses))
        for index, status in enumerate(statuses):
            x = index * (cell + gap)
            canvas.create_rectangle(
                x,
                5,
                x + cell,
                21,
                fill=STATE_COLORS.get(status, COLORS["faint"]),
                outline="",
            )

    def render_runs(self, select: str = "", *, preserve: bool = True) -> None:
        current = select or (self.runs_tree.selection()[0] if self.runs_tree.selection() else "")
        self.runs_tree.delete(*self.runs_tree.get_children())
        status = "" if self.status_filter.get() == "All" else self.status_filter.get()
        rows = self.view.filter_runs(status=status, needle=self.run_search.get())
        for row in rows:
            self._insert_row(
                self.runs_tree,
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
                state=str(row.get("status", "")),
            )
        self.runs_count_var.set(f"{len(rows)} of {len(self.view.run_rows)} run(s)")
        filtered = status or self.run_search.get().strip()
        self._set_empty(
            self.runs_tree,
            not rows,
        )
        if not rows and filtered:
            label = self._empty.get(self.runs_tree)
            if label is not None:
                label.configure(text="No runs match this filter.")
        elif not rows:
            label = self._empty.get(self.runs_tree)
            if label is not None:
                label.configure(text="No executions yet.\nSave a workflow, then press Run.")
        if current and self.runs_tree.exists(current):
            self.runs_tree.selection_set(current)
            self.runs_tree.see(current)
        elif preserve and rows:
            first = str(rows[0].get("id", ""))
            if first:
                self.runs_tree.selection_set(first)
                self.runs_tree.see(first)

    def clear_run_filters(self) -> None:
        self.status_filter.set("All")
        self.run_search.set("")
        self.render_runs()
        self._set_status(f"{len(self.view.run_rows)} run(s)")

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
            self._insert_row(
                self.steps_tree,
                values=(
                    step.get("node_id", ""),
                    step.get("state", ""),
                    step.get("attempt", 1),
                    step.get("error", ""),
                ),
                state=str(step.get("state", "")),
            )
        self._set_empty(self.steps_tree, not self.view.step_rows)
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
            self._toast("Select a run before replaying.", kind="warning")
            return
        if not messagebox.askyesno(
            "Replay this run?",
            f"Replay {execution_id[:8]}?\n\nThis re-executes the pinned workflow version "
            "and writes a second execution with its own audit chain.",
            parent=self.root,
        ):
            return
        lines = self._api(self.view.replay, execution_id)
        if lines is None:
            return
        match = bool(self.view.last_replay.get("comparison", {}).get("match"))
        self._set_status(lines[1])
        self._toast(
            "Replay matched the original output." if match else "Replay differs — inspect the run.",
            kind="success" if match else "warning",
            timeout=6000,
        )
        replay_id = str(self.view.last_replay.get("replay_execution_id", ""))
        if self._api(self.view.runs, quiet=True) is not None:
            self.render_runs(select=replay_id)
            self.render_overview_runs()
            self.update_audit_choices()
            self.notebook.select(self.runs_tab)
            self.open_run(replay_id)

    # -- workflow actions -------------------------------------------------
    def _remember_workflow_label(self) -> None:
        if self.view.workflow_id:
            self.workflow_var.set(
                f"{self.view.workflow_name or 'workflow'} · v{self.view.version} · "
                f"{self.view.workflow_id[:8]}"
            )
        elif self.view.workflow_name:
            self.workflow_var.set(f"{self.view.workflow_name} · unsaved")
        self._update_title()

    def new_workflow(self) -> None:
        from tkinter import messagebox

        if self._dirty and not messagebox.askyesno(
            "Discard changes?",
            "The editor has unpublished changes. Start a new workflow anyway?",
            parent=self.root,
        ):
            return
        self.view.workflow_id = ""
        self.view.version = 0
        self.view.workflow_name = ""
        self.view.finding_rows = []
        self.view.dry_run_rows = []
        self._set_editor(STARTER_DSL)
        self.workflow_var.set("new · unsaved")
        self._set_validation_badge("Not validated")
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
        self._set_validation_badge("Valid" if valid else "Invalid", "ok" if valid else "bad")
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
            self._set_validation_badge("Invalid", "bad")
            self._set_status(lines[0], error=True)
            return
        ready = bool(self.view.last_report.get("safe_to_run"))
        self._set_validation_badge(
            "Dry run ready" if ready else "Dry run blocked", "ok" if ready else "warn"
        )
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
        self._mark_clean()
        self._remember_workflow_label()
        self._set_status(lines[0])
        self.refresh_library()
        self._toast(lines[0])

    def run_workflow(self) -> None:
        lines = self._api(self.view.run)
        if lines is None:
            return
        self._set_status(lines[0])
        self._toast(f"Run started · {self.view.execution_id[:8]}", kind="info")
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
            self._insert_row(
                self.findings_tree,
                values=(location, error.get("stage", ""), error.get("message", "")),
                state="FAILED",
            )
        self._set_empty(self.findings_tree, not self.view.finding_rows)

    def render_policy(self, *, clear: bool = False) -> None:
        self.policy_tree.delete(*self.policy_tree.get_children())
        if clear:
            self.summary_var.set("")
            self._set_empty(self.policy_tree, True)
            return
        for node in self.view.dry_run_rows:
            verdict = str(node.get("verdict", ""))
            explanation = (node.get("explanation") or {}).get("headline", "")
            self._insert_row(
                self.policy_tree,
                values=(
                    node.get("node_id", ""),
                    node.get("node_type", ""),
                    verdict,
                    explanation,
                ),
                state=verdict,
            )
        self._set_empty(self.policy_tree, not self.view.dry_run_rows)

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
        if self._dirty:
            from tkinter import messagebox

            if not messagebox.askyesno(
                "Discard changes?",
                "Opening a saved version replaces the unpublished editor content. Continue?",
                parent=self.root,
            ):
                return
        label, workflow_id, version = self._library_choices[index]
        dsl_text = self._api(self.view.load, workflow_id, version)
        if dsl_text is None:
            return
        self._set_editor(dsl_text)
        self._remember_workflow_label()
        self._set_validation_badge("Loaded")
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
            self._insert_row(
                self.approval_tree,
                iid=iid,
                values=(
                    row.get("status", ""),
                    row.get("node_id", ""),
                    row.get("quorum", 1),
                    str(row.get("execution_id", ""))[:12],
                    row.get("expires_at", ""),
                ),
                state=str(row.get("status", "")),
            )
        pending = self.view.pending_approvals()
        self.approval_badge_var.set(f"{pending} pending" if pending else "no pending requests")
        self.approval_badge.configure(
            bg=COLORS["warning_bg"] if pending else COLORS["surface_alt"],
            fg=COLORS["warning"] if pending else COLORS["muted"],
        )
        self._set_empty(self.approval_tree, not self.view.approval_rows)
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
        if verdict == REJECT and not reason:
            self._set_status("a rejection needs a reason", error=True)
            self._toast("Add a reason before rejecting.", kind="warning")
            self.reason_text.focus_set()
            return
        message = self._api(self.view.decide, execution_id, node_id, verdict, reason=reason)
        if message is None:
            return
        self.reason_text.delete("1.0", "end")
        self._set_status(message)
        self._toast(message, kind="success" if verdict == APPROVE else "info")
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
            self._insert_row(
                self.audit_tree,
                iid=str(event.get("seq", "")),
                values=(
                    f"#{event.get('seq')}",
                    event.get("event", ""),
                    str(event.get("hash", ""))[:20],
                    event.get("created_at", ""),
                ),
            )
        self._set_empty(self.audit_tree, not self.view.audit_rows)
        self.audit_badge_var.set("not verified")
        self.audit_badge.configure(bg=COLORS["surface_alt"], fg=COLORS["muted"])
        self.payload_title_var.set("Select an event")
        self.copy_hash_button.state(["disabled"])
        self.payload_text.configure(state="normal")
        self.payload_text.delete("1.0", "end")
        self.payload_text.insert("1.0", "Select an event to inspect its payload.")
        self.payload_text.configure(state="disabled")
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
        self.audit_badge.configure(
            bg=COLORS["success_bg"] if ok else COLORS["danger_bg"],
            fg=COLORS["success"] if ok else COLORS["danger"],
        )
        self._set_status(message, error=not ok)
        self._toast(message, kind="success" if ok else "error")

    def _show_selected_event(self) -> None:
        selection = self.audit_tree.selection()
        if not selection:
            return
        event = next(
            (row for row in self.view.audit_rows if str(row.get("seq")) == selection[0]), None
        )
        if event is None:
            return
        self.payload_title_var.set(f"#{event.get('seq')} · {event.get('event', '')}")
        self.copy_hash_button.state(["!disabled"])
        self.payload_text.configure(state="normal")
        self.payload_text.delete("1.0", "end")
        self.payload_text.insert("1.0", _pretty_payload(event.get("payload")))
        self.payload_text.configure(state="disabled")

    def copy_event_hash(self) -> None:
        """Copy the selected event's full hash for a report or ticket."""
        selection = self.audit_tree.selection()
        if not selection:
            return
        event = next(
            (row for row in self.view.audit_rows if str(row.get("seq")) == selection[0]), None
        )
        if event is None:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(str(event.get("hash", "")))
        self._toast("Event hash copied to the clipboard.", kind="info")

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

    # -- memory actions ---------------------------------------------------
    def refresh_memory(self) -> None:
        if self._api(self.view.memory) is None:
            return
        self._render_memory_consent()
        self._render_memory_metrics()
        self.render_memory()
        self._set_status(
            f"{len(self.view.memory_items)} stored plan(s) · "
            f"{self.view.reusable_memory()} ready to reuse"
        )

    def _render_memory_metrics(self) -> None:
        data = self.view.memory_metrics_data
        self.card_vars["memory_items"].set(str(len(self.view.memory_items)))
        self.card_vars["memory_reusable"].set(str(self.view.reusable_memory()))
        self.card_vars["memory_hits"].set(str(data.get("reuse_hits", 0)))
        rate = float(data.get("hit_rate", 0.0) or 0.0)
        self.card_vars["memory_hit_rate"].set(f"{rate * 100:.1f}%")

    def _render_memory_consent(self) -> None:
        active = bool(self.view.memory_consent_data.get("active"))
        self.memory_consent_var.set("consent active" if active else "consent off")
        self.memory_consent_pill.configure(
            bg=COLORS["success_bg"] if active else COLORS["warning_bg"],
            fg=COLORS["success"] if active else COLORS["warning"],
        )
        self.consent_button.configure(text="Revoke consent" if active else "Grant consent")

    def toggle_memory_consent(self) -> None:
        active = bool(self.view.memory_consent_data.get("active"))
        message = self._api(self.view.set_memory_consent, not active)
        if message is None:
            return
        self._render_memory_consent()
        self._set_status(message)
        self._toast(message, kind="success" if not active else "info")

    def render_memory(self) -> None:
        current = self.memory_tree.selection()[0] if self.memory_tree.selection() else ""
        self.memory_tree.delete(*self.memory_tree.get_children())
        state = "" if self.memory_state_filter.get() == "All" else self.memory_state_filter.get()
        rows = self.view.filter_memory(state=state, needle=self.memory_filter.get())
        for item in rows:
            self._insert_row(
                self.memory_tree,
                iid=str(item.get("id", "")),
                values=(
                    item.get("state", ""),
                    item.get("intent") or "—",
                    item.get("risk", ""),
                    f"{float(item.get('reputation', 0.0)):.1f}",
                    item.get("reuse_count", 0),
                    item.get("summary") or "—",
                ),
                state=str(item.get("state", "")),
            )
        self.memory_count_var.set(f"{len(rows)} of {len(self.view.memory_items)}")
        self._set_empty(self.memory_tree, not rows)
        label = self._empty.get(self.memory_tree)
        if label is not None and not rows:
            label.configure(
                text="No stored plans.\nGrant consent, then store a plan through the API."
                if not self.view.memory_items
                else "No plans match this filter."
            )
        if current and self.memory_tree.exists(current):
            self.memory_tree.selection_set(current)
        elif rows:
            self.memory_tree.selection_set(str(rows[0].get("id", "")))
        else:
            self._reset_memory_detail()

    def _reset_memory_detail(self) -> None:
        self._selected_memory = ""
        self.view.memory_detail = {}
        self.memory_title_var.set("Select a stored plan")
        for var in self.memory_meta.values():
            var.set("—")
        self.memory_plan.configure(state="normal")
        self.memory_plan.delete("1.0", "end")
        self.memory_plan.insert("1.0", "Select a stored plan to inspect its canonical body.")
        self.memory_plan.configure(state="disabled")
        self.memory_events_tree.delete(*self.memory_events_tree.get_children())
        self._set_empty(self.memory_events_tree, True)
        self.erase_button.state(["disabled"])

    def _show_selected_memory(self) -> None:
        selection = self.memory_tree.selection()
        if selection:
            self._show_memory_item(selection[0])

    def _show_memory_item(self, item_id: str) -> None:
        plan_text = self._api(self.view.load_memory_item, item_id, quiet=True)
        if plan_text is None:
            return
        detail = self.view.memory_detail
        self._selected_memory = item_id
        self.memory_title_var.set(f"{detail.get('intent') or 'untitled'} · {item_id[:12]}")
        values = {
            "state": detail.get("state", "—"),
            "risk": detail.get("risk", "—"),
            "intent": detail.get("intent") or "—",
            "reputation": f"{float(detail.get('reputation', 0.0)):.2f}",
            "reuse_count": str(detail.get("reuse_count", 0)),
            "pii_findings": str(detail.get("pii_findings", 0)),
            "created_by": detail.get("created_by") or "—",
            "created_at": detail.get("created_at", "—"),
        }
        for key, var in self.memory_meta.items():
            var.set(values.get(key, "—"))
        self.memory_plan.configure(state="normal")
        self.memory_plan.delete("1.0", "end")
        self.memory_plan.insert("1.0", plan_text)
        self.memory_plan.configure(state="disabled")

        self.memory_events_tree.delete(*self.memory_events_tree.get_children())
        for event in detail.get("events", []):
            self._insert_row(
                self.memory_events_tree,
                values=(
                    event.get("event", ""),
                    event.get("detail", ""),
                    event.get("created_at", ""),
                ),
            )
        self._set_empty(self.memory_events_tree, not detail.get("events"))
        self.erase_button.state(["!disabled"] if self.view.can("workflow:run") else ["disabled"])

    def search_memory(self) -> None:
        lines = self._api(self.view.search_memory, self.memory_filter.get())
        if lines is None:
            return
        if not self.view.memory_hits:
            self.memory_summary_var.set("no reusable plan matches")
            self._toast("No reusable plan matches that description.", kind="warning")
            return
        best = self.view.memory_hits[0]
        score = float(best.get("score", 0.0))
        self.memory_summary_var.set(f"{len(self.view.memory_hits)} hit(s) · best {score:.2f}")
        if self.memory_state_filter.get() != "All":
            self.memory_state_filter.set("All")
        self.render_memory()
        best_id = str(best.get("id", ""))
        if self.memory_tree.exists(best_id):
            self.memory_tree.selection_set(best_id)
            self.memory_tree.see(best_id)
        self._set_status(self.memory_summary_var.get())
        self._toast(
            f"Found {len(self.view.memory_hits)} reusable plan(s); best match {score:.2f}.",
            kind="success" if score > 0.5 else "info",
        )

    def erase_selected_memory(self) -> None:
        from tkinter import messagebox

        selection = self.memory_tree.selection()
        if not selection:
            self._set_status("select a stored plan first", error=True)
            self._toast("Select a stored plan to erase.", kind="warning")
            return
        item_id = selection[0]
        label = self.memory_title_var.get()
        if not messagebox.askyesno(
            "Erase stored plan?",
            f"Permanently erase {label}?\n\nThe plan and its history are removed; "
            "the consent record is kept.",
            icon="warning",
            parent=self.root,
        ):
            return
        message = self._api(self.view.erase_memory, item_id)
        if message is None:
            return
        self._selected_memory = ""
        self.refresh_memory()
        self._toast(message)

    # -- misc -------------------------------------------------------------
    def _poll(self) -> None:
        try:
            if any(str(row.get("status")) == "RUNNING" for row in self.view.run_rows):
                if self._api(self.view.runs, quiet=True) is not None:
                    self.render_runs(preserve=True)
                    self.render_overview_runs()
                    self.update_audit_choices()
                    self._reload_selected_timeline()
                if self._api(self.view.approvals, quiet=True) is not None:
                    self.render_approvals()
        finally:
            if self.root.winfo_exists():
                self._poll_job = self.root.after(self.POLL_MS, self._poll)

    def show_shortcuts(self) -> None:
        from tkinter import messagebox

        messagebox.showinfo(
            "Keyboard shortcuts",
            "Ctrl+N  new workflow\nCtrl+S  save and publish\nCtrl+R  run\n"
            "Ctrl+Enter  validate\nCtrl+F  focus the run search\n"
            "Tab / Shift+Tab  indent / dedent in the editor\n"
            "Alt+1…6  jump to a screen\nF5  refresh every screen\nCtrl+Q  quit",
            parent=self.root,
        )

    def show_about(self) -> None:
        from tkinter import messagebox

        messagebox.showinfo(
            "About Auto System Agent",
            "Auto System Agent — governed automation workbench\n"
            f"{self.VERSION}\n\n"
            "The AI proposes; deterministic project-owned logic disposes;\n"
            "the repository remembers.\n\n"
            "Overview · Workflow · Runs · Approvals · Audit · Memory\n\n"
            f"API: {getattr(self.view.client, 'base_url', '')}",
            parent=self.root,
        )

    def focus_run_search(self) -> None:
        self.notebook.select(self.runs_tab)
        self.run_search_entry.focus_set()
        self.run_search_entry.select_range(0, "end")

    def _bind_shortcuts(self) -> None:
        def shortcut(action: Any) -> Any:
            action()
            return "break"

        self.root.bind("<Control-n>", lambda _event: shortcut(self.new_workflow))
        self.root.bind("<Control-s>", lambda _event: shortcut(self.save_workflow))
        self.root.bind("<Control-r>", lambda _event: shortcut(self.run_workflow))
        self.root.bind("<Control-Return>", lambda _event: shortcut(self.validate_workflow))
        self.root.bind("<Control-f>", lambda _event: shortcut(self.focus_run_search))
        self.root.bind("<F5>", lambda _event: shortcut(self.refresh_all))
        self.root.bind("<Control-q>", lambda _event: shortcut(self.root.destroy))
        for index, attribute in enumerate(
            (
                "overview_tab",
                "workflow_tab",
                "runs_tab",
                "approvals_tab",
                "audit_tab",
                "memory_tab",
            ),
            start=1,
        ):
            self.root.bind(
                f"<Alt-Key-{index}>",
                lambda _event, name=attribute: shortcut(
                    lambda: self.notebook.select(getattr(self, name))
                ),
            )


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
