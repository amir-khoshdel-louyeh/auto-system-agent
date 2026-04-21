import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
import queue
import os
import threading
import time
from typing import Callable

from auto_system_agent.agent import AutoSystemAgent
from auto_system_agent.models import StepStatus
from auto_system_agent.settings import LLMSettings, OLLAMA_DEFAULT_MODEL, OLLAMA_DEFAULT_URL, SettingsStore


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
        self._is_busy = False
        self._ui_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._request_counter = 0
        self._active_request_id: int | None = None
        self._cancelled_request_ids: set[int] = set()
        self._request_started_at: float | None = None
        self._task_timeout_seconds = float(os.getenv("AUTO_AGENT_GUI_TASK_TIMEOUT", "45") or "45")
        self._geometry_save_after_id: str | None = None
        self._pending_geometry: str | None = None
        self._tools_window: tk.Toplevel | None = None
        self._settings_window: tk.Toplevel | None = None
        self._settings_notebook: ttk.Notebook | None = None
        self.root = tk.Tk()
        self.root.title("Auto System Agent")
        self.root.configure(bg=BG_APP)
        self.root.minsize(720, 400)
        self.root.resizable(True, True)
        self._apply_saved_window_geometry()
        self._maximize_window()
        self._setup_window_geometry_persistence()
        # Some WMs apply geometry after mapping; re-assert maximized state
        try:
            self.root.after(100, self._maximize_window)
        except Exception:
            pass

        # Mandatory choice on every startup: API vs Local model
        self._show_startup_provider_dialog()

        self.agent = self._build_agent()
        # Ensure window stays maximized after startup dialog
        self._maximize_window()
        try:
            self.root.after(100, self._maximize_window)
        except Exception:
            pass

        menu_bar = tk.Menu(self.root)
        tools_menu = tk.Menu(menu_bar, tearoff=0)
        tools_menu.add_command(label="Tools Overview", command=self._open_tools_window)
        tools_menu.add_separator()
        tools_menu.add_command(label="Insert Example: make file in Downloads", command=lambda: self._insert_tool_command("make a file known as test.py in the Downloads directory"))
        tools_menu.add_command(label="Insert Example: list Downloads", command=lambda: self._insert_tool_command("list files in ~/Downloads"))
        tools_menu.add_command(label="Insert Example: install vlc", command=lambda: self._insert_tool_command("install vlc"))
        tools_menu.add_command(label="Insert Example: run ls -la", command=lambda: self._insert_tool_command("run ls -la ~/Downloads"))
        menu_bar.add_cascade(label="Tools", menu=tools_menu)

        settings_menu = tk.Menu(menu_bar, tearoff=0)
        settings_menu.add_command(label="Settings", command=self._open_settings_window)
        settings_menu.add_separator()
        settings_menu.add_command(label="LLM Settings", command=lambda: self._open_settings_window(selected_tab="llm"))
        settings_menu.add_command(label="App Options", command=lambda: self._open_settings_window(selected_tab="app"))
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
            self.root.after(300, self._on_close)
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

    def _show_startup_provider_dialog(self) -> None:
        """Mandatory modal on every startup: user must choose API or Local model."""
        # Use current settings as initial values but force explicit choice
        current_mode = self._settings_store._normalize_provider_mode(getattr(self._settings, "provider_mode", "local"))
        initial_mode = current_mode if current_mode in ("local", "api") else "local"

        # Prepare initial field values
        if initial_mode == "local":
            init_local_url = self._settings.url.strip() or OLLAMA_DEFAULT_URL
            init_local_model = self._settings.model.strip() or OLLAMA_DEFAULT_MODEL
            init_local_timeout = str(self._settings.timeout)
            init_api_url = ""
            init_api_key = ""
            init_api_model = "gpt-4o-mini"
            init_api_timeout = "30"
        else:
            init_local_url = OLLAMA_DEFAULT_URL
            init_local_model = OLLAMA_DEFAULT_MODEL
            init_local_timeout = "30"
            init_api_url = self._settings.url.strip()
            init_api_key = self._settings.api_key.strip()
            init_api_model = self._settings.model.strip() or "gpt-4o-mini"
            init_api_timeout = str(self._settings.timeout)

        choice_made = {"done": False, "mode": None}

        dialog = tk.Toplevel(self.root)
        dialog.title("Choose LLM Provider - Required")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(True, True)
        dialog.focus_set()

        def exit_app():
            try:
                dialog.destroy()
            except Exception:
                pass
            try:
                self.root.destroy()
            except Exception:
                pass
            import sys as _sys
            _sys.exit(0)

        def on_dialog_close():
            if not choice_made["done"]:
                exit_app()

        dialog.protocol("WM_DELETE_WINDOW", on_dialog_close)

        header = tk.Frame(dialog, bg=BG_PANEL, padx=16, pady=12)
        header.pack(fill=tk.X)
        tk.Label(header, text="Choose how the app will run", font=("TkDefaultFont", 12, "bold"), fg=ACCENT, bg=BG_PANEL).pack(anchor="w")
        tk.Label(
            header,
            text="You must select exactly one provider before the app can start.\nThe app will act according to your choice (Local Ollama or Remote API).",
            font=("TkDefaultFont", 9),
            fg=FG_MUTED,
            bg=BG_PANEL,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(4, 0))

        mode_var = tk.StringVar(value=initial_mode)

        radio_frame = tk.Frame(dialog, padx=16, pady=8)
        radio_frame.pack(fill=tk.X)
        tk.Radiobutton(radio_frame, text="Local Model (Ollama)  — runs on http://localhost:11434, no API key", variable=mode_var, value="local").pack(anchor="w", pady=2)
        tk.Radiobutton(radio_frame, text="Remote API  — OpenAI-compatible endpoint with API key", variable=mode_var, value="api").pack(anchor="w", pady=2)

        content = tk.Frame(dialog, padx=16, pady=8)
        content.pack(fill=tk.BOTH, expand=True)

        # Local frame
        local_frame = tk.LabelFrame(content, text="Local Model Settings", padx=10, pady=8)
        local_frame.pack(fill=tk.X, pady=(0, 8))
        tk.Label(local_frame, text="Ollama URL").grid(row=0, column=0, sticky="w", pady=4)
        local_url_entry = tk.Entry(local_frame, width=52)
        local_url_entry.grid(row=0, column=1, sticky="we", padx=8, pady=4)
        local_url_entry.insert(0, init_local_url)
        tk.Label(local_frame, text="Model").grid(row=1, column=0, sticky="w", pady=4)
        local_model_entry = tk.Entry(local_frame, width=52)
        local_model_entry.grid(row=1, column=1, sticky="we", padx=8, pady=4)
        local_model_entry.insert(0, init_local_model)
        tk.Label(local_frame, text="Timeout (s)").grid(row=2, column=0, sticky="w", pady=4)
        local_timeout_entry = tk.Entry(local_frame, width=20)
        local_timeout_entry.grid(row=2, column=1, sticky="w", padx=8, pady=4)
        local_timeout_entry.insert(0, init_local_timeout)
        local_frame.columnconfigure(1, weight=1)

        # API frame
        api_frame = tk.LabelFrame(content, text="Remote API Settings", padx=10, pady=8)
        api_frame.pack(fill=tk.X)
        tk.Label(api_frame, text="API URL *").grid(row=0, column=0, sticky="w", pady=4)
        api_url_entry = tk.Entry(api_frame, width=52)
        api_url_entry.grid(row=0, column=1, sticky="we", padx=8, pady=4)
        api_url_entry.insert(0, init_api_url)
        tk.Label(api_frame, text="API Key *").grid(row=1, column=0, sticky="w", pady=4)
        api_key_entry = tk.Entry(api_frame, width=52, show="*")
        api_key_entry.grid(row=1, column=1, sticky="we", padx=8, pady=4)
        api_key_entry.insert(0, init_api_key)
        tk.Label(api_frame, text="Model *").grid(row=2, column=0, sticky="w", pady=4)
        api_model_entry = tk.Entry(api_frame, width=52)
        api_model_entry.grid(row=2, column=1, sticky="we", padx=8, pady=4)
        api_model_entry.insert(0, init_api_model)
        tk.Label(api_frame, text="Timeout (s)").grid(row=3, column=0, sticky="w", pady=4)
        api_timeout_entry = tk.Entry(api_frame, width=20)
        api_timeout_entry.grid(row=3, column=1, sticky="w", padx=8, pady=4)
        api_timeout_entry.insert(0, init_api_timeout)
        api_frame.columnconfigure(1, weight=1)

        hint = tk.Label(dialog, text="* Required for Remote API. Local needs only URL/model.", fg=FG_MUTED, font=("TkDefaultFont", 8), anchor="w", padx=16)
        hint.pack(fill=tk.X, pady=(0, 8))

        def sync_state(*_args):
            mode = mode_var.get()
            is_local = mode == "local"
            state_local = tk.NORMAL if is_local else tk.DISABLED
            state_api = tk.NORMAL if not is_local else tk.DISABLED
            for w in (local_url_entry, local_model_entry, local_timeout_entry):
                try:
                    w.configure(state=state_local)
                except Exception:
                    pass
            for w in (api_url_entry, api_key_entry, api_model_entry, api_timeout_entry):
                try:
                    w.configure(state=state_api)
                except Exception:
                    pass
            # Visual cue
            try:
                local_frame.configure(fg=FG_PRIMARY if is_local else FG_MUTED)
                api_frame.configure(fg=FG_PRIMARY if not is_local else FG_MUTED)
            except Exception:
                pass

        mode_var.trace_add("write", sync_state)
        sync_state()

        def on_continue():
            mode = mode_var.get().strip() or "local"
            if mode not in ("local", "api"):
                messagebox.showerror("Invalid choice", "Please select Local or Remote API.", parent=dialog)
                return
            if mode == "local":
                url = local_url_entry.get().strip() or OLLAMA_DEFAULT_URL
                model = local_model_entry.get().strip() or OLLAMA_DEFAULT_MODEL
                timeout_raw = local_timeout_entry.get().strip() or "30"
                api_key = ""
                if not url:
                    messagebox.showerror("Invalid value", "Local Ollama URL is required.", parent=dialog)
                    return
                if not model:
                    messagebox.showerror("Invalid value", "Local model name is required.", parent=dialog)
                    return
            else:
                url = api_url_entry.get().strip()
                api_key = api_key_entry.get().strip()
                model = api_model_entry.get().strip()
                timeout_raw = api_timeout_entry.get().strip() or "30"
                if not url:
                    messagebox.showerror("Invalid value", "API URL is required for Remote API.", parent=dialog)
                    return
                if not api_key:
                    messagebox.showerror("Invalid value", "API Key is required for Remote API.", parent=dialog)
                    return
                if not model:
                    messagebox.showerror("Invalid value", "Model is required for Remote API.", parent=dialog)
                    return

            try:
                timeout_val = float(timeout_raw)
                if timeout_val <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Invalid value", "Timeout must be a positive number.", parent=dialog)
                return

            # Preserve other settings
            prev = self._settings
            self._settings = LLMSettings(
                provider_mode=mode,
                url=url,
                api_key=api_key,
                model=model,
                timeout=timeout_val,
                gui_timeout_seconds=getattr(prev, "gui_timeout_seconds", 45.0),
                install_retries=getattr(prev, "install_retries", 2),
                confirm_high_risk=getattr(prev, "confirm_high_risk", True),
                window_geometry=getattr(prev, "window_geometry", "920x560"),
            )
            try:
                self._settings_store.save(self._settings)
            except Exception as exc:
                messagebox.showerror("Save failed", f"Could not save settings: {exc}", parent=dialog)
                return
            choice_made["done"] = True
            choice_made["mode"] = mode
            try:
                dialog.grab_release()
            except Exception:
                pass
            dialog.destroy()

        btn_frame = tk.Frame(dialog, padx=16, pady=12)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)
        tk.Button(btn_frame, text="Exit", command=exit_app, bg="#6b7280", fg="#ffffff", relief=tk.FLAT, padx=12).pack(side=tk.RIGHT, padx=(8, 0))
        tk.Button(btn_frame, text="Continue", command=on_continue, bg=ACCENT, fg="#ffffff", relief=tk.FLAT, padx=12).pack(side=tk.RIGHT)
        # Bind Enter to continue
        dialog.bind("<Return>", lambda _e: on_continue())

        # Ensure window is at least as large as its content (no clipping)
        self._fit_dialog_to_content(dialog, default_width=680, default_height=560)

        # Modal block
        dialog.wait_window()

        if not choice_made["done"]:
            # User closed without choosing -> exit already handled, but ensure
            exit_app()

    def _fit_dialog_to_content(self, dialog: tk.Toplevel, default_width: int = 620, default_height: int = 380) -> None:
        """Ensure dialog window is at least as large as its content to avoid clipping."""
        try:
            dialog.update_idletasks()
            req_w = dialog.winfo_reqwidth()
            req_h = dialog.winfo_reqheight()
            # Add small padding for window decorations
            req_w += 20
            req_h += 20
            w = max(req_w, default_width)
            h = max(req_h, default_height)
            # Clamp to screen size
            try:
                sw = dialog.winfo_screenwidth()
                sh = dialog.winfo_screenheight()
                w = min(w, max(400, sw - 40))
                h = min(h, max(300, sh - 40))
            except Exception:
                pass
            # Center over root
            try:
                x = self.root.winfo_rootx() + (self.root.winfo_width() // 2) - (w // 2)
                y = self.root.winfo_rooty() + (self.root.winfo_height() // 2) - (h // 2)
                dialog.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
            except Exception:
                dialog.geometry(f"{w}x{h}")
            dialog.minsize(req_w, req_h)
        except Exception:
            pass

    def _maximize_window(self) -> None:
        """Maximize window while keeping title bar controls (minimize/maximize/close)."""
        # 'zoomed' keeps window decorations; never use overrideredirect or -fullscreen.
        try:
            self.root.state("zoomed")
            return
        except Exception:
            pass
        try:
            # Fallback for some Linux window managers
            self.root.attributes("-zoomed", True)
            return
        except Exception:
            pass
        # No hard fallback to geometry/fullscreen to preserve window controls

    def _apply_saved_window_geometry(self) -> None:
        geometry = getattr(self._settings, "window_geometry", "920x560")
        if not isinstance(geometry, str) or not geometry.strip():
            geometry = "920x560"
        geometry = geometry.strip()
        try:
            self.root.geometry(geometry)
        except Exception:
            try:
                self.root.geometry("920x560")
            except Exception:
                pass

    def _setup_window_geometry_persistence(self) -> None:
        try:
            self.root.bind("<Configure>", self._on_window_configure)
        except Exception:
            pass
        try:
            self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        except Exception:
            pass

    def _on_window_configure(self, event) -> None:
        try:
            if event.widget is not self.root:
                return
        except Exception:
            return
        try:
            geom = self.root.geometry()
        except Exception:
            return
        self._pending_geometry = geom
        if self._geometry_save_after_id is not None:
            try:
                self.root.after_cancel(self._geometry_save_after_id)
            except Exception:
                pass
        try:
            self._geometry_save_after_id = self.root.after(600, self._save_window_geometry)
        except Exception:
            self._geometry_save_after_id = None

    def _save_window_geometry(self) -> None:
        self._geometry_save_after_id = None
        geometry = self._pending_geometry
        if geometry is None:
            try:
                geometry = self.root.geometry()
            except Exception:
                return
        if not geometry or not isinstance(geometry, str):
            return
        geometry = geometry.strip()
        if not geometry:
            return
        if geometry == getattr(self._settings, "window_geometry", None):
            self._pending_geometry = None
            return
        self._settings.window_geometry = geometry
        try:
            self._settings_store.save(self._settings)
        except Exception:
            pass
        self._pending_geometry = None

    def _on_close(self) -> None:
        if self._geometry_save_after_id is not None:
            try:
                self.root.after_cancel(self._geometry_save_after_id)
            except Exception:
                pass
            self._geometry_save_after_id = None
        try:
            geometry = self.root.geometry()
            if geometry and isinstance(geometry, str) and geometry.strip():
                self._settings.window_geometry = geometry.strip()
                self._settings_store.save(self._settings)
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _apply_runtime_options(self) -> None:
        self._task_timeout_seconds = float(self._settings.gui_timeout_seconds)
        os.environ["AUTO_AGENT_GUI_TASK_TIMEOUT"] = str(self._settings.gui_timeout_seconds)
        os.environ["AUTO_AGENT_INSTALL_RETRIES"] = str(self._settings.install_retries)

    def _open_tools_window(self) -> None:
        """Dedicated Tools window (new window) showing available system tools."""
        # Avoid duplicate windows
        if hasattr(self, "_tools_window") and getattr(self, "_tools_window", None) is not None:
            try:
                win = self._tools_window
                if win.winfo_exists():
                    win.lift()
                    win.focus_set()
                    return
            except Exception:
                pass

        window = tk.Toplevel(self.root)
        window.title("Tools")
        window.transient(self.root)
        window.resizable(True, True)
        window.configure(bg=BG_APP)
        self._tools_window = window

        def on_close():
            try:
                window.destroy()
            except Exception:
                pass
            self._tools_window = None

        window.protocol("WM_DELETE_WINDOW", on_close)

        header = tk.Frame(window, bg=BG_PANEL, padx=16, pady=12)
        header.pack(fill=tk.X)
        tk.Label(header, text="Terminal Tools", font=("TkDefaultFont", 12, "bold"), fg=ACCENT, bg=BG_PANEL).pack(anchor="w")
        tk.Label(
            header,
            text="All requests run directly as bash commands via a persistent terminal session (bash -lc). No per-request Python tool.",
            font=("TkDefaultFont", 9),
            fg=FG_MUTED,
            bg=BG_PANEL,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(4, 0))

        paned = tk.PanedWindow(window, orient=tk.HORIZONTAL, bg=BG_APP, sashwidth=4)
        paned.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        left_frame = tk.Frame(paned, bg=BG_PANEL, highlightbackground="#d0d7e2", highlightthickness=1)
        paned.add(left_frame, minsize=220, width=260)
        tk.Label(left_frame, text="Tool Categories", font=("TkDefaultFont", 10, "bold"), fg=ACCENT, bg=BG_PANEL).pack(anchor="w", padx=10, pady=(10, 6))
        tools_list = tk.Listbox(left_frame, bg="#f8fafc", fg=FG_PRIMARY, borderwidth=0, highlightthickness=0, selectbackground="#dbeafe", font=("TkDefaultFont", 9))
        tools_list.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        right_frame = tk.Frame(paned, bg=BG_PANEL, highlightbackground="#d0d7e2", highlightthickness=1)
        paned.add(right_frame, minsize=380)
        tk.Label(right_frame, text="Details", font=("TkDefaultFont", 10, "bold"), fg=ACCENT, bg=BG_PANEL).pack(anchor="w", padx=10, pady=(10, 6))
        details_text = scrolledtext.ScrolledText(right_frame, wrap=tk.WORD, font=("TkDefaultFont", 9), bg="#f8fafc", fg=FG_PRIMARY, borderwidth=0, relief=tk.FLAT, padx=10, pady=8, height=16)
        details_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))
        details_text.configure(state=tk.DISABLED)

        btn_row = tk.Frame(right_frame, bg=BG_PANEL)
        btn_row.pack(fill=tk.X, padx=10, pady=(0, 10))

        # Terminal-centric catalog - no fake per-request tools
        catalog = [
            {
                "name": "Persistent Terminal (bash -lc)",
                "actions": ["run_command"],
                "description": "Single persistent bash session. Every user request is translated to an exact shell command and executed via TerminalSession (bash -lc) with cwd tracking (pwd/cd/history). No fake Python simulation.",
                "examples": ["touch ~/Downloads/test.py", "mkdir -p ~/Downloads/demo && touch ~/Downloads/demo/file.txt", "ls -la ~/Downloads", "cat ~/Downloads/test.py", "cp ~/Downloads/a.txt ~/Downloads/b.txt"],
                "safety": "Direct shell: any bash command is allowed. Destructive commands (rm, sudo) require confirmation. History and cwd are tracked.",
            },
            {
                "name": "File & Folder (via shell)",
                "actions": ["run_command"],
                "description": "File operations are plain shell commands, not Python wrappers.",
                "examples": ["mkdir -p ~/Downloads/demo", "touch ~/Downloads/test.py", "rm ~/Downloads/test.py", "mv ~/Downloads/a.txt ~/Downloads/b.txt", "zip -r ~/Downloads/archive.zip ~/Downloads/demo"],
                "safety": "Uses real mkdir/touch/rm/mv/zip. Paths like ~/Downloads/<name> are used directly.",
            },
            {
                "name": "System & Install (via shell)",
                "actions": ["run_command"],
                "description": "System tasks are shell commands via terminal, no separate installer tool.",
                "examples": ["sudo apt install -y vlc", "ps -eo pid,comm,%cpu | head", "ping -c 4 google.com", "git clone https://github.com/user/repo.git"],
                "safety": "Installer is just shell: sudo apt/dnf/pacman/brew/winget. Confirmation required for sudo/rm.",
            },
        ]

        def show_details(index: int) -> None:
            if index < 0 or index >= len(catalog):
                return
            item = catalog[index]
            details_text.configure(state=tk.NORMAL)
            details_text.delete("1.0", tk.END)
            details_text.insert(tk.END, f"{item['name']}\n", "title")
            details_text.insert(tk.END, f"\n{item['description']}\n\n")
            details_text.insert(tk.END, "Actions:\n")
            details_text.insert(tk.END, f"  {', '.join(item['actions'])}\n\n")
            details_text.insert(tk.END, "Examples:\n")
            for ex in item["examples"]:
                details_text.insert(tk.END, f"  • {ex}\n")
            details_text.insert(tk.END, f"\nSafety:\n  {item['safety']}\n")
            details_text.configure(state=tk.DISABLED)
            # Update button to insert first example
            for w in btn_row.winfo_children():
                w.destroy()
            tk.Button(
                btn_row,
                text=f"Insert example: {item['examples'][0]}",
                command=lambda ex=item['examples'][0]: self._insert_tool_command(ex),
                bg=ACCENT,
                fg="#ffffff",
                relief=tk.FLAT,
                padx=10,
            ).pack(side=tk.LEFT)
            tk.Button(btn_row, text="Close", command=on_close, bg="#6b7280", fg="#ffffff", relief=tk.FLAT, padx=10).pack(side=tk.RIGHT)

        for idx, entry in enumerate(catalog):
            tools_list.insert(tk.END, entry["name"])

        def on_select(_event=None):
            sel = tools_list.curselection()
            if sel:
                show_details(sel[0])

        tools_list.bind("<<ListboxSelect>>", on_select)
        tools_list.selection_set(0)
        show_details(0)

        details_text.tag_configure("title", font=("TkDefaultFont", 10, "bold"), foreground=ACCENT)

        self._fit_dialog_to_content(window, default_width=820, default_height=480)

    def _open_settings_window(self, selected_tab: str | None = None) -> None:
        """Dedicated Settings window (new window) with tabs for LLM and App options."""
        if hasattr(self, "_settings_window") and getattr(self, "_settings_window", None) is not None:
            try:
                win = self._settings_window
                if win.winfo_exists():
                    win.lift()
                    win.focus_set()
                    # Switch tab if requested
                    if selected_tab and hasattr(self, "_settings_notebook"):
                        try:
                            idx = 0 if selected_tab == "llm" else 1
                            self._settings_notebook.select(idx)
                        except Exception:
                            pass
                    return
            except Exception:
                pass

        window = tk.Toplevel(self.root)
        window.title("Settings")
        window.transient(self.root)
        window.resizable(True, True)
        window.configure(bg=BG_APP)
        self._settings_window = window

        def on_close():
            try:
                window.destroy()
            except Exception:
                pass
            self._settings_window = None
            self._settings_notebook = None

        window.protocol("WM_DELETE_WINDOW", on_close)

        header = tk.Frame(window, bg=BG_PANEL, padx=16, pady=10)
        header.pack(fill=tk.X)
        tk.Label(header, text="Settings", font=("TkDefaultFont", 12, "bold"), fg=ACCENT, bg=BG_PANEL).pack(anchor="w")
        tk.Label(header, text="Configure LLM provider and application behavior. Changes are saved to ~/.auto_system_agent/settings.json", font=("TkDefaultFont", 9), fg=FG_MUTED, bg=BG_PANEL).pack(anchor="w", pady=(2, 0))

        notebook = ttk.Notebook(window)
        notebook.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        self._settings_notebook = notebook

        # LLM Tab
        llm_frame = tk.Frame(notebook, padx=16, pady=12, bg=BG_PANEL)
        notebook.add(llm_frame, text="LLM Provider")

        normalized_mode = self._settings_store._normalize_provider_mode(self._settings.provider_mode)
        llm_mode_var = tk.StringVar(value=normalized_mode)

        tk.Label(llm_frame, text="Provider Mode", bg=BG_PANEL, fg=FG_PRIMARY, font=("TkDefaultFont", 9, "bold")).grid(row=0, column=0, sticky="nw", pady=6, padx=6)
        mode_box = tk.Frame(llm_frame, bg=BG_PANEL)
        mode_box.grid(row=0, column=1, sticky="w", pady=6, padx=6)
        tk.Radiobutton(mode_box, text="Local Model (Ollama) — local inference", variable=llm_mode_var, value="local", bg=BG_PANEL).pack(anchor="w")
        tk.Radiobutton(mode_box, text="Remote API — OpenAI-compatible endpoint", variable=llm_mode_var, value="api", bg=BG_PANEL).pack(anchor="w")

        tk.Label(llm_frame, text="LLM URL", bg=BG_PANEL).grid(row=1, column=0, sticky="w", padx=6, pady=6)
        llm_url_entry = tk.Entry(llm_frame, width=48)
        llm_url_entry.grid(row=1, column=1, sticky="we", padx=6, pady=6)
        llm_url_entry.insert(0, self._settings.url)

        tk.Label(llm_frame, text="API Key", bg=BG_PANEL).grid(row=2, column=0, sticky="w", padx=6, pady=6)
        llm_key_entry = tk.Entry(llm_frame, width=48, show="*")
        llm_key_entry.grid(row=2, column=1, sticky="we", padx=6, pady=6)
        llm_key_entry.insert(0, self._settings.api_key)

        tk.Label(llm_frame, text="Model", bg=BG_PANEL).grid(row=3, column=0, sticky="w", padx=6, pady=6)
        llm_model_entry = tk.Entry(llm_frame, width=48)
        llm_model_entry.grid(row=3, column=1, sticky="we", padx=6, pady=6)
        llm_model_entry.insert(0, self._settings.model)

        tk.Label(llm_frame, text="Timeout (s)", bg=BG_PANEL).grid(row=4, column=0, sticky="w", padx=6, pady=6)
        llm_timeout_entry = tk.Entry(llm_frame, width=20)
        llm_timeout_entry.grid(row=4, column=1, sticky="w", padx=6, pady=6)
        llm_timeout_entry.insert(0, str(self._settings.timeout))

        helper_llm = tk.Label(llm_frame, text="Local uses Ollama at http://localhost:11434 (or URL above). Remote API uses URL + API key + model.", fg=FG_MUTED, bg=BG_PANEL, justify=tk.LEFT, wraplength=500)
        helper_llm.grid(row=5, column=0, columnspan=2, sticky="w", padx=6, pady=8)
        llm_frame.columnconfigure(1, weight=1)

        def sync_llm_state(*_args):
            mode = llm_mode_var.get()
            if mode == "local":
                llm_key_entry.configure(state=tk.DISABLED)
            else:
                llm_key_entry.configure(state=tk.NORMAL)

        llm_mode_var.trace_add("write", sync_llm_state)
        sync_llm_state()

        # App Options Tab
        app_frame = tk.Frame(notebook, padx=16, pady=12, bg=BG_PANEL)
        notebook.add(app_frame, text="App Options")

        tk.Label(app_frame, text="GUI Request Timeout (seconds)", bg=BG_PANEL).grid(row=0, column=0, sticky="w", padx=6, pady=8)
        app_timeout_entry = tk.Entry(app_frame, width=20)
        app_timeout_entry.grid(row=0, column=1, sticky="w", padx=6, pady=8)
        app_timeout_entry.insert(0, str(self._settings.gui_timeout_seconds))

        tk.Label(app_frame, text="Install Retries", bg=BG_PANEL).grid(row=1, column=0, sticky="w", padx=6, pady=8)
        app_retries_entry = tk.Entry(app_frame, width=20)
        app_retries_entry.grid(row=1, column=1, sticky="w", padx=6, pady=8)
        app_retries_entry.insert(0, str(self._settings.install_retries))

        app_confirm_var = tk.BooleanVar(value=self._settings.confirm_high_risk)
        tk.Checkbutton(app_frame, text="Require confirmation for high-risk actions", variable=app_confirm_var, bg=BG_PANEL).grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=6)
        tk.Label(app_frame, text="These options apply immediately and are saved in local settings.", fg=FG_MUTED, bg=BG_PANEL, justify=tk.LEFT).grid(row=3, column=0, columnspan=2, sticky="w", padx=6, pady=8)
        app_frame.columnconfigure(1, weight=1)

        # Select requested tab
        if selected_tab == "app":
            try:
                notebook.select(1)
            except Exception:
                pass

        btn_frame = tk.Frame(window, bg=BG_APP)
        btn_frame.pack(fill=tk.X, padx=12, pady=(0, 12))

        def save_all():
            # Validate LLM
            try:
                llm_timeout_val = float(llm_timeout_entry.get().strip() or "30")
            except ValueError:
                messagebox.showerror("Invalid value", "LLM Timeout must be a number.", parent=window)
                return
            llm_mode = llm_mode_var.get().strip() or "local"
            if llm_mode not in ("local", "api"):
                llm_mode = "local"
            if llm_mode == "api" and not llm_url_entry.get().strip():
                messagebox.showerror("Invalid value", "API URL is required for Remote API.", parent=window)
                return
            if llm_mode == "api" and not llm_key_entry.get().strip():
                messagebox.showerror("Invalid value", "API Key is required for Remote API.", parent=window)
                return
            if not llm_model_entry.get().strip():
                messagebox.showerror("Invalid value", "Model is required.", parent=window)
                return
            # Validate App
            try:
                gui_timeout = float(app_timeout_entry.get().strip() or "45")
                install_retries = int(app_retries_entry.get().strip() or "2")
            except ValueError:
                messagebox.showerror("Invalid value", "Timeout must be numeric and retries must be integer.", parent=window)
                return
            if gui_timeout <= 0:
                messagebox.showerror("Invalid value", "GUI timeout must be > 0.", parent=window)
                return
            if install_retries < 0:
                messagebox.showerror("Invalid value", "Install retries cannot be negative.", parent=window)
                return

            prev_geo = getattr(self._settings, "window_geometry", "920x560")
            self._settings = LLMSettings(
                provider_mode=llm_mode,
                url=llm_url_entry.get().strip(),
                api_key=llm_key_entry.get().strip(),
                model=llm_model_entry.get().strip() or OLLAMA_DEFAULT_MODEL,
                timeout=llm_timeout_val,
                gui_timeout_seconds=gui_timeout,
                install_retries=install_retries,
                confirm_high_risk=bool(app_confirm_var.get()),
                window_geometry=prev_geo,
            )
            try:
                self._settings_store.save(self._settings)
            except Exception as exc:
                messagebox.showerror("Save failed", f"Could not save settings: {exc}", parent=window)
                return
            self.agent = self._build_agent()
            self._apply_runtime_options()
            self._append_message("System", "Settings saved and applied.")
            on_close()

        tk.Button(btn_frame, text="Cancel", command=on_close, bg="#6b7280", fg="#ffffff", relief=tk.FLAT, padx=12).pack(side=tk.RIGHT, padx=(8, 0))
        tk.Button(btn_frame, text="Save", command=save_all, bg=ACCENT, fg="#ffffff", relief=tk.FLAT, padx=12).pack(side=tk.RIGHT)

        self._fit_dialog_to_content(window, default_width=760, default_height=520)

    def _open_options_dialog(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("App Options")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(True, True)

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

        self._fit_dialog_to_content(dialog, default_width=520, default_height=260)

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
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(True, True)

        def add_row(label_text: str, row: int, initial: str, show: str | None = None) -> tk.Entry:
            label = tk.Label(dialog, text=label_text)
            label.grid(row=row, column=0, sticky="w", padx=12, pady=8)
            entry = tk.Entry(dialog, width=52, show=show)
            entry.grid(row=row, column=1, sticky="we", padx=12, pady=8)
            entry.insert(0, initial)
            return entry

        dialog.columnconfigure(1, weight=1)

        # Normalize for display (bundled->local, custom->api)
        normalized_mode = self._settings_store._normalize_provider_mode(self._settings.provider_mode)
        mode_var = tk.StringVar(value=normalized_mode)
        mode_label = tk.Label(dialog, text="Provider Mode")
        mode_label.grid(row=0, column=0, sticky="nw", padx=12, pady=8)

        mode_frame = tk.Frame(dialog)
        mode_frame.grid(row=0, column=1, sticky="w", padx=12, pady=8)
        tk.Radiobutton(
            mode_frame,
            text="Local Model (Ollama) — local inference",
            variable=mode_var,
            value="local",
        ).pack(anchor="w")
        tk.Radiobutton(
            mode_frame,
            text="Remote API — OpenAI-compatible endpoint",
            variable=mode_var,
            value="api",
        ).pack(anchor="w")

        url_entry = add_row("LLM URL", 1, self._settings.url)
        key_entry = add_row("API Key", 2, self._settings.api_key, show="*")
        model_entry = add_row("Model", 3, self._settings.model)
        timeout_entry = add_row("Timeout (seconds)", 4, str(self._settings.timeout))

        helper_label = tk.Label(
            dialog,
            text="Local: uses Ollama at http://localhost:11434 (or URL above).\n"
            "Remote API: uses URL + API key + model above. Choice is required at each startup.",
            justify=tk.LEFT,
            anchor="w",
            fg=FG_MUTED,
        )
        helper_label.grid(row=5, column=0, columnspan=2, sticky="w", padx=12, pady=(2, 6))

        # For local, API key is optional; keep all fields enabled but hint
        def sync_mode_state(*_args) -> None:
            # Keep all enabled; visual hint via helper, but ensure key is disabled for local to signal optional
            mode = mode_var.get()
            if mode == "local":
                key_entry.configure(state=tk.DISABLED)
            else:
                key_entry.configure(state=tk.NORMAL)

        mode_var.trace_add("write", sync_mode_state)
        sync_mode_state()

        def save_and_close() -> None:
            try:
                timeout_value = float(timeout_entry.get().strip() or "30")
            except ValueError:
                messagebox.showerror("Invalid value", "Timeout must be a number.", parent=dialog)
                return
            mode = mode_var.get().strip() or "local"
            if mode not in ("local", "api"):
                mode = "local"
            if mode == "api" and not url_entry.get().strip():
                messagebox.showerror("Invalid value", "API URL is required for Remote API.", parent=dialog)
                return
            if mode == "api" and not key_entry.get().strip():
                messagebox.showerror("Invalid value", "API Key is required for Remote API.", parent=dialog)
                return
            if not model_entry.get().strip():
                messagebox.showerror("Invalid value", "Model is required.", parent=dialog)
                return

            previous_geometry = getattr(self._settings, "window_geometry", "920x560")
            previous_gui_timeout = getattr(self._settings, "gui_timeout_seconds", 45.0)
            previous_retries = getattr(self._settings, "install_retries", 2)
            previous_confirm = getattr(self._settings, "confirm_high_risk", True)
            self._settings = LLMSettings(
                provider_mode=mode,
                url=url_entry.get().strip(),
                api_key=key_entry.get().strip(),
                model=model_entry.get().strip() or OLLAMA_DEFAULT_MODEL,
                timeout=timeout_value,
                gui_timeout_seconds=previous_gui_timeout,
                install_retries=previous_retries,
                confirm_high_risk=previous_confirm,
                window_geometry=previous_geometry,
            )
            self._settings_store.save(self._settings)
            self.agent = self._build_agent()
            if self._settings.provider_mode == "api":
                self._append_message("Agent", "LLM settings saved in Remote API mode and applied.")
            else:
                self._append_message("Agent", "LLM settings saved in Local Model mode and applied.")
            dialog.destroy()

        button_frame = tk.Frame(dialog)
        button_frame.grid(row=6, column=0, columnspan=2, sticky="e", padx=12, pady=12)
        tk.Button(button_frame, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT, padx=(8, 0))
        tk.Button(button_frame, text="Save", command=save_and_close).pack(side=tk.RIGHT)

        self._fit_dialog_to_content(dialog, default_width=620, default_height=380)

    def run(self) -> None:
        self._apply_runtime_options()
        # Ensure maximized state at startup (keep decorations); schedule again after mapping
        self._maximize_window()
        try:
            self.root.after(100, self._maximize_window)
        except Exception:
            pass
        self.root.mainloop()


def run_gui() -> None:
    app = AgentChatGUI()
    app.run()
