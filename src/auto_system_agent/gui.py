import tkinter as tk
from tkinter import messagebox, scrolledtext

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
        self.root.geometry("760x520")

        menu_bar = tk.Menu(self.root)
        settings_menu = tk.Menu(menu_bar, tearoff=0)
        settings_menu.add_command(label="LLM Settings", command=self._open_settings_dialog)
        menu_bar.add_cascade(label="Settings", menu=settings_menu)
        self.root.config(menu=menu_bar)

        self.chat_log = scrolledtext.ScrolledText(
            self.root,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("TkDefaultFont", 11),
            padx=10,
            pady=10,
        )
        self.chat_log.pack(fill=tk.BOTH, expand=True, padx=12, pady=(12, 8))

        bottom_frame = tk.Frame(self.root)
        bottom_frame.pack(fill=tk.X, padx=12, pady=(0, 12))

        self.entry = tk.Entry(bottom_frame, font=("TkDefaultFont", 11))
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry.bind("<Return>", self._on_send)

        send_button = tk.Button(bottom_frame, text="Send", command=self._on_send)
        send_button.pack(side=tk.LEFT, padx=(8, 0))

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

        self.entry.delete(0, tk.END)
        self._append_message("You", user_input)

        if user_input.lower() in {"exit", "quit"}:
            self._append_message("Agent", "Closing chat window.")
            self.root.after(300, self.root.destroy)
            return

        response = self.agent.process(user_input)
        self._append_message("Agent", response)

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
