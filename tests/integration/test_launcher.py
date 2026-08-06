"""Phase 11 item [105]: one command starts or reuses the API, then logs in.

The launcher owns three promises, so each is tested against a real socket:

1. an API that is already answering `/health` is reused, never duplicated;
2. a missing API is started, waited for and stopped again on exit;
3. `main()` logs in with the configured admin account and hands the token to
   the workbench, and still opens it (with an empty token) when login fails.

A headless host is covered too: it must report no display instead of raising.
"""

import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import uvicorn

import launcher
from app.main import create_app
from app.settings import Settings
from launcher import ApiServer, base_url, check_health, has_display, login, wait_healthy
from ui.api_client import WorkbenchClient


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_closed(url: str, timeout: float = 10.0) -> bool:
    """True once the port stops answering (used to prove a clean shutdown)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not check_health(url, timeout=0.2):
            return True
        time.sleep(0.02)
    return False


class LiveApi:
    """A uvicorn server on a background thread with dev-default credentials."""

    def __init__(self, settings: Settings, port: int) -> None:
        self.base_url = base_url("127.0.0.1", port)
        config = uvicorn.Config(
            create_app(settings), host="127.0.0.1", port=port, log_level="warning", access_log=False
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self, timeout: float = 10.0) -> None:
        self.thread.start()
        if not wait_healthy(self.base_url, timeout=timeout):
            raise RuntimeError("test API did not start")

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


class LauncherBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        # Development defaults: admin/admin, which is what `login()` reads from
        # the environment when no credentials are passed.
        self.settings = Settings(
            admin_user="admin",
            admin_password="admin",
            sqlite_path=str(self.dir / "launcher.db"),
        )

    def settings_for(self, name: str) -> Settings:
        return Settings(
            admin_user="admin",
            admin_password="admin",
            sqlite_path=str(self.dir / name),
        )


class HealthProbeTests(LauncherBase):
    def test_a_closed_port_is_not_healthy(self):
        self.assertFalse(check_health(base_url("127.0.0.1", free_port()), timeout=0.2))

    def test_a_live_api_is_healthy(self):
        port = free_port()
        api = LiveApi(self.settings, port)
        api.start()
        self.addCleanup(api.stop)
        self.assertTrue(check_health(api.base_url))
        self.assertTrue(wait_healthy(api.base_url, timeout=1.0))


class ApiServerTests(LauncherBase):
    def test_ensure_starts_the_api_reuses_it_and_stops_it(self):
        port = free_port()
        url = base_url("127.0.0.1", port)
        server = ApiServer(port=port, settings=self.settings_for("owned.db"))
        try:
            self.assertTrue(server.ensure(timeout=15), "no API was listening")
            self.assertTrue(server.owned)
            self.assertTrue(check_health(url))
            self.assertFalse(server.ensure(), "a second ensure must reuse the server")
        finally:
            server.stop()
        self.assertTrue(wait_closed(url), "the owned server was not shut down")
        self.assertFalse(server.owned)

    def test_ensure_does_not_duplicate_an_api_it_did_not_start(self):
        port = free_port()
        api = LiveApi(self.settings, port)
        api.start()
        self.addCleanup(api.stop)

        server = ApiServer(port=port, settings=self.settings_for("unused.db"))
        self.assertFalse(server.ensure())
        self.assertFalse(server.owned)
        server.stop()  # must be a no-op, not a shutdown of the operator's API
        self.assertTrue(check_health(api.base_url))


class LoginTests(LauncherBase):
    def setUp(self):
        super().setUp()
        self.port = free_port()
        self.api = LiveApi(self.settings, self.port)
        self.api.start()
        self.addCleanup(self.api.stop)
        self.client = WorkbenchClient(self.api.base_url, timeout=10)

    def test_configured_admin_credentials_produce_a_token(self):
        token = login(self.client)
        self.assertTrue(token)
        self.assertEqual(self.client.token, token)

    def test_refused_credentials_return_empty_without_raising(self):
        import io
        from contextlib import redirect_stderr

        captured = io.StringIO()
        with redirect_stderr(captured):
            token = login(self.client, user="admin", password="wrong-password")
        self.assertEqual(token, "")
        self.assertIn("auto-login failed", captured.getvalue())


class DisplayTests(LauncherBase):
    def test_linux_without_display_is_headless(self):
        with (
            mock.patch.object(launcher, "sys") as fake_sys,
            mock.patch.dict(os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": ""}, clear=False),
        ):
            fake_sys.platform = "linux"
            self.assertFalse(has_display())

    def test_linux_with_x_display_has_a_window(self):
        with (
            mock.patch.object(launcher, "sys") as fake_sys,
            mock.patch.dict(os.environ, {"DISPLAY": ":0"}, clear=False),
        ):
            fake_sys.platform = "linux"
            self.assertTrue(has_display())

    def test_desktop_platforms_always_have_a_display(self):
        with mock.patch.object(launcher, "sys") as fake_sys:
            fake_sys.platform = "win32"
            self.assertTrue(has_display())
            fake_sys.platform = "darwin"
            self.assertTrue(has_display())


class MainEntrypointTests(LauncherBase):
    def test_main_starts_the_api_auto_logs_in_and_opens_the_workbench(self):
        port = free_port()
        url = base_url("127.0.0.1", port)
        with (
            mock.patch("app.settings.get_settings", return_value=self.settings_for("main.db")),
            mock.patch.object(launcher, "has_display", return_value=True),
            mock.patch("ui.workbench.run_workbench") as run,
        ):
            code = launcher.main(["--port", str(port)])

        self.assertEqual(code, 0)
        run.assert_called_once()
        called_url, token = run.call_args.args
        self.assertEqual(called_url, url)
        self.assertTrue(token, "the workbench must receive an auto-login token")
        self.assertTrue(wait_closed(url), "main must stop the API it started")

    def test_main_reuses_an_api_that_is_already_running(self):
        port = free_port()
        api = LiveApi(self.settings, port)
        api.start()
        self.addCleanup(api.stop)

        with (
            mock.patch("app.settings.get_settings", return_value=self.settings_for("reuse.db")),
            mock.patch.object(launcher, "has_display", return_value=True),
            mock.patch("ui.workbench.run_workbench") as run,
        ):
            code = launcher.main(["--port", str(port)])

        self.assertEqual(code, 0)
        self.assertTrue(run.call_args.args[1])
        self.assertTrue(check_health(api.base_url), "the reused API must stay up")

    def test_an_unusable_display_reports_instead_of_raising(self):
        import tkinter as tk

        port = free_port()
        with (
            mock.patch("app.settings.get_settings", return_value=self.settings_for("broken.db")),
            mock.patch.object(launcher, "has_display", return_value=True),
            mock.patch.object(launcher, "_prompt_login", return_value=""),
            mock.patch("ui.workbench.run_workbench", side_effect=tk.TclError("no display name")),
        ):
            code = launcher.main(["--no-server", "--port", str(port)])
        self.assertEqual(code, 1)

    def test_login_failure_still_opens_the_workbench_with_an_empty_token(self):
        port = free_port()
        with (
            mock.patch("app.settings.get_settings", return_value=self.settings_for("down.db")),
            mock.patch.object(launcher, "has_display", return_value=True),
            mock.patch.object(launcher, "_prompt_login", return_value=""),
            mock.patch("ui.workbench.run_workbench") as run,
        ):
            code = launcher.main(["--no-server", "--port", str(port)])

        self.assertEqual(code, 0)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[1], "")


if __name__ == "__main__":
    unittest.main()
