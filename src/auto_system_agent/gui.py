import tkinter as tk
from tkinter import scrolledtext

from auto_system_agent.agent import AutoSystemAgent


class AgentChatGUI:
    """Minimal desktop chat interface for the Auto System Agent."""

    def __init__(self) -> None:
        self.agent = AutoSystemAgent()
        self.root = tk.Tk()
        self.root.title("Auto System Agent")
        self.root.geometry("760x520")

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

    def run(self) -> None:
        self.root.mainloop()


def run_gui() -> None:
    app = AgentChatGUI()
    app.run()
