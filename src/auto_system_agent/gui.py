import tkinter as tk
from tkinter import messagebox, scrolledtext
import queue
import re
import threading
from typing import Callable

from auto_system_agent.agent import AutoSystemAgent
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
        self._ui_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
        self.root = tk.Tk()
        self.root.title("Auto System Agent")
        self.root.geometry("920x560")
        self.root.configure(bg=BG_APP)

        menu_bar = tk.Menu(self.root)
        settings_menu = tk.Menu(menu_bar, tearoff=0)
        settings_menu.add_command(label="LLM Settings", command=self._open_settings_dialog)
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
            self.cancel_button.configure(state=tk.DISABLED)
            return

        self.confirm_button.configure(state=tk.NORMAL if has_pending else tk.DISABLED)
        self.cancel_button.configure(state=tk.NORMAL if has_pending else tk.DISABLED)

    def _set_busy(self, busy: bool) -> None:
        self._is_busy = busy
        self.send_button.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.entry.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self._sync_confirmation_controls()

    def _start_background_task(self, task_fn: Callable[[Callable[[str], None]], str | None]) -> None:
        self._set_busy(True)

        def worker() -> None:
            def on_progress(message: str) -> None:
                self._ui_queue.put(("progress", message))

            try:
                response = task_fn(on_progress)
                if response:
                    self._ui_queue.put(("response", response))
            except Exception as exc:
                self._ui_queue.put(("error", f"Unexpected error while processing request: {exc}"))
            finally:
                self._ui_queue.put(("done", None))

        threading.Thread(target=worker, daemon=True).start()

    def _drain_ui_queue(self) -> None:
        try:
            while True:
                event_type, payload = self._ui_queue.get_nowait()
                if event_type == "progress" and payload is not None:
                    self._append_message("System", payload)
                    self._update_progress_panel(payload)
                elif event_type == "response" and payload is not None:
                    self._append_message("Agent", payload)
                elif event_type == "error" and payload is not None:
                    self._append_message("System", payload)
                elif event_type == "done":
                    self._set_busy(False)
                    self.entry.focus_set()
        except queue.Empty:
            pass

        self.root.after(50, self._drain_ui_queue)

    def _reset_progress_panel(self) -> None:
        self.progress_list.delete(0, tk.END)
        self._step_progress_rows.clear()

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

    def _update_progress_panel(self, message: str) -> None:
        running_match = re.match(r"Step\s+(\d+)/(\d+):\s+running\s+(.+)\.\.\.$", message)
        if running_match:
            step = int(running_match.group(1))
            total = int(running_match.group(2))
            tool = running_match.group(3).strip()
            self._set_step_status(step, total, "running", tool)
            return

        finished_match = re.match(
            r"Step\s+(\d+)/(\d+)\s+finished\s+(.+)\s+\((ok|failed)\)\.$",
            message,
        )
        if finished_match:
            step = int(finished_match.group(1))
            total = int(finished_match.group(2))
            tool = finished_match.group(3).strip()
            state = "done" if finished_match.group(4) == "ok" else "failed"
            self._set_step_status(step, total, state, tool)
            return

        single_running = re.match(r"Running\s+(.+)\.\.\.$", message)
        if single_running:
            self._set_step_status(1, 1, "running", single_running.group(1).strip())
            return

        single_finished = re.match(r"Finished\s+(.+)\s+\((ok|failed)\)\.$", message)
        if single_finished:
            state = "done" if single_finished.group(2) == "ok" else "failed"
            self._set_step_status(1, 1, state, single_finished.group(1).strip())

    def _build_agent(self) -> AutoSystemAgent:
        config = {
            "url": self._settings.url,
            "api_key": self._settings.api_key,
            "model": self._settings.model,
            "timeout": self._settings.timeout,
        }
        return AutoSystemAgent(llm_config=config)

    def _open_settings_dialog(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("LLM Settings")
        dialog.geometry("520x260")
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
        url_entry = add_row("LLM URL", 0, self._settings.url)
        key_entry = add_row("API Key", 1, self._settings.api_key, show="*")
        model_entry = add_row("Model", 2, self._settings.model)
        timeout_entry = add_row("Timeout (seconds)", 3, str(self._settings.timeout))

        def save_and_close() -> None:
            try:
                timeout_value = float(timeout_entry.get().strip() or "8")
            except ValueError:
                messagebox.showerror("Invalid value", "Timeout must be a number.", parent=dialog)
                return

            self._settings = LLMSettings(
                url=url_entry.get().strip(),
                api_key=key_entry.get().strip(),
                model=model_entry.get().strip() or "gpt-4o-mini",
                timeout=timeout_value,
            )
            self._settings_store.save(self._settings)
            self.agent = self._build_agent()
            self._append_message("Agent", "LLM settings saved and applied.")
            dialog.destroy()

        button_frame = tk.Frame(dialog)
        button_frame.grid(row=4, column=0, columnspan=2, sticky="e", padx=12, pady=12)
        tk.Button(button_frame, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT, padx=(8, 0))
        tk.Button(button_frame, text="Save", command=save_and_close).pack(side=tk.RIGHT)

    def run(self) -> None:
        self.root.mainloop()


def run_gui() -> None:
    app = AgentChatGUI()
    app.run()
