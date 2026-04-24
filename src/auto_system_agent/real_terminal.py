"""Real PTY-backed terminal widget for Tkinter.

Embeds a real bash instance via pty so sudo/password prompts work
natively and the AI and user share the exact same terminal.
"""
import os
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import fcntl
import threading
import time
import queue
from pathlib import Path
import tkinter as tk
from tkinter import scrolledtext


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\].*?\x07|\x1b[()][A-Z0-9]|\r")


def _strip_ansi(s: str) -> str:
    # Keep \n, strip common escape sequences for display; keep output readable
    return _ANSI_RE.sub("", s)


class RealTerminalFrame(tk.Frame):
    """A real terminal (pty + bash -i) embedded in Tkinter.

    - `bash -i` runs with a real pty, so `sudo` shows `[sudo] password for user:` and
      user can type password directly in this widget (and AI commands also run here).
    - AI can call `run_command()` which writes to the same pty and waits for marker.
    """

    def __init__(self, master, cwd: Path | None = None, **kwargs):
        super().__init__(master, **kwargs)
        bg = "#0f172a"
        fg = "#e2e8f0"
        self.text = scrolledtext.ScrolledText(
            self,
            wrap=tk.NONE,
            font=("Monospace", 10),
            bg="black",
            fg=fg,
            insertbackground=fg,
            selectbackground="#334155",
            selectforeground=fg,
            borderwidth=0,
            relief=tk.FLAT,
            padx=6,
            pady=6,
            state=tk.NORMAL,
            undo=False,
        )
        self.text.pack(fill=tk.BOTH, expand=True)
        self.text.focus_set()

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

        try:
            self.master_fd, slave_fd = pty.openpty()
            # Set slave termios to raw-ish but keep echo for interactive
            # Launch bash -i with setsid so job control works
            env = os.environ.copy()
            env["TERM"] = env.get("TERM", "xterm-256color")
            # Make prompt simple and detectable if needed
            # Keep user PS1 but also ensure we can detect command done via marker
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
        self.after(100, self._set_winsize)
        self.bind("<Configure>", lambda e: self._set_winsize())

        # Reader thread: copy pty master -> Text widget
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        # Key handling: send to pty, not Text's default
        self.text.bind("<KeyPress>", self._on_key_press)
        # Handle paste
        self.text.bind("<<Paste>>", self._on_paste)
        # Prevent mouse editing from breaking? Keep normal selection
        self.text.bind("<Button-1>", lambda e: self.text.focus_set())

        # Welcome banner (will be interleaved with bash startup)
        self.after(300, lambda: self._write_to_text("\n# Real terminal ready — same pty for AI and user. Type here for sudo passwords.\n", "system"))

    # ---- Window size ----
    def _set_winsize(self):
        if self.master_fd is None:
            return
        try:
            # Estimate cols/rows from Text widget size and font
            # Use font metrics: approx char width 8, height 17 for Monospace 10
            w = self.text.winfo_width()
            h = self.text.winfo_height()
            if w < 10 or h < 10:
                return
            cols = max(20, w // 8)
            rows = max(5, h // 17)
            s = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, s)
            # Also send SIGWINCH to process group
            if self.proc and self.proc.pid:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGWINCH)
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
                data = os.read(self.master_fd, 4096)
                if not data:
                    break
                try:
                    text = data.decode("utf-8", errors="replace")
                except Exception:
                    text = data.decode(errors="replace")
                # Push to queue for exec capture as well
                try:
                    self._output_queue.put(text, block=False)
                except Exception:
                    pass
                # Schedule UI insert on main thread
                self.after(0, lambda t=text: self._write_to_text(t, None))
            except OSError:
                break
            except Exception:
                time.sleep(0.05)
        self._alive = False

    def _write_to_text(self, data: str, tag: str | None):
        # Strip ANSI for cleaner display but keep original for realism?
        # Show raw with ANSI stripped for readability; keep color via tags? Simplify: strip and insert as output
        try:
            # Handle \r carriage return (common in terminal)
            # Convert \r\n and \r to \n for Tk
            # Keep incremental
            clean = _strip_ansi(data)
            # Tk Text handles \r poorly; replace \r with \n if followed by not \n
            # Simplify: replace \r with ""
            # Preserve prompt line
            self.text.configure(state=tk.NORMAL)
            # Avoid inserting huge binary
            if len(clean) > 8000:
                clean = clean[-8000:]
            self.text.insert(tk.END, clean)
            self.text.configure(state=tk.DISABLED)
            self.text.see(tk.END)
        except Exception:
            pass

    # ---- Input handling ----
    def _on_key_press(self, event):
        if self.master_fd is None:
            return "break"
        # Allow copy: Ctrl-C when no selection? Actually Ctrl-C should send SIGINT
        # Check modifiers
        # Handle special keys
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
            # Ctrl combinations
            if event.state & 0x4:  # Control
                # Ctrl-C, Ctrl-D, Ctrl-Z, Ctrl-L, etc.
                k = event.keysym.lower()
                if k == "c":
                    # Send SIGINT to foreground process group via \x03
                    os.write(self.master_fd, b"\x03")
                    return "break"
                if k == "d":
                    os.write(self.master_fd, b"\x04")
                    return "break"
                if k == "z":
                    os.write(self.master_fd, b"\x1a")
                    return "break"
                if k == "l":
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
                # For other Ctrl+key, send control char
                if len(event.char) == 1 and event.char.isalpha():
                    # Already handled above? Fallthrough
                    pass
                # Let Ctrl-V (paste) be handled by <<Paste>>
                if k == "v":
                    return None  # allow paste
                # For other Ctrl combos, send as control code
                if event.char and 0 < ord(event.char) < 32:
                    os.write(self.master_fd, event.char.encode())
                    return "break"
            # Regular printable
            if event.char and len(event.char) == 1:
                # For normal typing, send char
                # Note: need to encode as utf-8
                os.write(self.master_fd, event.char.encode("utf-8", errors="replace"))
                return "break"
            # For unhandled, break to prevent Text insertion
            # But allow F-keys? ignore
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

    # ---- Public API for AI ----
    def run_command(self, cmd: str, timeout: int = 30) -> tuple[str, int]:
        """Run a command in this SAME pty and capture output.

        Writes `cmd` + marker, reads until marker appears, returns (output, exit_code).
        Output is also already displayed in Text via reader thread, but we also capture for ExecutionResult.
        """
        if self.master_fd is None or not self._alive:
            return ("Terminal not available", 1)
        # Ensure only one AI command at a time
        with self._exec_lock:
            # Clear output queue
            while not self._output_queue.empty():
                try:
                    self._output_queue.get_nowait()
                except queue.Empty:
                    break
            marker = f"__CMD_DONE_{int(time.time()*1000) % 100000}__"
            # Send command with echo marker
            full = f"{cmd}\n"
            # For reliable capture, append marker echo after command: `; echo MARKER:$?` is already
            # But we handle by sending cmd, then waiting a bit and sending marker? Instead we send cmd with marker wrapper:
            # Actually we send the raw cmd, then after it finishes, bash will show prompt again. Hard to detect.
            # Simpler: send cmd as is, then send `echo {marker}:$?` as second command and wait for that echo.
            try:
                os.write(self.master_fd, full.encode("utf-8", errors="replace"))
            except OSError as e:
                return (f"Write failed: {e}", 1)

            # Wait a bit for command to execute, then send marker echo
            # But we can just send marker echo immediately after; bash -i will execute sequentially
            # Send marker echo
            marker_cmd = f"echo {marker}:$?\n"
            # Small delay to let previous cmd be enqueued
            time.sleep(0.05)
            try:
                os.write(self.master_fd, marker_cmd.encode())
            except OSError as e:
                return (f"Write marker failed: {e}", 1)

            # Now read until marker appears
            buf = ""
            start = time.time()
            exit_code = 0
            # We need to capture output that occurred between cmd and marker
            # The pty output includes echo of cmd, output, and marker line
            while time.time() - start < timeout:
                try:
                    # Poll queue with timeout
                    chunk = self._output_queue.get(timeout=0.2)
                    buf += chunk
                    # Check for marker
                    # Marker appears as `marker:0` or `marker:1` etc.
                    m = re.search(rf"{re.escape(marker)}:(\d+)", buf)
                    if m:
                        exit_code = int(m.group(1))
                        # Output is buffer up to marker (exclude marker line and after)
                        # Find start after cmd echo? For now return buffer without marker
                        # Strip marker line
                        out = buf[: m.start()]
                        # Remove cmd echo and marker echo from output for cleanliness
                        # Heuristic: remove first line containing cmd and last lines
                        # Keep as is but strip ANSI and marker
                        out_clean = _strip_ansi(out)
                        # Remove the echoed cmd line if present at start
                        # The pty echoes `cmd\r\n` then output
                        # Try to remove first occurrence of cmd
                        # Keep full clean for debugging
                        # Trim to last 8000 chars
                        if len(out_clean) > 8000:
                            out_clean = out_clean[-8000:]
                        return (out_clean.strip(), exit_code)
                except queue.Empty:
                    # No data, continue
                    # Check if process alive
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
