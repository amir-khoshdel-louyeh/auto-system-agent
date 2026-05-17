import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
import queue
import os
import threading
import time
from typing import Callable

from auto_system_agent.agent import AutoSystemAgent
from auto_system_agent.models import ExecutionResult, StepStatus
from auto_system_agent.real_terminal import RealTerminalFrame
from auto_system_agent.settings import LLMSettings, OLLAMA_DEFAULT_MODEL, OLLAMA_DEFAULT_URL, SettingsStore
from auto_system_agent.system_info import SystemConfig, detect_system_config, system_config_from_dict, system_config_to_dict


BG_APP = "#eef2f7"
BG_PANEL = "#ffffff"
BG_USER = "#dbeafe"
BG_AGENT = "#f0fdf4"
BG_AGENT_ERROR = "#fef2f2"
BG_SYSTEM = "#fffbeb"
FG_PRIMARY = "#1e293b"
FG_MUTED = "#64748b"
ACCENT = "#0f4c81"
ACCENT_LIGHT = "#3b82f6"
SUCCESS = "#16a34a"
SUCCESS_BG = "#dcfce7"
ERROR = "#dc2626"
ERROR_BG = "#fee2e2"


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
        self._task_timeout_seconds = float(os.getenv("AUTO_AGENT_GUI_TASK_TIMEOUT", "0") or "0")
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

        # Required system architecture window – user must review/accept OS/arch/package-manager
        self._show_system_config_dialog()

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
        settings_menu.add_separator()
        settings_menu.add_command(label="System Architecture", command=lambda: self._show_system_config_dialog(required=False))
        menu_bar.add_cascade(label="Settings", menu=settings_menu)
        # System menu for quick access
        system_menu = tk.Menu(menu_bar, tearoff=0)
        system_menu.add_command(label="System Architecture…", command=lambda: self._show_system_config_dialog(required=False))
        menu_bar.add_cascade(label="System", menu=system_menu)
        self.root.config(menu=menu_bar)

        # Main container: 1/4 chat | 3/4 terminal pinned (Alacritty style)
        main_container = tk.Frame(self.root, bg=BG_APP)
        main_container.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        main_container.grid_columnconfigure(0, weight=1, uniform="panel")
        main_container.grid_columnconfigure(1, weight=3, uniform="panel")
        main_container.grid_rowconfigure(0, weight=1)

        # Left panel (1/4): chat + input stacked vertically
        left_panel = tk.Frame(main_container, bg=BG_PANEL, highlightbackground="#d0d7e2", highlightthickness=1)
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left_panel.grid_rowconfigure(1, weight=1)
        left_panel.grid_columnconfigure(0, weight=1)

        # --- Agent header with status indicator ---
        agent_header = tk.Frame(left_panel, bg=ACCENT, height=42)
        agent_header.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        agent_header.grid_propagate(False)
        agent_header.grid_columnconfigure(0, weight=1)
        left_head = tk.Frame(agent_header, bg=ACCENT)
        left_head.grid(row=0, column=0, sticky="w", padx=12)
        tk.Label(left_head, text="⬢", font=("TkDefaultFont", 14), fg="#ffffff", bg=ACCENT).pack(side=tk.LEFT, padx=(0, 6))
        tk.Label(left_head, text="Agent", font=("Segoe UI", 11, "bold"), fg="#ffffff", bg=ACCENT).pack(side=tk.LEFT)
        tk.Label(left_head, text="Automated assistant", font=("Segoe UI", 8), fg="#cbd5e1", bg=ACCENT).pack(side=tk.LEFT, padx=(8, 0))
        # status dot + text
        self._agent_status_dot = tk.Label(agent_header, text="●", font=("TkDefaultFont", 10), fg=SUCCESS, bg=ACCENT)
        self._agent_status_dot.grid(row=0, column=1, sticky="e", padx=(0, 4))
        self._agent_status_label = tk.Label(agent_header, text="Ready", font=("Segoe UI", 8), fg="#e0f2fe", bg=ACCENT)
        self._agent_status_label.grid(row=0, column=2, sticky="e", padx=(0, 12))

        chat_container = tk.Frame(left_panel, bg=BG_PANEL)
        chat_container.grid(row=1, column=0, sticky="nsew", padx=6, pady=6)
        chat_container.grid_rowconfigure(0, weight=1)
        chat_container.grid_columnconfigure(0, weight=1)

        self.chat_log = scrolledtext.ScrolledText(
            chat_container,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("Segoe UI", 10),
            bg="#f8fafc",
            fg=FG_PRIMARY,
            borderwidth=0,
            relief=tk.FLAT,
            padx=16,
            pady=12,
            insertbackground=FG_PRIMARY,
            spacing1=4,
            spacing3=4,
        )
        self._configure_chat_styles()
        self.chat_log.grid(row=0, column=0, sticky="nsew")

        input_frame = tk.Frame(left_panel, bg=BG_PANEL)
        input_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 8))
        input_frame.grid_columnconfigure(0, weight=1)

        # Entry row – modern pill style
        entry_row = tk.Frame(input_frame, bg="#f1f5f9", highlightbackground="#cbd5e1", highlightthickness=1, bd=0)
        entry_row.grid(row=0, column=0, sticky="ew", pady=(0, 0), ipady=2)
        entry_row.grid_columnconfigure(0, weight=1)

        self.entry = tk.Entry(
            entry_row,
            font=("Segoe UI", 11),
            bg="#f1f5f9",
            fg=FG_PRIMARY,
            relief=tk.FLAT,
            borderwidth=0,
            highlightthickness=0,
            insertbackground=FG_PRIMARY,
            disabledbackground="#e2e8f0",
        )
        self.entry.grid(row=0, column=0, sticky="ew", padx=(12, 6), pady=8)
        try:
            self.entry.insert(0, "")
        except Exception:
            pass
        self.entry.bind("<Return>", self._on_send)
        # placeholder handling
        self._entry_placeholder = "Ask the agent..."
        self._entry_has_placeholder = False
        self._show_entry_placeholder()
        self.entry.bind("<FocusIn>", self._on_entry_focus_in)
        self.entry.bind("<FocusOut>", self._on_entry_focus_out)

        self.send_button = tk.Button(
            entry_row,
            text="➤ Send",
            command=self._on_send,
            bg=ACCENT,
            fg="#ffffff",
            activebackground="#0c3a62",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=14,
            pady=4,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            bd=0,
        )
        self.send_button.grid(row=0, column=1, padx=(0, 6), pady=4)

        # Right panel (3/4): pinned terminal – pty-backed bash, same session for AI and you
        right_panel = tk.Frame(main_container, bg=BG_PANEL, highlightbackground="#d0d7e2", highlightthickness=1)
        right_panel.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right_panel.grid_rowconfigure(1, weight=1)
        right_panel.grid_columnconfigure(0, weight=1)

        header_frame = tk.Frame(right_panel, bg=BG_PANEL)
        header_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 4))
        tk.Label(header_frame, text="Terminal", font=("TkDefaultFont", 10, "bold"), fg=ACCENT, bg=BG_PANEL).pack(anchor="w")

        # Real pty terminal (bash -i) – auto-started
        try:
            cwd_for_pty = self.agent._executor.terminal.cwd if hasattr(self.agent, "_executor") and hasattr(self.agent._executor, "terminal") else None
        except Exception:
            cwd_for_pty = None
        self.real_terminal = RealTerminalFrame(right_panel, cwd=cwd_for_pty, bg=BG_PANEL, highlightbackground="#0f172a", highlightthickness=1)
        self.real_terminal.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.terminal_text = self.real_terminal.text
        self._step_progress_rows: dict[int, int] = {}
        self.progress_list = self.terminal_text

        # Patch agent's TerminalSession to use the SAME pty (so AI commands run in this visible terminal)
        try:
            _orig_terminal = self.agent._executor.terminal
            real_term = self.real_terminal

            def _pty_run(cmd, timeout=300):
                out, code = real_term.run_command(cmd, timeout=timeout)
                try:
                    _orig_terminal._history.append(cmd)
                    if len(_orig_terminal._history) > 500:
                        _orig_terminal._history = _orig_terminal._history[-500:]
                    stripped = cmd.strip()
                    if stripped == "cd" or stripped.startswith("cd "):
                        import shlex
                        from pathlib import Path
                        try:
                            parts = shlex.split(stripped)
                            target = parts[1] if len(parts) > 1 else "~"
                            if target == "~":
                                p = Path.home().resolve()
                            elif target.startswith("~"):
                                p = Path(target).expanduser().resolve()
                            elif target.startswith("/"):
                                p = Path(target).resolve()
                            else:
                                p = (_orig_terminal._cwd / target).resolve()
                            if p.exists() and p.is_dir():
                                _orig_terminal._cwd = p
                        except Exception:
                            pass
                except Exception:
                    pass
                success = code == 0
                msg = out.strip() if out.strip() else (f"Command executed: {cmd}" if success else f"Command failed (exit {code}): {cmd}")
                if len(msg) > 6000:
                    msg = msg[-6000:]
                return ExecutionResult(success=success, message=msg, data={"exit_code": code, "command": cmd})

            _orig_terminal.run = _pty_run
        except Exception as e:
            # If patch fails, keep original terminal but log
            try:
                self.real_terminal.text.configure(state=tk.NORMAL)
                self.real_terminal.text.insert(tk.END, f"Failed to attach pty to executor: {e}\n")
                self.real_terminal.text.configure(state=tk.DISABLED)
            except Exception:
                pass

        self._append_message("Agent", "Welcome. Type help to see example commands.")
        self.root.after(50, self._drain_ui_queue)

    def _configure_chat_styles(self) -> None:
        # --- Header labels (who) ---
        self.chat_log.tag_configure(
            "who_you",
            foreground=ACCENT,
            font=("Segoe UI", 8, "bold"),
            justify="right",
            rmargin=16,
            spacing1=14,
            spacing3=2,
        )
        self.chat_log.tag_configure(
            "who_agent",
            foreground="#065f46",
            font=("Segoe UI", 8, "bold"),
            justify="left",
            lmargin1=16,
            lmargin2=16,
            spacing1=14,
            spacing3=2,
        )
        self.chat_log.tag_configure(
            "who_agent_success",
            foreground=SUCCESS,
            font=("Segoe UI", 8, "bold"),
            justify="left",
            lmargin1=16,
            lmargin2=16,
            spacing1=14,
            spacing3=2,
        )
        self.chat_log.tag_configure(
            "who_agent_error",
            foreground=ERROR,
            font=("Segoe UI", 8, "bold"),
            justify="left",
            lmargin1=16,
            lmargin2=16,
            spacing1=14,
            spacing3=2,
        )
        self.chat_log.tag_configure(
            "who_system",
            foreground="#92400e",
            font=("Segoe UI", 8),
            justify="center",
            spacing1=6,
            spacing3=2,
        )
        # --- Bubbles ---
        self.chat_log.tag_configure(
            "bubble_you",
            background=BG_USER,
            foreground="#1e3a5f",
            font=("Segoe UI", 10),
            justify="right",
            rmargin=16,
            lmargin1=60,
            lmargin2=60,
            spacing1=4,
            spacing3=10,
            borderwidth=0,
            relief="flat",
        )
        self.chat_log.tag_configure(
            "bubble_agent",
            background=BG_AGENT,
            foreground="#14532d",
            font=("Segoe UI", 10),
            justify="left",
            lmargin1=16,
            lmargin2=16,
            rmargin=60,
            spacing1=4,
            spacing3=10,
        )
        self.chat_log.tag_configure(
            "bubble_agent_success",
            background=SUCCESS_BG,
            foreground="#14532d",
            font=("Segoe UI", 10, "bold"),
            justify="left",
            lmargin1=16,
            lmargin2=16,
            rmargin=60,
            spacing1=4,
            spacing3=10,
        )
        self.chat_log.tag_configure(
            "bubble_agent_error",
            background=ERROR_BG,
            foreground="#7f1d1d",
            font=("Segoe UI", 10, "bold"),
            justify="left",
            lmargin1=16,
            lmargin2=16,
            rmargin=60,
            spacing1=4,
            spacing3=10,
        )
        self.chat_log.tag_configure(
            "bubble_system",
            background=BG_SYSTEM,
            foreground="#78350f",
            font=("Segoe UI", 8, "italic"),
            justify="center",
            lmargin1=40,
            lmargin2=40,
            rmargin=40,
            spacing1=4,
            spacing3=10,
        )
        self.chat_log.tag_configure(
            "timestamp",
            foreground="#94a3b8",
            font=("Segoe UI", 7),
            justify="right" if False else "left",
            spacing3=2,
        )

    def _append_message(self, speaker: str, message: str) -> None:
        # Only show user and agent; hide verbose System progress as requested,
        # but keep welcome/important system as subtle center note if needed.
        import time as _time
        ts = _time.strftime("%H:%M")
        # Simplify agent output to success / error only
        if speaker == "Agent":
            low = message.lower()
            is_error = ("[error]" in low or " error" in low or "failed" in low or "✗" in message)
            is_success = ("[success]" in low or "success" in low or "succeeded" in low or "✓" in message)
            # Welcome message is neutral
            if message.strip().lower().startswith("welcome"):
                who_tag = "who_agent"
                body_tag = "bubble_agent"
                label = "◆ Agent  " + ts
            elif is_error and not is_success:
                who_tag = "who_agent_error"
                body_tag = "bubble_agent_error"
                label = "✗ Agent  " + ts
                # Show LLM explanation in chat when available, otherwise concise fallback
                if len(message.strip()) < 25 or message.strip().lower() == "✗ error — see terminal for details.":
                    message = "✗ Error — see terminal for details."
                # Keep the actual LLM-generated explanation (no generic replacement)
            elif is_success:
                who_tag = "who_agent_success"
                body_tag = "bubble_agent_success"
                label = "✓ Agent  " + ts
                message = "✓ Succeeded — see terminal for output."
            else:
                # Generic agent (e.g., help) keep as is but with success style
                who_tag = "who_agent"
                body_tag = "bubble_agent"
                label = "◆ Agent  " + ts
        elif speaker == "You":
            who_tag = "who_you"
            body_tag = "bubble_you"
            label = "You  " + ts + "  ●"
        elif speaker == "System":
            # Hide noisy progress; only show critical system notes as subtle center text
            # Keep welcome/empty? For now show as subtle system but could also hide.
            # To strictly follow 'only user + AI', return without displaying.
            return
        else:
            who_tag = "who_agent"
            body_tag = "bubble_agent"
            label = "◆ Agent  " + ts

        self.chat_log.configure(state=tk.NORMAL)
        self.chat_log.insert(tk.END, f"{label}\n", who_tag)
        # Add subtle bubble padding via leading space
        self.chat_log.insert(tk.END, f"  {message}\n", body_tag)
        self.chat_log.insert(tk.END, "\n")
        self.chat_log.configure(state=tk.DISABLED)
        self.chat_log.see(tk.END)

    # --- Entry placeholder & status helpers ---
    def _show_entry_placeholder(self) -> None:
        try:
            if not self.entry.get().strip():
                self.entry.delete(0, tk.END)
                self.entry.insert(0, self._entry_placeholder)
                self.entry.configure(fg="#94a3b8")
                self._entry_has_placeholder = True
        except Exception:
            pass

    def _clear_entry_placeholder(self) -> None:
        try:
            if self._entry_has_placeholder:
                self.entry.delete(0, tk.END)
                self.entry.configure(fg=FG_PRIMARY)
                self._entry_has_placeholder = False
        except Exception:
            pass

    def _on_entry_focus_in(self, event=None) -> None:
        self._clear_entry_placeholder()

    def _on_entry_focus_out(self, event=None) -> None:
        if not self.entry.get().strip():
            self._show_entry_placeholder()

    def _set_agent_status(self, state: str) -> None:
        try:
            if state == "busy":
                self._agent_status_dot.configure(fg="#f59e0b")
                self._agent_status_label.configure(text="Working…")
            elif state == "error":
                self._agent_status_dot.configure(fg=ERROR)
                self._agent_status_label.configure(text="Error")
            elif state == "success":
                self._agent_status_dot.configure(fg=SUCCESS)
                self._agent_status_label.configure(text="Done")
            else:
                self._agent_status_dot.configure(fg=SUCCESS)
                self._agent_status_label.configure(text="Ready")
        except Exception:
            pass

    def _on_send(self, _event=None) -> None:
        # Handle placeholder
        if getattr(self, "_entry_has_placeholder", False):
            return
        user_input = self.entry.get().strip()
        if not user_input or user_input == getattr(self, "_entry_placeholder", ""):
            return

        if self._is_busy or str(self.send_button["state"]) == "disabled":
            return

        self.entry.delete(0, tk.END)
        self._show_entry_placeholder()
        # Keep placeholder invisible while busy? clear it so next focus shows empty
        self._clear_entry_placeholder()
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
        # Confirmation UI removed – no-op kept for compatibility
        return

    def _on_cancel(self) -> None:
        # Only handles cancelling a running request; confirmation removed
        if self._is_busy:
            if self._active_request_id is not None:
                self._cancelled_request_ids.add(self._active_request_id)
            self._append_message("Agent", "[ERROR] Cancelled")
            self._set_agent_status("error")
            self._active_request_id = None
            self._request_started_at = None
            self._set_busy(False)

    def _sync_confirmation_controls(self) -> None:
        # Removed confirmation UI – no controls to sync
        return

    def _render_pending_confirmation_card(self) -> None:
        return

    def _copy_preview_text(self) -> None:
        return

    def _set_confirmation_status(self, status: str, details: str, color: str) -> None:
        return

    def _set_busy(self, busy: bool) -> None:
        self._is_busy = busy
        self.send_button.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.entry.configure(state=tk.DISABLED if busy else tk.NORMAL)
        # Visual status + button text
        try:
            if busy:
                self.send_button.configure(text="◌ Working…", bg="#64748b")
                self._set_agent_status("busy")
                self.entry.configure(bg="#e2e8f0")
            else:
                self.send_button.configure(text="➤ Send", bg=ACCENT)
                self._set_agent_status("ready")
                self.entry.configure(bg="#f1f5f9")
                # restore placeholder if empty
                if not self.entry.get().strip():
                    self._show_entry_placeholder()
        except Exception:
            pass

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
        # Condition-based timeout: do not disturb while busy.
        # Only timeout if idle (no progress) AND terminal not executing. If terminal/worker is busy, keep waiting.
        is_terminal_busy = False
        try:
            if hasattr(self, "real_terminal") and self.real_terminal is not None:
                # _exec_lock is held during run_command
                if hasattr(self.real_terminal, "_exec_lock") and self.real_terminal._exec_lock.locked():
                    is_terminal_busy = True
                # also check pty output activity via recent queue if needed
        except Exception:
            is_terminal_busy = False

        if self._is_busy and self._request_started_at is not None and self._task_timeout_seconds > 0 and not is_terminal_busy:
            elapsed = time.time() - self._request_started_at
            if elapsed > self._task_timeout_seconds and self._active_request_id is not None:
                self._cancelled_request_ids.add(self._active_request_id)
                # Timeout is an error – show simplified agent error
                self._append_message("Agent", "[ERROR] Request timed out")
                self._set_agent_status("error")
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
                    # Hide verbose progress per user request – only update internal tracking
                    self._update_progress_panel(status)
                    # Reset idle timer on progress so long jobs with steps don't time out
                    self._request_started_at = time.time()
                    # Optionally pulse status dot but no chat bubble
                elif event_type == "response" and isinstance(payload, tuple):
                    request_id, response = payload
                    if self._should_accept_event(request_id) and response is not None:
                        txt = str(response)
                        self._append_message("Agent", txt)
                        # Update status dot based on success/error
                        low = txt.lower()
                        if "[error]" in low or "failed" in low or "error" in low:
                            self._set_agent_status("error")
                        else:
                            self._set_agent_status("success")
                elif event_type == "error" and isinstance(payload, tuple):
                    request_id, error_text = payload
                    if self._should_accept_event(request_id) and error_text is not None:
                        self._append_message("Agent", f"[ERROR] {error_text}")
                        self._set_agent_status("error")
                elif event_type == "done" and isinstance(payload, tuple):
                    request_id, _ = payload
                    if self._active_request_id == request_id:
                        self._active_request_id = None
                        self._request_started_at = None
                        self._set_busy(False)
                        try:
                            self.entry.focus_set()
                            if not self.entry.get().strip():
                                self._show_entry_placeholder()
                        except Exception:
                            pass
        except queue.Empty:
            pass

        self.root.after(50, self._drain_ui_queue)

    def _reset_progress_panel(self) -> None:
        self._step_progress_rows.clear()

    def _clear_timeline(self) -> None:
        # Timeline removed – no-op kept for compatibility
        return

    def _insert_tool_command(self, command_text: str) -> None:
        if hasattr(self, "entry"):
            self._clear_entry_placeholder()
            self.entry.delete(0, tk.END)
            self.entry.insert(0, command_text)
            self.entry.configure(fg=FG_PRIMARY)
            self._entry_has_placeholder = False
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
                gui_timeout_seconds=getattr(prev, "gui_timeout_seconds", 300.0),
                install_retries=getattr(prev, "install_retries", 2),
                confirm_high_risk=False,
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

    def _show_system_config_dialog(self, required: bool = True) -> None:
        """Required window after provider: show detected OS/arch/hardware and let user modify/accept.

        Needed so installs use correct manager (apt/dnf/pacman/snap/flatpak) and
        LLM generates accurate commands for the host. If required=True, closing
        without Accept exits the app; if False (menu), just closes.
        """
        # Load persisted or freshly detected
        try:
            from auto_system_agent.system_info import detect_system_config

            persisted = self._settings.system_config if isinstance(self._settings.system_config, dict) else None
            detected = detect_system_config()
            # If persisted exists, start from it; otherwise from detected
            if isinstance(persisted, dict) and persisted:
                cfg = system_config_from_dict(persisted)
                # Fill missing hardware with detected fallback
                if not cfg.cpu_model:
                    cfg.cpu_model = detected.cpu_model
                if not cfg.cpu_cores:
                    cfg.cpu_cores = detected.cpu_cores
                if not cfg.ram_gb:
                    cfg.ram_gb = detected.ram_gb
            else:
                cfg = detected
        except Exception:
            cfg = detect_system_config()

        accepted = {"done": False}

        dialog = tk.Toplevel(self.root)
        dialog.title("System Configuration — Required")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(True, True)
        dialog.focus_set()

        def exit_app():
            try:
                dialog.destroy()
            except Exception:
                pass
            if not required:
                return
            try:
                self.root.destroy()
            except Exception:
                pass
            import sys as _sys
            _sys.exit(0)

        def on_close():
            if not accepted["done"]:
                if required:
                    exit_app()
                else:
                    try:
                        dialog.destroy()
                    except Exception:
                        pass

        dialog.protocol("WM_DELETE_WINDOW", on_close)

        header = tk.Frame(dialog, bg=BG_PANEL, padx=16, pady=12)
        header.pack(fill=tk.X)
        tk.Label(header, text="System Architecture — Please review", font=("TkDefaultFont", 12, "bold"), fg=ACCENT, bg=BG_PANEL).pack(anchor="w")
        tk.Label(
            header,
            text="Detected your OS, architecture and package managers. Installing apps needs the right manager (apt for Debian/Ubuntu, dnf for Fedora, pacman for Arch, snap/flatpak if available). Review, modify if needed, then Accept. This config will be used to generate correct commands.",
            font=("TkDefaultFont", 9), fg=FG_MUTED, bg=BG_PANEL, wraplength=640, justify=tk.LEFT
        ).pack(anchor="w", pady=(4, 0))

        body = tk.Frame(dialog, padx=16, pady=8)
        body.pack(fill=tk.BOTH, expand=True)

        # Use canvas+scrollbar for many fields
        canvas = tk.Canvas(body, bg=BG_PANEL, highlightthickness=0)
        vscroll = ttk.Scrollbar(body, orient=tk.VERTICAL, command=canvas.yview)
        inner = tk.Frame(canvas, bg=BG_PANEL)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vscroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vscroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Helper to add row
        def add_combo_row(parent, label, row, values, initial, width=28):
            tk.Label(parent, text=label, bg=BG_PANEL, anchor="w").grid(row=row, column=0, sticky="w", padx=6, pady=4)
            var = tk.StringVar(value=initial)
            cb = ttk.Combobox(parent, textvariable=var, values=values, width=width, state="normal")
            cb.grid(row=row, column=1, sticky="we", padx=6, pady=4)
            return var, cb

        def add_entry_row(parent, label, row, initial, width=40):
            tk.Label(parent, text=label, bg=BG_PANEL, anchor="w").grid(row=row, column=0, sticky="w", padx=6, pady=4)
            ent = tk.Entry(parent, width=width)
            ent.grid(row=row, column=1, sticky="we", padx=6, pady=4)
            ent.insert(0, initial or "")
            return ent

        inner.grid_columnconfigure(1, weight=1)
        r = 0
        os_var, os_cb = add_combo_row(inner, "OS", r, ["linux", "windows", "macos"], cfg.os_name); r += 1
        distro_vals = ["ubuntu", "debian", "fedora", "arch", "manjaro", "opensuse-leap", "opensuse-tumbleweed", "alpine", "linuxmint", "pop", "rhel", "centos", "rocky", "unknown"]
        distro_var, distro_cb = add_combo_row(inner, "Distro ID", r, distro_vals, cfg.distro_id); r += 1
        distro_ver_ent = add_entry_row(inner, "Version", r, cfg.distro_version); r += 1
        pretty_ent = add_entry_row(inner, "Pretty Name", r, cfg.distro_pretty); r += 1
        arch_var, arch_cb = add_combo_row(inner, "Architecture", r, ["x86_64", "aarch64", "armv7l", "i386", "ppc64le", "s390x"], cfg.arch); r += 1
        kernel_ent = add_entry_row(inner, "Kernel", r, cfg.kernel); r += 1
        cpu_model_ent = add_entry_row(inner, "CPU Model", r, cfg.cpu_model); r += 1
        tk.Label(inner, text="CPU Cores", bg=BG_PANEL).grid(row=r, column=0, sticky="w", padx=6, pady=4)
        cores_var = tk.StringVar(value=str(cfg.cpu_cores))
        cores_spin = tk.Spinbox(inner, from_=1, to=256, textvariable=cores_var, width=8)
        cores_spin.grid(row=r, column=1, sticky="w", padx=6, pady=4); r += 1
        ram_ent = add_entry_row(inner, "RAM (GB)", r, f"{cfg.ram_gb:.1f}" if cfg.ram_gb else ""); r += 1
        pkg_vals = ["apt", "dnf", "pacman", "zypper", "apk", "snap", "flatpak", "brew", "winget", "unknown"]
        pkg_var, pkg_cb = add_combo_row(inner, "Primary Package Manager *", r, pkg_vals, cfg.package_manager); r += 1
        tk.Label(inner, text="* Used for `install` commands (snap/flatpak also available as fallback)", bg=BG_PANEL, fg=FG_MUTED, font=("TkDefaultFont", 7)).grid(row=r, column=0, columnspan=2, sticky="w", padx=6, pady=(0,4)); r+=1

        snap_var = tk.BooleanVar(value=bool(cfg.snap_available))
        flatpak_var = tk.BooleanVar(value=bool(cfg.flatpak_available))
        brew_var = tk.BooleanVar(value=bool(cfg.brew_available))
        tk.Checkbutton(inner, text="Snap available (snap install)", variable=snap_var, bg=BG_PANEL, anchor="w").grid(row=r, column=0, columnspan=2, sticky="w", padx=6, pady=2); r+=1
        tk.Checkbutton(inner, text="Flatpak available (flatpak install)", variable=flatpak_var, bg=BG_PANEL, anchor="w").grid(row=r, column=0, columnspan=2, sticky="w", padx=6, pady=2); r+=1
        tk.Checkbutton(inner, text="Homebrew available (brew install)", variable=brew_var, bg=BG_PANEL, anchor="w").grid(row=r, column=0, columnspan=2, sticky="w", padx=6, pady=2); r+=1
        hw_ent = add_entry_row(inner, "Hardware Summary", r, cfg.hardware_summary); r+=1

        note = tk.Label(inner, text="You can change package manager if you prefer snap/flatpak even though your distro default is different.\nThis choice directly affects commands like `sudo apt install -y vlc` vs `sudo dnf install -y vlc` vs `snap install vlc`.", fg=FG_MUTED, bg=BG_PANEL, font=("TkDefaultFont", 8), wraplength=580, justify=tk.LEFT)
        note.grid(row=r, column=0, columnspan=2, sticky="w", padx=6, pady=8); r+=1

        def do_detect_again():
            try:
                fresh = detect_system_config()
                os_var.set(fresh.os_name)
                distro_var.set(fresh.distro_id)
                distro_ver_ent.delete(0, tk.END); distro_ver_ent.insert(0, fresh.distro_version)
                pretty_ent.delete(0, tk.END); pretty_ent.insert(0, fresh.distro_pretty)
                arch_var.set(fresh.arch)
                kernel_ent.delete(0, tk.END); kernel_ent.insert(0, fresh.kernel)
                cpu_model_ent.delete(0, tk.END); cpu_model_ent.insert(0, fresh.cpu_model)
                cores_var.set(str(fresh.cpu_cores))
                ram_ent.delete(0, tk.END); ram_ent.insert(0, f"{fresh.ram_gb:.1f}" if fresh.ram_gb else "")
                pkg_var.set(fresh.package_manager)
                snap_var.set(bool(fresh.snap_available))
                flatpak_var.set(bool(fresh.flatpak_available))
                brew_var.set(bool(fresh.brew_available))
                hw_ent.delete(0, tk.END); hw_ent.insert(0, fresh.hardware_summary)
            except Exception as e:
                messagebox.showerror("Detect failed", str(e), parent=dialog)

        btn_frame = tk.Frame(dialog, padx=16, pady=12)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)
        tk.Button(btn_frame, text="Detect Again", command=do_detect_again, bg="#e5e7eb", fg="#1f2937", relief=tk.FLAT, padx=12).pack(side=tk.LEFT)
        tk.Button(btn_frame, text="Exit", command=exit_app, bg="#6b7280", fg="#ffffff", relief=tk.FLAT, padx=12).pack(side=tk.RIGHT, padx=(8,0))
        # Accept handler
        def on_accept():
            # Validate
            arch_v = arch_var.get().strip() or "x86_64"
            pkg_v = pkg_var.get().strip() or "unknown"
            if pkg_v not in pkg_vals:
                messagebox.showerror("Invalid", f"Package manager must be one of {', '.join(pkg_vals)}", parent=dialog)
                return
            try:
                cores_i = int(float(cores_var.get().strip() or "0"))
                ram_f = float(ram_ent.get().strip() or "0")
            except ValueError:
                messagebox.showerror("Invalid", "CPU cores must be integer, RAM must be numeric", parent=dialog)
                return
            new_cfg = SystemConfig(
                os_name=os_var.get().strip() or "linux",
                distro_id=distro_var.get().strip().lower() or "unknown",
                distro_version=distro_ver_ent.get().strip(),
                distro_pretty=pretty_ent.get().strip(),
                arch=arch_v,
                kernel=kernel_ent.get().strip(),
                cpu_model=cpu_model_ent.get().strip(),
                cpu_cores=max(0, cores_i),
                ram_gb=max(0.0, ram_f),
                package_manager=pkg_v,
                snap_available=bool(snap_var.get()),
                flatpak_available=bool(flatpak_var.get()),
                brew_available=bool(brew_var.get()),
                hardware_summary=hw_ent.get().strip(),
            )
            # Persist
            self._settings.system_config = system_config_to_dict(new_cfg)
            try:
                self._settings_store.save(self._settings)
            except Exception as exc:
                messagebox.showerror("Save failed", str(exc), parent=dialog)
                return
            accepted["done"] = True
            try:
                dialog.grab_release()
            except Exception:
                pass
            dialog.destroy()

        tk.Button(btn_frame, text="Accept", command=on_accept, bg=ACCENT, fg="#ffffff", relief=tk.FLAT, padx=12).pack(side=tk.RIGHT)
        dialog.bind("<Return>", lambda e: on_accept())
        self._fit_dialog_to_content(dialog, default_width=720, default_height=620)
        dialog.wait_window()
        if not accepted["done"] and required:
            exit_app()
        # If required and accepted, rebuild agent with new system config
        if accepted["done"]:
            try:
                self.agent = self._build_agent()
                self._append_message("System", f"System config accepted: {pkg_var.get()} on {distro_var.get()} {arch_var.get()} | snap={snap_var.get()} flatpak={flatpak_var.get()}")
            except Exception:
                pass

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
        # Properly terminate pinned terminal pty and bash
        try:
            if hasattr(self, "real_terminal") and self.real_terminal is not None:
                self.real_terminal.destroy()
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
        tk.Label(header, text="Available Tools", font=("TkDefaultFont", 12, "bold"), fg=ACCENT, bg=BG_PANEL).pack(anchor="w")
        tk.Label(
            header,
            text="All requests run directly as bash commands (bash -lc). No per-request Python tool.",
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

        # Shell-centric catalog
        catalog = [
            {
                "name": "Shell Commands (bash -lc)",
                "actions": ["run_command"],
                "description": "Every user request is translated to an exact shell command and executed via bash -lc with cwd tracking (pwd/cd/history).",
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
                "description": "System tasks are shell commands, no separate installer tool.",
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

        tk.Label(app_frame, text="These options apply immediately and are saved in local settings.", fg=FG_MUTED, bg=BG_PANEL, justify=tk.LEFT).grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=8)
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
                gui_timeout = float(app_timeout_entry.get().strip() or "300")
                install_retries = int(app_retries_entry.get().strip() or "2")
            except ValueError:
                messagebox.showerror("Invalid value", "Timeout must be numeric and retries must be integer.", parent=window)
                return
            if gui_timeout < 0:
                messagebox.showerror("Invalid value", "GUI timeout must be >= 0 (0 disables timeout).", parent=window)
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
                confirm_high_risk=False,
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

        helper = tk.Label(
            dialog,
            text="These options apply immediately and are saved in local settings.",
            fg=FG_MUTED,
            justify=tk.LEFT,
        )
        helper.grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=8)

        def save_and_close() -> None:
            try:
                gui_timeout = float(timeout_entry.get().strip() or "300")
                install_retries = int(retries_entry.get().strip() or "2")
            except ValueError:
                messagebox.showerror("Invalid value", "Timeout must be numeric and retries must be integer.", parent=dialog)
                return

            if gui_timeout < 0:
                messagebox.showerror("Invalid value", "GUI timeout must be >= 0 (0 disables timeout).", parent=dialog)
                return
            if install_retries < 0:
                messagebox.showerror("Invalid value", "Install retries cannot be negative.", parent=dialog)
                return

            self._settings.gui_timeout_seconds = gui_timeout
            self._settings.install_retries = install_retries
            self._settings.confirm_high_risk = False
            self._settings_store.save(self._settings)
            self._apply_runtime_options()
            self._append_message("System", "App options saved and applied.")
            dialog.destroy()

        button_frame = tk.Frame(dialog)
        button_frame.grid(row=3, column=0, columnspan=2, sticky="e", padx=12, pady=12)
        tk.Button(button_frame, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT, padx=(8, 0))
        tk.Button(button_frame, text="Save", command=save_and_close).pack(side=tk.RIGHT)

        self._fit_dialog_to_content(dialog, default_width=520, default_height=260)

    def _set_step_status(self, step: int, total: int, state: str, tool: str) -> None:
        text = f"[{step}/{total}] {state.upper():<7} {tool}"
        self._step_progress_rows[step] = text

    def _update_progress_panel(self, status: StepStatus) -> None:
        self._set_step_status(status.step, status.total, status.state, status.tool)

    def _status_to_text(self, status: StepStatus) -> str:
        if status.state == "running":
            return f"Step {status.step}/{status.total}: running {status.tool}..."
        if status.state == "done":
            return f"Step {status.step}/{status.total} finished {status.tool} (ok)."
        return f"Step {status.step}/{status.total} finished {status.tool} (failed)."

    def _timeline_text_for_status(self, status: StepStatus) -> str:
        # Timeline removed – kept for compatibility
        return ""

    def _append_timeline(self, text: str) -> None:
        # Timeline removed – no-op kept for compatibility
        return

    def _should_accept_event(self, request_id: int) -> bool:
        if request_id in self._cancelled_request_ids:
            return False
        return self._active_request_id == request_id

    def _build_agent(self) -> AutoSystemAgent:
        config = self._settings_store.resolve_llm_config(self._settings)
        # System config is now required for correct package-manager commands (apt/dnf/snap/flatpak)
        system_cfg = None
        try:
            if isinstance(self._settings.system_config, dict) and self._settings.system_config:
                system_cfg = self._settings.system_config
            else:
                # Fallback to detection (should have been set via system window)
                from auto_system_agent.system_info import system_config_to_dict, detect_system_config
                system_cfg = system_config_to_dict(detect_system_config())
        except Exception:
            system_cfg = None
        # Confirmation removed – always execute without pending confirmation
        return AutoSystemAgent(llm_config=config, confirm_high_risk=False, system_config=system_cfg)

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
            previous_gui_timeout = getattr(self._settings, "gui_timeout_seconds", 300.0)
            previous_retries = getattr(self._settings, "install_retries", 2)
            self._settings = LLMSettings(
                provider_mode=mode,
                url=url_entry.get().strip(),
                api_key=key_entry.get().strip(),
                model=model_entry.get().strip() or OLLAMA_DEFAULT_MODEL,
                timeout=timeout_value,
                gui_timeout_seconds=previous_gui_timeout,
                install_retries=previous_retries,
                confirm_high_risk=False,
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
