import tkinter as tk
from tkinter import messagebox, scrolledtext
import re

from auto_system_agent.agent import AutoSystemAgent
from auto_system_agent.settings import LLMSettings, SettingsStore


class AgentChatGUI:
    """Minimal desktop chat interface for the Auto System Agent."""

    def __init__(self) -> None:
        self._settings_store = SettingsStore()
        self._settings = self._settings_store.load()
        self.agent = self._build_agent()
        self.root = tk.Tk()
        self.root.title("Auto System Agent")
        self.root.geometry("920x560")

        menu_bar = tk.Menu(self.root)
        settings_menu = tk.Menu(menu_bar, tearoff=0)
        settings_menu.add_command(label="LLM Settings", command=self._open_settings_dialog)
        menu_bar.add_cascade(label="Settings", menu=settings_menu)
        self.root.config(menu=menu_bar)

        content_frame = tk.Frame(self.root)
        content_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(12, 8))

        self.chat_log = scrolledtext.ScrolledText(
            content_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("TkDefaultFont", 11),
            padx=10,
            pady=10,
        )
        self.chat_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        progress_frame = tk.Frame(content_frame, width=260)
        progress_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(12, 0))
        progress_frame.pack_propagate(False)

        tk.Label(progress_frame, text="Execution Progress", font=("TkDefaultFont", 10, "bold")).pack(
            anchor="w", pady=(0, 6)
        )
        self.progress_list = tk.Listbox(progress_frame, height=16)
        self.progress_list.pack(fill=tk.BOTH, expand=True)
        self._step_progress_rows: dict[int, int] = {}

        bottom_frame = tk.Frame(self.root)
        bottom_frame.pack(fill=tk.X, padx=12, pady=(0, 12))

        self.entry = tk.Entry(bottom_frame, font=("TkDefaultFont", 11))
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry.bind("<Return>", self._on_send)

        self.send_button = tk.Button(bottom_frame, text="Send", command=self._on_send)
        self.send_button.pack(side=tk.LEFT, padx=(8, 0))

        self.confirm_button = tk.Button(
            bottom_frame,
            text="Confirm",
            command=self._on_confirm,
            state=tk.DISABLED,
        )
        self.confirm_button.pack(side=tk.LEFT, padx=(8, 0))

        self.cancel_button = tk.Button(
            bottom_frame,
            text="Cancel",
            command=self._on_cancel,
            state=tk.DISABLED,
        )
        self.cancel_button.pack(side=tk.LEFT, padx=(8, 0))

        self._append_message("Agent", "Welcome. Type help to see example commands.")

    def _append_message(self, speaker: str, message: str) -> None:
        self.chat_log.configure(state=tk.NORMAL)
        self.chat_log.insert(tk.END, f"{speaker}: {message}\n\n")
        self.chat_log.configure(state=tk.DISABLED)
        self.chat_log.see(tk.END)

    def _on_send(self, _event=None) -> None:
        user_input = self.entry.get().strip()
        if not user_input:
            return

        if str(self.send_button["state"]) == "disabled":
            return

        self.entry.delete(0, tk.END)
        self._append_message("You", user_input)

        if user_input.lower() in {"exit", "quit"}:
            self._append_message("Agent", "Closing chat window.")
            self.root.after(300, self.root.destroy)
            return

        self._reset_progress_panel()

        self.send_button.configure(state=tk.DISABLED)
        self.entry.configure(state=tk.DISABLED)

        def on_progress(message: str) -> None:
            self._append_message("Agent", message)
            self._update_progress_panel(message)
            self.root.update_idletasks()

        response = self.agent.process(user_input, progress_callback=on_progress)
        self._append_message("Agent", response)
        self.entry.configure(state=tk.NORMAL)
        self.send_button.configure(state=tk.NORMAL)
        self.entry.focus_set()
        self._sync_confirmation_controls()

    def _on_confirm(self) -> None:
        if not self.agent.has_pending_confirmation():
            self._sync_confirmation_controls()
            return

        self._append_message("You", "yes")
        self._reset_progress_panel()

        self.send_button.configure(state=tk.DISABLED)
        self.confirm_button.configure(state=tk.DISABLED)
        self.cancel_button.configure(state=tk.DISABLED)
        self.entry.configure(state=tk.DISABLED)

        def on_progress(message: str) -> None:
            self._append_message("Agent", message)
            self._update_progress_panel(message)
            self.root.update_idletasks()

        response = self.agent.confirm_pending(progress_callback=on_progress)
        if response:
            self._append_message("Agent", response)

        self.entry.configure(state=tk.NORMAL)
        self.send_button.configure(state=tk.NORMAL)
        self.entry.focus_set()
        self._sync_confirmation_controls()

    def _on_cancel(self) -> None:
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
        self.confirm_button.configure(state=tk.NORMAL if has_pending else tk.DISABLED)
        self.cancel_button.configure(state=tk.NORMAL if has_pending else tk.DISABLED)

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
