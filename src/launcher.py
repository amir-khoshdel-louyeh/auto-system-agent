"""Phase 11 item [105]: `python main.py` starts everything it needs.

One command has to be enough on a clean machine: the API, a token, and the
workbench. This module is the missing glue.

- :func:`check_health` probes `GET /health`, so an API the operator already
  started is *reused* and never duplicated;
- :class:`ApiServer` starts uvicorn in a daemon thread only when nothing is
  listening, waits until it is healthy, and stops it again if this process
  started it;
- :func:`login` exchanges `APP_ADMIN_USER` / `APP_ADMIN_PASSWORD` for a
  bearer token so the client never asks for a pasted token;
- :func:`has_display` decides whether a Tk window is possible — a headless
  host gets the server and its URL instead of a traceback.

The client stays stdlib-only: this module uses urllib for the health probe and
uvicorn/FastAPI only when it has to host the API.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from typing import Any
from urllib import error, request

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
HEALTH_PATH = "/health"
START_TIMEOUT = 15.0
HEADLESS_PLATFORMS = ("linux", "freebsd")


class ServerError(RuntimeError):
    """The local API could not be started (missing dependency, dead port)."""


def base_url(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> str:
    """The API origin the client and the health probe use."""
    return f"http://{host}:{port}"


def check_health(url: str, *, timeout: float = 1.0) -> bool:
    """True when `GET <url>/health` answers 200 (an API is already running)."""
    # The origin comes from base_url() (always http://host:port), not from
    # caller text, so the scheme cannot be swapped for file: or ftp:.
    target = f"{url.rstrip('/')}{HEALTH_PATH}"
    try:
        with request.urlopen(target, timeout=timeout) as response:  # nosec B310
            status = int(getattr(response, "status", 0) or 0)
            body = response.read()
    except (error.URLError, OSError, ValueError):
        return False
    if status != 200:
        return False
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("status") == "healthy"


def wait_healthy(url: str, *, timeout: float = START_TIMEOUT, interval: float = 0.05) -> bool:
    """Poll the health probe until it passes or the deadline expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check_health(url):
            return True
        time.sleep(interval)
    return check_health(url)


def has_display() -> bool:
    """Whether a Tk window can be opened on this host."""
    if sys.platform.startswith("win") or sys.platform == "darwin":
        return True
    if not sys.platform.startswith(HEADLESS_PLATFORMS):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


class ApiServer:
    """A uvicorn server this process owns, or `None` when one is reused."""

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        settings=None,
    ) -> None:
        self.host = host
        self.port = port
        self.base_url = base_url(host, port)
        self.settings = settings
        # uvicorn.Server, imported only when this process hosts the API.
        self.server: Any = None
        self.thread: threading.Thread | None = None

    @property
    def owned(self) -> bool:
        """True only when this instance started the server."""
        return self.server is not None

    def ensure(self, *, timeout: float = START_TIMEOUT) -> bool:
        """Start the API if needed; returns True when this call started it."""
        if check_health(self.base_url):
            return False
        self.start(timeout=timeout)
        return True

    def start(self, *, timeout: float = START_TIMEOUT) -> None:
        """Run uvicorn in a daemon thread and wait until `/health` answers."""
        try:
            import uvicorn

            from app.main import create_app
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ServerError(
                "the API needs FastAPI and uvicorn: pip install -r requirements-api.txt"
            ) from exc
        if self.settings is None:
            from app.settings import get_settings

            self.settings = get_settings()
        config = uvicorn.Config(
            create_app(self.settings),
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, name="api-server", daemon=True)
        self.thread.start()
        if not wait_healthy(self.base_url, timeout=timeout):
            self.stop()
            raise ServerError(f"the API did not become healthy on {self.base_url}")

    def stop(self) -> None:
        """Stop the server if this process started it (safe to call twice)."""
        if self.server is None:
            return
        self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=10)
        self.server = None
        self.thread = None


def login(client, *, user: str = "", password: str = "") -> str:
    """Log in with the configured admin account; "" when it fails."""
    from app.settings import get_settings
    from ui.api_client import ApiError

    settings = get_settings()
    try:
        return client.login(user or settings.admin_user, password or settings.admin_password)
    except ApiError as exc:
        print(f"auto-login failed: {exc}", file=sys.stderr)
        return ""


def _prompt_login(client) -> str:
    """Visible login prompt when the env-admin credentials are refused."""
    try:
        import tkinter as tk
        from tkinter import simpledialog

        from app.settings import get_settings

        settings = get_settings()
        root = tk.Tk()
        root.withdraw()
        try:
            user = simpledialog.askstring(
                "Sign in to Auto System Agent",
                "Username:",
                initialvalue=settings.admin_user,
                parent=root,
            )
            if not user:
                return ""
            password = simpledialog.askstring(
                "Sign in to Auto System Agent", "Password:", show="*", parent=root
            )
            if password is None:
                return ""
            return client.login(user, password)
        finally:
            root.destroy()
    except Exception as exc:  # noqa: BLE001 - no display / cancelled dialog
        print(f"login failed: {exc}", file=sys.stderr)
        return ""


def _serve_forever(server: ApiServer | None) -> int:
    """Keep the API process alive until Ctrl-C."""
    print(f"API listening on {server.base_url if server else base_url()}")
    try:
        if server is not None and server.thread is not None:
            server.thread.join()
        else:
            while True:
                time.sleep(3600)
    except KeyboardInterrupt:
        print("")
        return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entrypoint behind `python main.py`."""
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Auto System Agent — start the API automatically and open the workbench",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="API port (default 8000)")
    parser.add_argument("--host", default=DEFAULT_HOST, help="API host (default 127.0.0.1)")
    parser.add_argument(
        "--server-only", action="store_true", help="start the API and print its URL, no GUI"
    )
    parser.add_argument(
        "--no-server", action="store_true", help="do not start an API; use one already running"
    )
    parser.add_argument(
        "--gui",
        "--chat",
        dest="client",
        action="store_const",
        const="chat",
        help="open the legacy chat client instead of the workbench",
    )
    args = parser.parse_args(argv)

    from app.settings import get_settings

    settings = get_settings()
    url = base_url(args.host, args.port)
    server: ApiServer | None = None
    started = False
    if not args.no_server:
        server = ApiServer(host=args.host, port=args.port, settings=settings)
        try:
            started = server.ensure()
            if not started:
                print(f"API already running on {url}")
        except ServerError as exc:
            print(f"warning: {exc}", file=sys.stderr)
            print("continuing without a local API (start one, or use --no-server)", file=sys.stderr)
            server = None

    try:
        if args.server_only:
            return _serve_forever(server)
        if args.client == "chat":
            from ui.gui import run_gui

            run_gui()
            return 0
        if not has_display():
            print("no display detected (set DISPLAY to open the workbench)", file=sys.stderr)
            return _serve_forever(server)

        from ui.api_client import WorkbenchClient
        from ui.workbench import run_workbench

        client = WorkbenchClient(url, timeout=30.0)
        token = login(client)
        if not token:
            token = _prompt_login(client)
        run_workbench(url, token)
        return 0
    finally:
        if started and server is not None:
            server.stop()


if __name__ == "__main__":  # pragma: no cover - interactive entrypoint
    raise SystemExit(main())
