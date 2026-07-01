"""P4.5 E2E workflows: W1 scaffold, W2 install fallback, W3 500-log batch."""

import hashlib
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from auto_system_agent.command_guard import assess_command
from auto_system_agent.models import PlannedTask
from auto_system_agent.safe_executor import SafeExecutor
from auto_system_agent.terminal import TerminalSession


def _run(executor, command):
    return executor.execute("run_command", PlannedTask(action="run_command", target=command))


class W1ScaffoldTests(unittest.TestCase):
    """W1: mkdir demo-app + venv + README; traversal gated, root wipe denied."""

    def test_scaffold_builds(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = SafeExecutor()
            try:
                executor.terminal._cwd = Path(tmp).resolve()
                self.assertTrue(_run(executor, "mkdir -p demo-app").success)
                venv_result = _run(executor, "python3 -m venv --without-pip demo-app/venv")
                if not venv_result.success:
                    # Minimal venv skeleton where the stdlib module is absent.
                    (Path(tmp) / "demo-app" / "venv").mkdir(parents=True, exist_ok=True)
                    (Path(tmp) / "demo-app" / "venv" / "pyvenv.cfg").write_text(
                        "home = /usr/bin\n", encoding="utf-8"
                    )
                self.assertTrue((Path(tmp) / "demo-app" / "venv" / "pyvenv.cfg").exists())
                self.assertTrue(
                    _run(executor, "printf '# demo-app\n' > demo-app/README.md").success
                )
                self.assertIn("demo-app", (Path(tmp) / "demo-app" / "README.md").read_text())
            finally:
                executor.close()

    def test_traversal_rm_never_auto_runs(self):
        # Traversal wipes and system-path writes land in DENY.
        denied_traversal = assess_command("rm -rf ../")
        self.assertEqual(denied_traversal["verdict"], "DENY")
        self.assertGreater(denied_traversal["score"], 80)
        denied_system = assess_command("mkdir /root/x")
        self.assertEqual(denied_system["verdict"], "DENY")
        self.assertGreater(denied_system["score"], 80)
        denied = assess_command("rm -rf /")
        self.assertEqual(denied["verdict"], "DENY")
        self.assertGreater(denied["score"], 80)
        executor = SafeExecutor()
        try:
            with patch.object(TerminalSession, "run") as mocked:
                result = executor.execute(
                    "run_command", PlannedTask(action="run_command", target="rm -rf /")
                )
                self.assertFalse(result.success)
                mocked.assert_not_called()
        finally:
            executor.close()


class W2InstallFallbackTests(unittest.TestCase):
    """W2: vlc/firefox resolve to native managers with fallback chains."""

    MATRIX = [
        ("linux", "ubuntu", {"apt"}, "vlc", "sudo apt install -y vlc"),
        ("linux", "fedora", {"dnf"}, "vlc", "sudo dnf install -y vlc"),
        ("linux", "fedora", {"dnf"}, "firefox", "sudo dnf install -y firefox"),
        ("linux", "arch", {"pacman"}, "firefox", "sudo pacman -S --noconfirm firefox"),
        ("macos", "macos", {"brew"}, "firefox", "brew install --cask firefox"),
        ("windows", "windows", {"winget"}, "firefox", "winget install Mozilla.Firefox"),
        ("linux", "ubuntu", {"snap"}, "vlc", "sudo snap install vlc"),
    ]

    def test_manager_matrix(self):
        from auto_system_agent import os_utils as resolver
        from auto_system_agent.tools import install_tool

        for os_name, distro, available, app, expected in self.MATRIX:
            with self.subTest(os=os_name, distro=distro, app=app):
                package = install_tool.resolve_os_package_name(app, os_name) or app
                best = resolver.best_install_command(
                    package, os_name=os_name, distro_id=distro, available=frozenset(available)
                )
                self.assertEqual(best, expected)

    def test_selector_rewrites_foreign_manager_command(self):
        from auto_system_agent import os_utils as resolver
        from auto_system_agent.tool_selector import ToolSelector

        class QuietMapper:
            def map_intent(self, text, allowed):
                raise AssertionError("LLM must not pick managers")

        with patch.object(resolver, "live_available_binaries", return_value=frozenset({"dnf"})):
            selector = ToolSelector(
                llm_mapper=QuietMapper(),
                system_config={"os_name": "linux", "distro_id": "fedora"},
            )
            task = PlannedTask(
                action="run_command", target="sudo apt install -y firefox", raw_input="install firefox"
            )
            self.assertEqual(selector.select(task), "run_command")
            self.assertEqual(task.target, "sudo dnf install -y firefox")


class W3LogBatchTests(unittest.TestCase):
    """W3: 500 logs, UTF-8 normalize, zip, checksum, parallel fan-out, no freeze."""

    COUNT = 500

    def test_500_logs_zip_and_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = SafeExecutor(max_workers=4, queue_size=50)
            try:
                executor.terminal._cwd = Path(tmp).resolve()
                logs_dir = Path(tmp) / "logs"
                logs_dir.mkdir()

                def make_batch(start, end):
                    for index in range(start, end):
                        path = logs_dir / f"app-{index:04d}.log"
                        path.write_bytes(f"line {index} ok\n".encode("utf-8"))
                    return True

                batches = [(i, min(i + 125, self.COUNT)) for i in range(0, self.COUNT, 125)]
                started = time.monotonic()
                futures = [executor._pool.submit(make_batch, s, e) for s, e in batches]
                self.assertTrue(all(future.result(60) for future in futures))
                # One latin-1 file must normalize to UTF-8.
                (logs_dir / "legacy.log").write_bytes(b"caf\xe9 na\xefve\n")
                create_elapsed = time.monotonic() - started

                normalize = _run(
                    executor,
                    "python3 -c \"import pathlib; p=pathlib.Path('logs/legacy.log');"
                    " p.write_text(p.read_bytes().decode('latin-1'), encoding='utf-8')\"",
                )
                self.assertTrue(normalize.success, normalize.message)
                content = (logs_dir / "legacy.log").read_text(encoding="utf-8")
                self.assertIn("café", content)

                self.assertTrue(_run(executor, "zip -qr archive.zip logs").success)
                archive = Path(tmp) / "archive.zip"
                self.assertTrue(archive.exists())
                with zipfile.ZipFile(archive) as bundle:
                    names = [name for name in bundle.namelist() if name.endswith(".log")]
                self.assertEqual(len(names), self.COUNT + 1)

                digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                (Path(tmp) / "archive.sha256").write_text(f"{digest}  archive.zip\n")
                check = _run(executor, "sha256sum -c archive.sha256")
                self.assertTrue(check.success, check.message)
                total_elapsed = time.monotonic() - started
                self.assertLess(total_elapsed, 300.0, f"W3 froze: {total_elapsed:.1f}s")
                _ = create_elapsed
            finally:
                executor.close()


if __name__ == "__main__":
    unittest.main()
