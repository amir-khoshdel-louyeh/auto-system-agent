import tkinter as tk
from tkinter import messagebox, scrolledtext
import queue
import os
import threading
import time
from typing import Callable

from auto_system_agent.agent import AutoSystemAgent
from auto_system_agent.models import StepStatus
from auto_system_agent.settings import LLMSettings, SettingsStore


BG_APP = "#f2f5f9"
BG_PANEL = "#ffffff"
BG_USER = "#d7ebff"
BG_AGENT = "#eef2f7"
BG_SYSTEM = "#fff4d9"
FG_PRIMARY = "#1f2937"
FG_MUTED = "#6b7280"
ACCENT = "#0f4c81"


class AgentChatGUI:
    """Minimal desktop chat interface for the Auto System Agent."""

    def __init__(self) -> None:
        self._settings_store = SettingsStore()
        self._settings = self._settings_store.load()
        self.agent = self._build_agent()
        self._is_busy = False
        self._ui_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._request_counter = 0
        self._active_request_id: int | None = None
        self._cancelled_request_ids: set[int] = set()
        self._request_started_at: float | None = None
        self._task_timeout_seconds = float(os.getenv("AUTO_AGENT_GUI_TASK_TIMEOUT", "45") or "45")
        self.root = tk.Tk()
        self.root.title("Auto System Agent")
        self.root.geometry("920x560")
        self.root.configure(bg=BG_APP)

        menu_bar = tk.Menu(self.root)
        tools_menu = tk.Menu(menu_bar, tearoff=0)
        tools_menu.add_command(label="Insert: install vlc", command=lambda: self._insert_tool_command("install vlc"))
        tools_menu.add_command(label="Insert: list files in .", command=lambda: self._insert_tool_command("list files in ."))
        tools_menu.add_command(label="Insert: create folder demo", command=lambda: self._insert_tool_command("create folder demo"))
        tools_menu.add_separator()
        tools_menu.add_command(label="Clear Timeline", command=self._clear_timeline)
        tools_menu.add_command(label="Clear Progress", command=self._reset_progress_panel)
        menu_bar.add_cascade(label="Tools", menu=tools_menu)

        settings_menu = tk.Menu(menu_bar, tearoff=0)
        settings_menu.add_command(label="LLM Settings", command=self._open_settings_dialog)
        settings_menu.add_command(label="App Options", command=self._open_options_dialog)
        menu_bar.add_cascade(label="Settings", menu=settings_menu)
        self.root.config(menu=menu_bar)

        content_frame = tk.Frame(self.root, bg=BG_APP)
        content_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(12, 8))

        self.chat_log = scrolledtext.ScrolledText(
            content_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("TkDefaultFont", 10),
            bg=BG_PANEL,
            fg=FG_PRIMARY,
            borderwidth=0,
            relief=tk.FLAT,
            padx=14,
            pady=10,
            insertbackground=FG_PRIMARY,
        )
        self._configure_chat_styles()
        self.chat_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        progress_frame = tk.Frame(content_frame, width=260, bg=BG_PANEL, highlightbackground="#d0d7e2", highlightthickness=1)
        progress_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(12, 0))
        progress_frame.pack_propagate(False)

        tk.Label(
            progress_frame,
            text="Execution Progress",
            font=("TkDefaultFont", 10, "bold"),
            fg=ACCENT,
            bg=BG_PANEL,
        ).pack(
            anchor="w", pady=(0, 6)
        )
        self.progress_list = tk.Listbox(
            progress_frame,
            height=16,
            bg="#f8fafc",
            fg=FG_PRIMARY,
            borderwidth=0,
            highlightthickness=0,
            selectbackground="#dbeafe",
            selectforeground=FG_PRIMARY,
        )
        self.progress_list.pack(fill=tk.BOTH, expand=True)
        self._step_progress_rows: dict[int, int] = {}

        tk.Label(
            progress_frame,
            text="Timeline",
            font=("TkDefaultFont", 10, "bold"),
            fg=ACCENT,
            bg=BG_PANEL,
        ).pack(anchor="w", pady=(8, 4))
        self.timeline_list = tk.Listbox(
            progress_frame,
            height=6,
            bg="#f8fafc",
            fg=FG_PRIMARY,
            borderwidth=0,
            highlightthickness=0,
            selectbackground="#dbeafe",
            selectforeground=FG_PRIMARY,
            font=("TkDefaultFont", 9),
        )
        self.timeline_list.pack(fill=tk.X)

        confirmation_frame = tk.Frame(
            progress_frame,
            bg=BG_PANEL,
            highlightbackground="#d0d7e2",
            highlightthickness=1,
            padx=8,
            pady=8,
        )
        confirmation_frame.pack(fill=tk.X, pady=(8, 0))

        tk.Label(
            confirmation_frame,
            text="Confirmation",
            font=("TkDefaultFont", 10, "bold"),
            fg=ACCENT,
            bg=BG_PANEL,
        ).pack(anchor="w")

        self.confirmation_status_label = tk.Label(
            confirmation_frame,
            text="No pending confirmation.",
            font=("TkDefaultFont", 9, "bold"),
            fg="#4b5563",
            bg=BG_PANEL,
            wraplength=230,
            justify=tk.LEFT,
        )
        self.confirmation_status_label.pack(anchor="w", pady=(4, 4))

        self.confirmation_details_label = tk.Label(
            confirmation_frame,
            text="",
            font=("TkDefaultFont", 9),
            fg=FG_MUTED,
            bg=BG_PANEL,
            wraplength=230,
            justify=tk.LEFT,
        )
        self.confirmation_details_label.pack(anchor="w")

        self.risk_badges_label = tk.Label(
            confirmation_frame,
            text="",
            font=("TkDefaultFont", 9, "bold"),
            fg="#1f2937",
            bg=BG_PANEL,
            wraplength=230,
            justify=tk.LEFT,
        )
        self.risk_badges_label.pack(anchor="w", pady=(4, 4))

        tk.Label(
            confirmation_frame,
            text="Command Preview",
            font=("TkDefaultFont", 9, "bold"),
            fg=FG_MUTED,
            bg=BG_PANEL,
        ).pack(anchor="w")

        preview_row = tk.Frame(confirmation_frame, bg=BG_PANEL)
        preview_row.pack(fill=tk.X, pady=(2, 0))
        self.command_preview_var = tk.StringVar(value="")
        self.command_preview_entry = tk.Entry(
            preview_row,
            textvariable=self.command_preview_var,
            state=tk.DISABLED,
            disabledforeground=FG_PRIMARY,
            bg="#f8fafc",
            relief=tk.FLAT,
            borderwidth=0,
            highlightbackground="#c7d2e0",
            highlightthickness=1,
            font=("TkDefaultFont", 9),
        )
        self.command_preview_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.copy_preview_button = tk.Button(
            preview_row,
            text="Copy",
            state=tk.DISABLED,
            command=self._copy_preview_text,
            bg="#2563eb",
            fg="#ffffff",
            activebackground="#1d4ed8",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=8,
        )
        self.copy_preview_button.pack(side=tk.LEFT, padx=(6, 0))

        bottom_frame = tk.Frame(self.root, bg=BG_APP)
        bottom_frame.pack(fill=tk.X, padx=12, pady=(0, 12))

        self.entry = tk.Entry(
            bottom_frame,
            font=("TkDefaultFont", 11),
            bg=BG_PANEL,
            fg=FG_PRIMARY,
            relief=tk.FLAT,
            borderwidth=0,
            highlightbackground="#c7d2e0",
            highlightthickness=1,
            insertbackground=FG_PRIMARY,
        )
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry.bind("<Return>", self._on_send)

        self.send_button = tk.Button(
            bottom_frame,
            text="Send",
            command=self._on_send,
            bg=ACCENT,
            fg="#ffffff",
            activebackground="#0c3a62",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=10,
        )
        self.send_button.pack(side=tk.LEFT, padx=(8, 0))

        self.confirm_button = tk.Button(
            bottom_frame,
            text="Confirm",
            command=self._on_confirm,
            state=tk.DISABLED,
            bg="#1d7a45",
            fg="#ffffff",
            activebackground="#17623a",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=10,
        )
        self.confirm_button.pack(side=tk.LEFT, padx=(8, 0))

        self.cancel_button = tk.Button(
            bottom_frame,
            text="Cancel",
            command=self._on_cancel,
            state=tk.DISABLED,
            bg="#b91c1c",
            fg="#ffffff",
            activebackground="#991b1b",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=10,
        )
        self.cancel_button.pack(side=tk.LEFT, padx=(8, 0))

        self._append_message("Agent", "Welcome. Type help to see example commands.")
        self._sync_confirmation_controls()
        self.root.after(50, self._drain_ui_queue)

    def _configure_chat_styles(self) -> None:
        self.chat_log.tag_configure(
            "who_you",
            foreground=ACCENT,
            font=("TkDefaultFont", 9, "bold"),
            justify="right",
            rmargin=26,
            spacing1=10,
            spacing3=2,
        )
        self.chat_log.tag_configure(
            "who_agent",
            foreground="#374151",
            font=("TkDefaultFont", 9, "bold"),
            justify="left",
            lmargin1=26,
            lmargin2=26,
            spacing1=10,
            spacing3=2,
        )
        self.chat_log.tag_configure(
            "who_system",
            foreground="#7c5e10",
            font=("TkDefaultFont", 9, "bold"),
            justify="left",
            lmargin1=26,
            lmargin2=26,
            spacing1=10,
            spacing3=2,
        )

        self.chat_log.tag_configure(
            "bubble_you",
            background=BG_USER,
            foreground=FG_PRIMARY,
            justify="right",
            rmargin=26,
            spacing3=8,
        )
        self.chat_log.tag_configure(
            "bubble_agent",
            background=BG_AGENT,
            foreground=FG_PRIMARY,
            justify="left",
            lmargin1=26,
            lmargin2=26,
            spacing3=8,
        )
        self.chat_log.tag_configure(
            "bubble_system",
            background=BG_SYSTEM,
            foreground=FG_PRIMARY,
            justify="left",
            lmargin1=26,
            lmargin2=26,
            spacing3=8,
        )

    def _append_message(self, speaker: str, message: str) -> None:
        if speaker == "You":
            who_tag = "who_you"
            body_tag = "bubble_you"
            label = "You"
        elif speaker == "System":
            who_tag = "who_system"
            body_tag = "bubble_system"
            label = "System"
        else:
            who_tag = "who_agent"
            body_tag = "bubble_agent"
            label = "Agent"

        self.chat_log.configure(state=tk.NORMAL)
        self.chat_log.insert(tk.END, f"{label}\n", who_tag)
        self.chat_log.insert(tk.END, f" {message}\n", body_tag)
        self.chat_log.insert(tk.END, "\n")
        self.chat_log.configure(state=tk.DISABLED)
        self.chat_log.see(tk.END)

    def _on_send(self, _event=None) -> None:
        user_input = self.entry.get().strip()
        if not user_input:
            return

        if self._is_busy or str(self.send_button["state"]) == "disabled":
            return

        self.entry.delete(0, tk.END)
        self._append_message("You", user_input)

        if user_input.lower() in {"exit", "quit"}:
            self._append_message("Agent", "Closing chat window.")
            self.root.after(300, self.root.destroy)
            return

        self._reset_progress_panel()
        self._start_background_task(
            lambda on_progress: self.agent.process(user_input, progress_callback=on_progress)
        )

    def _on_confirm(self) -> None:
        if self._is_busy:
            return

        if not self.agent.has_pending_confirmation():
            self._sync_confirmation_controls()
            return

        self._append_message("You", "yes")
        self._reset_progress_panel()
        self._start_background_task(self.agent.confirm_pending)

    def _on_cancel(self) -> None:
        if self._is_busy:
            if self._active_request_id is not None:
                self._cancelled_request_ids.add(self._active_request_id)
            self._append_message("System", "Cancelled running request.")
            self._active_request_id = None
            self._request_started_at = None
            self._set_busy(False)
            return

        if not self.agent.has_pending_confirmation():
            self._sync_confirmation_controls()
            return

        self._append_message("You", "no")
        response = self.agent.cancel_pending()
        if response:
            self._append_message("Agent", response)
        self._sync_confirmation_controls()

    def _sync_confirmation_controls(self) -> None:
        has_pending = self.agent.has_pending_confirmation()
        if self._is_busy:
            self.confirm_button.configure(state=tk.DISABLED)
            self.cancel_button.configure(state=tk.NORMAL)
            self._set_confirmation_status(
                "Request in progress...",
                "You can press Cancel to stop waiting for this request.",
                "#92400e",
            )
            return

        self.confirm_button.configure(state=tk.NORMAL if has_pending else tk.DISABLED)
        self.cancel_button.configure(state=tk.NORMAL if has_pending else tk.DISABLED)
        if has_pending:
            self._render_pending_confirmation_card()
            return

        self._set_confirmation_status("No pending confirmation.", "", "#4b5563")
        if hasattr(self, "risk_badges_label"):
            self.risk_badges_label.configure(text="")
        if hasattr(self, "command_preview_var"):
            self.command_preview_var.set("")
        if hasattr(self, "copy_preview_button"):
            self.copy_preview_button.configure(state=tk.DISABLED)

    def _render_pending_confirmation_card(self) -> None:
        details_fn = getattr(self.agent, "get_pending_confirmation_details", None)
        if callable(details_fn):
            details = details_fn()
        else:
            details = []

        if not details:
            summary = self.agent.get_pending_confirmation_summary()
            self._set_confirmation_status(
                "Pending confirmation",
                summary if summary else "High-risk action is pending confirmation.",
                "#b45309",
            )
            return

        summary = "; ".join(f"{item['action']} {item['target']}".strip() for item in details)
        self._set_confirmation_status("Pending confirmation", summary, "#b45309")

        badge_parts = [f"[{item['risk_level'].upper()}] {item['action']}" for item in details]
        if hasattr(self, "risk_badges_label"):
            self.risk_badges_label.configure(text=" ".join(badge_parts))

        preview_text = " | ".join(item["preview"] for item in details if item.get("preview"))
        if hasattr(self, "command_preview_var"):
            self.command_preview_var.set(preview_text)
        if hasattr(self, "copy_preview_button"):
            self.copy_preview_button.configure(state=tk.NORMAL if preview_text else tk.DISABLED)

    def _copy_preview_text(self) -> None:
        if not hasattr(self, "command_preview_var"):
            return
        preview_text = self.command_preview_var.get().strip()
        if not preview_text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(preview_text)
        self._append_message("System", "Copied command preview to clipboard.")

    def _set_confirmation_status(self, status: str, details: str, color: str) -> None:
        if hasattr(self, "confirmation_status_label"):
            self.confirmation_status_label.configure(text=status, fg=color)
        if hasattr(self, "confirmation_details_label"):
            self.confirmation_details_label.configure(text=details)

    def _set_busy(self, busy: bool) -> None:
        self._is_busy = busy
        self.send_button.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.entry.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self._sync_confirmation_controls()

    def _start_background_task(self, task_fn: Callable[[Callable[[StepStatus], None]], str | None]) -> None:
        self._request_counter += 1
        request_id = self._request_counter
        self._active_request_id = request_id
        self._request_started_at = time.time()
        self._set_busy(True)

        def worker() -> None:
            def on_progress(status: StepStatus) -> None:
                self._ui_queue.put(("progress", (request_id, status)))

            try:
                response = task_fn(on_progress)
                if response:
                    self._ui_queue.put(("response", (request_id, response)))
            except Exception as exc:
                self._ui_queue.put(("error", (request_id, f"Unexpected error while processing request: {exc}")))
            finally:
                self._ui_queue.put(("done", (request_id, None)))

        threading.Thread(target=worker, daemon=True).start()

    def _drain_ui_queue(self) -> None:
        if self._is_busy and self._request_started_at is not None:
            elapsed = time.time() - self._request_started_at
            if elapsed > self._task_timeout_seconds and self._active_request_id is not None:
                self._cancelled_request_ids.add(self._active_request_id)
                self._append_message("System", f"Request timed out after {int(self._task_timeout_seconds)}s.")
                self._active_request_id = None
                self._request_started_at = None
                self._set_busy(False)

        try:
            while True:
                event_type, payload = self._ui_queue.get_nowait()
                if event_type == "progress" and isinstance(payload, tuple):
                    request_id, status = payload
                    if not self._should_accept_event(request_id):
                        continue
                    if isinstance(status, StepStatus):
                        self._append_message("System", self._status_to_text(status))
                        self._update_progress_panel(status)
                        self._append_timeline(self._timeline_text_for_status(status))
                    else:
                        self._append_message("System", str(status))
                elif event_type == "response" and isinstance(payload, tuple):
                    request_id, response = payload
                    if self._should_accept_event(request_id) and response is not None:
                        self._append_message("Agent", str(response))
                        self._append_timeline("result available")
                elif event_type == "error" and isinstance(payload, tuple):
                    request_id, error_text = payload
                    if self._should_accept_event(request_id) and error_text is not None:
                        self._append_message("System", str(error_text))
                        self._append_timeline("error")
                elif event_type == "done" and isinstance(payload, tuple):
                    request_id, _ = payload
                    if self._active_request_id == request_id:
                        self._active_request_id = None
                        self._request_started_at = None
                        self._append_timeline("request finished")
                        self._set_busy(False)
                        self.entry.focus_set()
        except queue.Empty:
            pass

        self.root.after(50, self._drain_ui_queue)

    def _reset_progress_panel(self) -> None:
        self.progress_list.delete(0, tk.END)
        self._step_progress_rows.clear()

    def _clear_timeline(self) -> None:
        if hasattr(self, "timeline_list"):
            self.timeline_list.delete(0, tk.END)

    def _insert_tool_command(self, command_text: str) -> None:
        if hasattr(self, "entry"):
            self.entry.delete(0, tk.END)
            self.entry.insert(0, command_text)
            self.entry.focus_set()

    def _apply_runtime_options(self) -> None:
        self._task_timeout_seconds = float(self._settings.gui_timeout_seconds)
        os.environ["AUTO_AGENT_GUI_TASK_TIMEOUT"] = str(self._settings.gui_timeout_seconds)
        os.environ["AUTO_AGENT_INSTALL_RETRIES"] = str(self._settings.install_retries)

    def _open_options_dialog(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("App Options")
        dialog.geometry("520x240")
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(dialog, text="GUI Request Timeout (seconds)").grid(row=0, column=0, sticky="w", padx=12, pady=10)
        timeout_entry = tk.Entry(dialog, width=20)
        timeout_entry.grid(row=0, column=1, sticky="w", padx=12, pady=10)
        timeout_entry.insert(0, str(self._settings.gui_timeout_seconds))

        tk.Label(dialog, text="Install Retries").grid(row=1, column=0, sticky="w", padx=12, pady=10)
        retries_entry = tk.Entry(dialog, width=20)
        retries_entry.grid(row=1, column=1, sticky="w", padx=12, pady=10)
        retries_entry.insert(0, str(self._settings.install_retries))

        confirm_var = tk.BooleanVar(value=self._settings.confirm_high_risk)
        confirm_check = tk.Checkbutton(
            dialog,
            text="Require confirmation for high-risk actions",
            variable=confirm_var,
        )
        confirm_check.grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=6)

        helper = tk.Label(
            dialog,
            text="These options apply immediately and are saved in local settings.",
            fg=FG_MUTED,
            justify=tk.LEFT,
        )
        helper.grid(row=3, column=0, columnspan=2, sticky="w", padx=12, pady=8)

        def save_and_close() -> None:
            try:
                gui_timeout = float(timeout_entry.get().strip() or "45")
                install_retries = int(retries_entry.get().strip() or "2")
            except ValueError:
                messagebox.showerror("Invalid value", "Timeout must be numeric and retries must be integer.", parent=dialog)
                return

            if gui_timeout <= 0:
                messagebox.showerror("Invalid value", "GUI timeout must be > 0.", parent=dialog)
                return
            if install_retries < 0:
                messagebox.showerror("Invalid value", "Install retries cannot be negative.", parent=dialog)
                return

            self._settings.gui_timeout_seconds = gui_timeout
            self._settings.install_retries = install_retries
            self._settings.confirm_high_risk = bool(confirm_var.get())
            self._settings_store.save(self._settings)
            self._apply_runtime_options()
            self._append_message("System", "App options saved and applied.")
            dialog.destroy()

        button_frame = tk.Frame(dialog)
        button_frame.grid(row=4, column=0, columnspan=2, sticky="e", padx=12, pady=12)
        tk.Button(button_frame, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT, padx=(8, 0))
        tk.Button(button_frame, text="Save", command=save_and_close).pack(side=tk.RIGHT)

    def _set_step_status(self, step: int, total: int, state: str, tool: str) -> None:
        text = f"{step}/{total} | {state:<7} | {tool}"
        if step in self._step_progress_rows:
            row = self._step_progress_rows[step]
            self.progress_list.delete(row)
            self.progress_list.insert(row, text)
        else:
            row = self.progress_list.size()
            self.progress_list.insert(tk.END, text)
            self._step_progress_rows[step] = row
        self.progress_list.see(row)

    def _update_progress_panel(self, status: StepStatus) -> None:
        self._set_step_status(status.step, status.total, status.state, status.tool)

    def _status_to_text(self, status: StepStatus) -> str:
        if status.state == "running":
            return f"Step {status.step}/{status.total}: running {status.tool}..."
        if status.state == "done":
            return f"Step {status.step}/{status.total} finished {status.tool} (ok)."
        return f"Step {status.step}/{status.total} finished {status.tool} (failed)."

    def _timeline_text_for_status(self, status: StepStatus) -> str:
        if status.state == "running":
            return f"{status.step}/{status.total} run {status.tool}"
        if status.state == "done":
            return f"{status.step}/{status.total} ok {status.tool}"
        return f"{status.step}/{status.total} fail {status.tool}"

    def _append_timeline(self, text: str) -> None:
        if not hasattr(self, "timeline_list"):
            return
        self.timeline_list.insert(tk.END, text)
        while self.timeline_list.size() > 40:
            self.timeline_list.delete(0)
        self.timeline_list.see(tk.END)

    def _should_accept_event(self, request_id: int) -> bool:
        if request_id in self._cancelled_request_ids:
            return False
        return self._active_request_id == request_id

    def _build_agent(self) -> AutoSystemAgent:
        config = self._settings_store.resolve_llm_config(self._settings)
        return AutoSystemAgent(llm_config=config, confirm_high_risk=self._settings.confirm_high_risk)

    def _open_settings_dialog(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("LLM Settings")
        dialog.geometry("620x380")
        dialog.transient(self.root)
        dialog.grab_set()

        def add_row(label_text: str, row: int, initial: str, show: str | None = None) -> tk.Entry:
            label = tk.Label(dialog, text=label_text)
            label.grid(row=row, column=0, sticky="w", padx=12, pady=8)
            entry = tk.Entry(dialog, width=52, show=show)
            entry.grid(row=row, column=1, sticky="we", padx=12, pady=8)
            entry.insert(0, initial)
            return entry

        dialog.columnconfigure(1, weight=1)

        mode_var = tk.StringVar(value=self._settings.provider_mode)
        mode_label = tk.Label(dialog, text="Provider Mode")
        mode_label.grid(row=0, column=0, sticky="nw", padx=12, pady=8)

        mode_frame = tk.Frame(dialog)
        mode_frame.grid(row=0, column=1, sticky="w", padx=12, pady=8)
        tk.Radiobutton(
            mode_frame,
            text="Bundled (use app preconfigured provider)",
            variable=mode_var,
            value="bundled",
        ).pack(anchor="w")
        tk.Radiobutton(
            mode_frame,
            text="Custom (use my own token and endpoint)",
            variable=mode_var,
            value="custom",
        ).pack(anchor="w")

        url_entry = add_row("Custom LLM URL", 1, self._settings.url)
        key_entry = add_row("Custom API Key", 2, self._settings.api_key, show="*")
        model_entry = add_row("Custom Model", 3, self._settings.model)
        timeout_entry = add_row("Custom Timeout (seconds)", 4, str(self._settings.timeout))

        helper_label = tk.Label(
            dialog,
            text="Bundled mode reads AUTO_AGENT_DEFAULT_LLM_* env vars.\n"
            "Custom mode uses values below and stores them in your local settings.",
            justify=tk.LEFT,
            anchor="w",
            fg=FG_MUTED,
        )
        helper_label.grid(row=5, column=0, columnspan=2, sticky="w", padx=12, pady=(2, 6))

        custom_controls = [url_entry, key_entry, model_entry, timeout_entry]

        def sync_mode_state(*_args) -> None:
            state = tk.NORMAL if mode_var.get() == "custom" else tk.DISABLED
            for control in custom_controls:
                control.configure(state=state)

        mode_var.trace_add("write", sync_mode_state)
        sync_mode_state()

        def save_and_close() -> None:
            try:
                timeout_value = float(timeout_entry.get().strip() or "8")
            except ValueError:
                messagebox.showerror("Invalid value", "Timeout must be a number.", parent=dialog)
                return

            self._settings = LLMSettings(
                provider_mode=mode_var.get().strip() or "bundled",
                url=url_entry.get().strip(),
                api_key=key_entry.get().strip(),
                model=model_entry.get().strip() or "gpt-4o-mini",
                timeout=timeout_value,
            )
            self._settings_store.save(self._settings)
            self.agent = self._build_agent()
            if self._settings.provider_mode == "custom":
                self._append_message("Agent", "LLM settings saved in custom mode and applied.")
            else:
                self._append_message("Agent", "LLM settings saved in bundled mode and applied.")
            dialog.destroy()

        button_frame = tk.Frame(dialog)
        button_frame.grid(row=6, column=0, columnspan=2, sticky="e", padx=12, pady=12)
        tk.Button(button_frame, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT, padx=(8, 0))
        tk.Button(button_frame, text="Save", command=save_and_close).pack(side=tk.RIGHT)

    def run(self) -> None:
        self._apply_runtime_options()
        self.root.mainloop()


def run_gui() -> None:
    app = AgentChatGUI()
    app.run()
