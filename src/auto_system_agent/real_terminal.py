"""Real PTY-backed terminal widget for Tkinter – Alacritty look-and-feel.

Embeds a real bash -i via pty so sudo/password prompts work natively and
the AI and user share the exact same terminal. Visual style mirrors Alacritty:
JetBrains Mono 10, #0f172a bg, #f8fafc fg, xterm-256color with ANSI parsing.
"""
import os
import pty
import re
import select
import signal
import struct
import subprocess
import termios
import fcntl
import threading
import time
import queue
from pathlib import Path
import tkinter as tk
from tkinter import scrolledtext

# Fallback stripping for OSC/CSI sequences – Fedora shell integration emits OSC 3008 with ESC\ terminator
_ANSI_OSC_RE = re.compile(r"\x1b\].*?(?:\x07|\x1b\\)")
_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_ANSI_EXTRA_RE = re.compile(r"\x1b\(B")
_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
# Sequences that mean clear screen
_CLEAR_RE = re.compile(r"\x1b\[2J|\x1b\[3J|\x1b\[H\x1b\[2J|\x1b\[2K")


# Alacritty-inspired palette approximation for 8 + bright colors
_ALACRITTY_COLORS = {
    30: "#64748b",  # black -> slate
    31: "#ef4444",  # red
    32: "#22c55e",  # green
    33: "#eab308",  # yellow
    34: "#3b82f6",  # blue
    35: "#a855f7",  # magenta
    36: "#06b6d4",  # cyan
    37: "#f8fafc",  # white
    90: "#94a3b8",  # bright black
    91: "#f87171",
    92: "#4ade80",
    93: "#facc15",
    94: "#60a5fa",
    95: "#c084fc",
    96: "#22d3ee",
    97: "#ffffff",
}


class RealTerminalFrame(tk.Frame):
    """A real terminal (pty + bash -i) embedded in Tkinter.

    - `bash -i` runs with a real pty, so `sudo` shows `[sudo] password for user:` and
      user can type password directly in this widget (and AI commands also run here).
    - AI can call `run_command()` which writes to the same pty and waits for marker.
    - Visual: JetBrains Mono 10, bg #0f172a, fg #f8fafc, TERM=xterm-256color.
    """

    def __init__(self, master, cwd: Path | None = None, **kwargs):
        super().__init__(master, **kwargs)
        bg = "#0f172a"
        fg = "#f8fafc"
        # Try JetBrains Mono 10, fallback to Monospace
        # wrap=CHAR limits text to widget width (no horizontal overflow beyond page)
        try:
            self.text = scrolledtext.ScrolledText(
                self,
                wrap=tk.CHAR,
                font=("JetBrains Mono", 10),
                bg=bg,
                fg=fg,
                insertbackground=fg,
                selectbackground="#334155",
                selectforeground=fg,
                borderwidth=0,
                relief=tk.FLAT,
                padx=8,
                pady=8,
                state=tk.NORMAL,
                undo=False,
                highlightthickness=0,
            )
            # Probe if font exists; Tk will fallback silently, but we keep it
        except Exception:
            self.text = scrolledtext.ScrolledText(
                self,
                wrap=tk.CHAR,
                font=("Monospace", 10),
                bg=bg,
                fg=fg,
                insertbackground=fg,
                selectbackground="#334155",
                selectforeground=fg,
                borderwidth=0,
                relief=tk.FLAT,
                padx=8,
                pady=8,
                state=tk.NORMAL,
                undo=False,
                highlightthickness=0,
            )
        self.text.pack(fill=tk.BOTH, expand=True)
        self.text.focus_set()
        # Configure ANSI tags
        self._configure_ansi_tags()
        # Also handle horizontal scrolling via shift+wheel if needed
        try:
            self.text.configure(insertwidth=2)
        except Exception:
            pass

        # pty + bash
        self.master_fd: int | None = None
        self.proc: subprocess.Popen | None = None
        self._alive = True
        self._write_lock = threading.Lock()
        self._exec_lock = threading.Lock()
        self._output_queue: queue.Queue[str] = queue.Queue()
        self._cwd = (cwd or Path.home()).resolve() if (cwd or Path.home()).exists() else Path.cwd().resolve()
        if not self._cwd.exists():
            self._cwd = Path.cwd().resolve()
        # Page limits: rows/cols updated by _set_winsize, used to trim buffer
        self._rows: int = 24
        self._cols: int = 80
        # Keep scrollback history for scrolling (was 1 page only, now 200 pages)
        self._page_limit_factor: int = 200

        try:
            self.master_fd, slave_fd = pty.openpty()
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["COLORTERM"] = "truecolor"
            env["TERM_PROGRAM"] = "Alacritty"
            # Simple detectable prompt but keep user customization minimal
            env["PS1"] = r"\u@\h:\w\$ "
            self.proc = subprocess.Popen(
                ["bash", "-i"],
                preexec_fn=os.setsid,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=str(self._cwd),
                env=env,
                close_fds=True,
            )
            os.close(slave_fd)
        except Exception as e:
            self.text.insert(tk.END, f"Failed to start pty bash: {e}\n", "error")
            self.master_fd = None
            self.proc = None
            return

        # Configure initial window size
        self.after(150, self._set_winsize)
        self.bind("<Configure>", lambda e: self._set_winsize())

        # Reader thread: copy pty master -> Text widget
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        # Key handling: send to pty, not Text's default
        self.text.bind("<KeyPress>", self._on_key_press)
        self.text.bind("<<Paste>>", self._on_paste)
        self.text.bind("<Button-1>", lambda e: self.text.focus_set())
        # Explicit Alacritty-style shortcuts (redundant with _on_key_press but ensures Shift+Ctrl caught)
        self.text.bind("<Control-Shift-C>", self._copy_selection)
        self.text.bind("<Control-Shift-V>", self._on_paste)
        self.text.bind("<Control-P>", lambda e: self._send_ctrl(b"\x10"))
        self.text.bind("<Control-p>", lambda e: self._send_ctrl(b"\x10"))
        self.text.bind("<Control-N>", lambda e: self._send_ctrl(b"\x0e"))
        self.text.bind("<Control-n>", lambda e: self._send_ctrl(b"\x0e"))
        self.text.bind("<Control-A>", lambda e: self._send_ctrl(b"\x01"))
        self.text.bind("<Control-a>", lambda e: self._send_ctrl(b"\x01"))
        self.text.bind("<Control-E>", lambda e: self._send_ctrl(b"\x05"))
        self.text.bind("<Control-e>", lambda e: self._send_ctrl(b"\x05"))
        self.text.bind("<Control-L>", lambda e: self._send_ctrl(b"\x0c"))
        self.text.bind("<Control-l>", lambda e: self._send_ctrl(b"\x0c"))
        self.text.bind("<Control-C>", lambda e: self._send_ctrl(b"\x03"))
        self.text.bind("<Control-c>", lambda e: self._send_ctrl(b"\x03"))
        # Handle Ctrl+L etc via keypress already

        self.after(400, lambda: self._write_to_text("\n# Alacritty embedded terminal ready — same pty for AI and user. sudo prompts appear here.\n", None))

    def _configure_ansi_tags(self):
        # Base fg/bg already, add color tags
        try:
            for code, color in _ALACRITTY_COLORS.items():
                tag = f"ansi_fg_{code}"
                self.text.tag_configure(tag, foreground=color)
                # bright background not needed now but configure bg variants
            for code in range(40, 48):
                fg_code = code - 10  # 40->30 etc, reuse colors as bg
                color = _ALACRITTY_COLORS.get(fg_code, "#0f172a")
                tag = f"ansi_bg_{code}"
                self.text.tag_configure(tag, background=color)
            self.text.tag_configure("ansi_bold", font=("JetBrains Mono", 10, "bold"))
            # Fallback Monospace bold if JetBrains missing
            try:
                self.text.tag_configure("ansi_bold_fallback", font=("Monospace", 10, "bold"))
            except Exception:
                pass
            self.text.tag_configure("ansi_dim", foreground="#94a3b8")
        except Exception:
            pass

    def _enforce_page_limits(self):
        """Trim text widget to keep scrollback history."""
        try:
            # Keep large scrollback (e.g. 200 pages) instead of just visible page
            max_lines = max(5, self._rows * self._page_limit_factor)
            # Cap absolute to avoid unbounded memory, but allow scrolling
            max_lines = min(max_lines, 5000)
            end = self.text.index(tk.END)
            line_count = int(end.split(".")[0]) - 1
            if line_count > max_lines:
                delete_until = f"{line_count - max_lines + 1}.0"
                self.text.delete("1.0", delete_until)
                # Re-enforce after delete, trim char limit per line is handled by wrap=CHAR
        except Exception:
            pass

    # ---- Window size ----
    def _set_winsize(self):
        if self.master_fd is None:
            return
        try:
            w = self.text.winfo_width()
            h = self.text.winfo_height()
            if w < 10 or h < 10:
                return
            # Estimate using actual font metrics if possible
            try:
                font = self.text.cget("font")
                # Rough: JetBrains Mono 10 ~ char width 8, height 17
                cols = max(20, w // 8)
                rows = max(5, h // 17)
            except Exception:
                cols = max(20, w // 8)
                rows = max(5, h // 17)
            self._cols = cols
            self._rows = rows
            s = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, s)
            if self.proc and self.proc.pid:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGWINCH)
                except Exception:
                    pass
            # Enforce limits immediately after resize so buffer fits new page
            try:
                self.text.configure(state=tk.NORMAL)
                self._enforce_page_limits()
                # Keep NORMAL so BackSpace/Delete and typing work via _on_key_press
                self.text.configure(state=tk.NORMAL)
            except Exception:
                pass
        except Exception:
            pass

    # ---- Reader ----
    def _reader_loop(self):
        if self.master_fd is None:
            return
        while self._alive and self.master_fd is not None:
            try:
                r, _, _ = select.select([self.master_fd], [], [], 0.1)
                if not r:
                    continue
                data = os.read(self.master_fd, 8192)
                if not data:
                    break
                try:
                    text = data.decode("utf-8", errors="replace")
                except Exception:
                    text = data.decode(errors="replace")
                try:
                    self._output_queue.put(text, block=False)
                except Exception:
                    pass
                self.after(0, lambda t=text: self._write_to_text(t, None))
            except OSError:
                break
            except Exception:
                time.sleep(0.05)
        self._alive = False

    def _write_to_text(self, data: str, tag: str | None):
        try:
            # Suppress AI marker echo from display (keep it queued for run_command)
            if "__CMD_DONE_" in data:
                data = re.sub(r"__CMD_DONE_\d+__:\d+\r?\n?", "", data)
                # Also handle without underscores (legacy)
                data = re.sub(r"CMD_DONE_\d+:\d+\r?\n?", "", data)
                if not data.strip():
                    return
            # Handle clear screen sequences first
            if _CLEAR_RE.search(data):
                # If clear detected, wipe widget
                try:
                    self.text.configure(state=tk.NORMAL)
                    self.text.delete("1.0", tk.END)
                    self.text.configure(state=tk.DISABLED)
                except Exception:
                    pass
                # Remove clear codes from data to avoid insertion artifacts
                data = _CLEAR_RE.sub("", data)
                # Also handle ESC[H ESC[2J combos stripped above, but also ESC[H alone shouldn't clear entire?
                # For Ctrl+L, bash sends \x1b[H\x1b[2J, already cleared.

            # Strip OSC (including Fedora's 3008 shell integration) and ESC ( B
            data_for_parse = _ANSI_OSC_RE.sub("", data)
            data_for_parse = _ANSI_EXTRA_RE.sub("", data_for_parse)
            # Replace carriage returns and handle cursor moves
            # Normalize \r\n -> \n
            data_for_parse = data_for_parse.replace("\r\n", "\n")
            # Handle \r as carriage return: move to line start then overwrite not trivial in Tk.
            # Simplify: replace remaining \r with "" (as before) unless it precedes text that should overwrite.
            # For progress bars, treat \r as newline for visibility
            if "\r" in data_for_parse:
                # If \r followed by text without \n, show as newline to avoid invisible output
                data_for_parse = data_for_parse.replace("\r", "\n")

            # Now parse SGR
            self.text.configure(state=tk.NORMAL)
            last = 0
            current_tags: list[str] = []
            # Use bold flag separately
            for m in _SGR_RE.finditer(data_for_parse):
                start, end = m.span()
                # Insert text before this SGR
                chunk = data_for_parse[last:start]
                if chunk:
                    # Strip remaining CSI including [?2004h/l bracketed paste and ?25h cursor etc.
                    chunk = _CSI_RE.sub("", chunk)
                    if chunk:
                        if len(chunk) > 12000:
                            chunk = chunk[-12000:]
                        tags = tuple(current_tags) if current_tags else ()
                        self.text.insert(tk.END, chunk, tags)
                # Update tags based on SGR params
                params = m.group(1)
                if params == "" or params == "0":
                    current_tags = []
                else:
                    for p in params.split(";"):
                        if not p:
                            continue
                        try:
                            code = int(p)
                        except ValueError:
                            continue
                        if code == 0:
                            current_tags = []
                        elif code == 1:
                            if "ansi_bold" not in current_tags:
                                current_tags.append("ansi_bold")
                        elif code == 2:
                            if "ansi_dim" not in current_tags:
                                current_tags.append("ansi_dim")
                        elif code == 22:  # normal intensity
                            current_tags = [t for t in current_tags if t not in ("ansi_bold", "ansi_dim")]
                        elif 30 <= code <= 37 or 90 <= code <= 97:
                            # Remove previous fg
                            current_tags = [t for t in current_tags if not t.startswith("ansi_fg_")]
                            current_tags.append(f"ansi_fg_{code}")
                        elif code == 39:
                            current_tags = [t for t in current_tags if not t.startswith("ansi_fg_")]
                        elif 40 <= code <= 47 or 100 <= code <= 107:
                            current_tags = [t for t in current_tags if not t.startswith("ansi_bg_")]
                            current_tags.append(f"ansi_bg_{code}")
                        elif code == 49:
                            current_tags = [t for t in current_tags if not t.startswith("ansi_bg_")]
                last = end
            # Tail chunk
            tail = data_for_parse[last:]
            if tail:
                tail = _CSI_RE.sub("", tail)
                if tail:
                    if len(tail) > 12000:
                        tail = tail[-12000:]
                    tags = tuple(current_tags) if current_tags else ()
                    self.text.insert(tk.END, tail, tags)
            # Enforce page-length limit (vertical + width via wrap=CHAR)
            self._enforce_page_limits()
            self.text.configure(state=tk.NORMAL)
            self.text.see(tk.END)
        except Exception:
            # Fallback: strip all ANSI (CSI with ?, OSC with BEL or ESC\)
            try:
                clean = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\].*?(?:\x07|\x1b\\)|\x1b\(B|\r", "", data)
                self.text.configure(state=tk.NORMAL)
                if len(clean) > 8000:
                    clean = clean[-8000:]
                self.text.insert(tk.END, clean)
                self._enforce_page_limits()
                self.text.configure(state=tk.NORMAL)
                self.text.see(tk.END)
            except Exception:
                pass

    # ---- Input handling ----
    def _on_key_press(self, event):
        if self.master_fd is None:
            return "break"
        try:
            if event.keysym == "Return" or event.keysym == "KP_Enter":
                os.write(self.master_fd, b"\n")
                return "break"
            elif event.keysym == "BackSpace":
                os.write(self.master_fd, b"\x7f")
                return "break"
            elif event.keysym == "Tab":
                os.write(self.master_fd, b"\t")
                return "break"
            elif event.keysym == "Up":
                os.write(self.master_fd, b"\x1b[A")
                return "break"
            elif event.keysym == "Down":
                os.write(self.master_fd, b"\x1b[B")
                return "break"
            elif event.keysym == "Left":
                os.write(self.master_fd, b"\x1b[D")
                return "break"
            elif event.keysym == "Right":
                os.write(self.master_fd, b"\x1b[C")
                return "break"
            elif event.keysym == "Home":
                os.write(self.master_fd, b"\x1b[H")
                return "break"
            elif event.keysym == "End":
                os.write(self.master_fd, b"\x1b[F")
                return "break"
            elif event.keysym == "Delete":
                os.write(self.master_fd, b"\x1b[3~")
                return "break"
            elif event.keysym == "Escape":
                os.write(self.master_fd, b"\x1b")
                return "break"
            elif event.keysym == "Prior":  # PageUp
                os.write(self.master_fd, b"\x1b[5~")
                return "break"
            elif event.keysym == "Next":  # PageDown
                os.write(self.master_fd, b"\x1b[6~")
                return "break"
            if event.state & 0x4:  # Control
                k = event.keysym.lower()
                is_shift = bool(event.state & 0x1)
                # Ctrl+Shift+C = Copy, Ctrl+Shift+V = Paste (Alacritty style)
                if is_shift:
                    if k == "c":
                        # Copy selection to clipboard
                        try:
                            sel = self.text.selection_get()
                            if sel:
                                self.clipboard_clear()
                                self.clipboard_append(sel)
                        except Exception:
                            pass
                        return "break"
                    if k == "v":
                        return self._on_paste()
                    # For other Shift+Ctrl combos, fall through to normal handling without shift distinction
                if k == "c" and not is_shift:
                    os.write(self.master_fd, b"\x03")
                    return "break"
                if k == "d":
                    os.write(self.master_fd, b"\x04")
                    return "break"
                if k == "z":
                    os.write(self.master_fd, b"\x1a")
                    return "break"
                if k == "l":
                    # Ctrl+L clear screen – send form feed, also handle locally
                    os.write(self.master_fd, b"\x0c")
                    return "break"
                if k == "u":
                    os.write(self.master_fd, b"\x15")
                    return "break"
                if k == "k":
                    os.write(self.master_fd, b"\x0b")
                    return "break"
                if k == "a":
                    os.write(self.master_fd, b"\x01")
                    return "break"
                if k == "e":
                    os.write(self.master_fd, b"\x05")
                    return "break"
                if k == "p":
                    # Ctrl+P = previous history (like Up) – send 0x10
                    os.write(self.master_fd, b"\x10")
                    return "break"
                if k == "n":
                    # Ctrl+N = next history (like Down) – send 0x0e
                    os.write(self.master_fd, b"\x0e")
                    return "break"
                if k == "v" and not is_shift:
                    return self._on_paste()
                if event.char and 0 < ord(event.char) < 32:
                    os.write(self.master_fd, event.char.encode())
                    return "break"
            if event.char and len(event.char) == 1:
                os.write(self.master_fd, event.char.encode("utf-8", errors="replace"))
                return "break"
            return "break"
        except OSError:
            return "break"
        except Exception:
            return "break"

    def _on_paste(self, event=None):
        try:
            data = self.clipboard_get()
            if data:
                os.write(self.master_fd, data.encode("utf-8", errors="replace"))
        except Exception:
            pass
        return "break"

    def _copy_selection(self, event=None):
        """Ctrl+Shift+C – copy selected text to clipboard."""
        try:
            sel = self.text.selection_get()
            if sel:
                self.clipboard_clear()
                self.clipboard_append(sel)
        except Exception:
            pass
        return "break"

    def _send_ctrl(self, data: bytes, event=None):
        """Helper to send raw control byte to pty."""
        try:
            if self.master_fd is not None:
                os.write(self.master_fd, data)
        except Exception:
            pass
        return "break"

    # ---- Public API for AI ----
    def run_command(self, cmd: str, timeout: int = 300) -> tuple[str, int]:
        """Run a command in this SAME pty and capture output.

        Writes `cmd` + marker, reads until marker appears, returns (output, exit_code).
        Output is also already displayed in Text via reader thread, but we also capture for ExecutionResult.
        """
        if self.master_fd is None or not self._alive:
            return ("Terminal not available", 1)
        with self._exec_lock:
            while not self._output_queue.empty():
                try:
                    self._output_queue.get_nowait()
                except queue.Empty:
                    break
            marker = f"__CMD_DONE_{int(time.time()*1000) % 100000}__"
            full = f"{cmd}\n"
            try:
                os.write(self.master_fd, full.encode("utf-8", errors="replace"))
            except OSError as e:
                return (f"Write failed: {e}", 1)
            time.sleep(0.05)
            marker_cmd = f"echo {marker}:$?\n"
            try:
                os.write(self.master_fd, marker_cmd.encode())
            except OSError as e:
                return (f"Write marker failed: {e}", 1)
            buf = ""
            start = time.time()
            exit_code = 0
            while time.time() - start < timeout:
                try:
                    chunk = self._output_queue.get(timeout=0.2)
                    buf += chunk
                    m = re.search(rf"{re.escape(marker)}:(\d+)", buf)
                    if m:
                        exit_code = int(m.group(1))
                        out = buf[: m.start()]
                        # Strip ANSI for AI result but keep readable
                        out_clean = _SGR_RE.sub("", out)
                        out_clean = _ANSI_OSC_RE.sub("", out_clean)
                        out_clean = _ANSI_EXTRA_RE.sub("", out_clean)
                        out_clean = _CSI_RE.sub("", out_clean)
                        out_clean = out_clean.replace("\r\n", "\n").replace("\r", "\n")
                        if len(out_clean) > 8000:
                            out_clean = out_clean[-8000:]
                        return (out_clean.strip(), exit_code)
                except queue.Empty:
                    if self.proc and self.proc.poll() is not None:
                        return (buf, 1)
                    continue
            return (buf.strip() + f"\n[timeout after {timeout}s waiting for marker]", 124)

    def destroy(self):
        self._alive = False
        try:
            if self.master_fd is not None:
                os.close(self.master_fd)
        except Exception:
            pass
        self.master_fd = None
        try:
            if self.proc:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                except Exception:
                    try:
                        self.proc.terminate()
                    except Exception:
                        pass
        except Exception:
            pass
        super().destroy()
