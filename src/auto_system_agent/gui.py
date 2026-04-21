import tkinter as tk
from tkinter import messagebox, scrolledtext
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
        dialog.geometry("680x560")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.focus_set()
        # Keep dialog centered over root
        try:
            dialog.update_idletasks()
            x = self.root.winfo_rootx() + (self.root.winfo_width() // 2) - 340
            y = self.root.winfo_rooty() + (self.root.winfo_height() // 2) - 280
            dialog.geometry(f"680x560+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

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

        # Modal block
        dialog.wait_window()

        if not choice_made["done"]:
            # User closed without choosing -> exit already handled, but ensure
            exit_app()

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
