"""P2.5: OS x package-manager resolver matrix (mocked which / os-release)."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from auto_system_agent.platforms import os_utils as resolver
from auto_system_agent.models import PlannedTask
from auto_system_agent.platforms.tool_selector import ToolSelector


class StrictMapper:
    """Explodes if the LLM is consulted for manager choice."""

    def __init__(self, fallback="run_command"):
        self.calls = []
        self._fallback = fallback

    def map_intent(self, text, allowed):
        self.calls.append(text)
        if text.startswith("install ") or "install " in text:
            raise AssertionError(f"LLM must never pick a manager for: {text!r}")
        return self._fallback


class ResolverMatrixTests(unittest.TestCase):
    """Native managers win per OS/distro; fallbacks engage when bins miss."""

    MATRIX = [
        # (os, distro, available, expected head command)
        ("linux", "ubuntu", {"apt"}, "sudo apt install -y vlc"),
        ("linux", "debian", {"apt"}, "sudo apt install -y vlc"),
        ("linux", "fedora", {"dnf"}, "sudo dnf install -y vlc"),
        ("linux", "arch", {"pacman"}, "sudo pacman -S --noconfirm vlc"),
        ("linux", "opensuse-tumbleweed", {"zypper"}, "sudo zypper install -y vlc"),
        ("linux", "alpine", {"apk"}, "sudo apk add vlc"),
        ("macos", "macos", {"brew"}, "brew install --cask vlc"),
        ("windows", "windows", {"winget"}, "winget install vlc"),
        ("windows", "windows", {"choco"}, "choco install -y vlc"),
        # Fallbacks when the native binary is missing.
        ("linux", "ubuntu", {"snap"}, "sudo snap install vlc"),
        ("linux", "ubuntu", {"flatpak"}, "flatpak install -y flathub vlc"),
        ("linux", "fedora", {"flatpak"}, "flatpak install -y flathub vlc"),
        ("linux", "fedora", set(), None),
    ]

    def test_matrix_heads(self):
        for os_name, distro, available, expected in self.MATRIX:
            with self.subTest(os=os_name, distro=distro, available=sorted(available)):
                best = resolver.best_install_command(
                    "vlc", os_name=os_name, distro_id=distro, available=frozenset(available)
                )
                self.assertEqual(best, expected)

    REMOVE_MATRIX = [
        # (os, distro, available, package, expected head command)
        ("linux", "fedora", {"dnf"}, "vlc", "sudo dnf remove -y vlc"),
        ("linux", "ubuntu", {"apt"}, "vlc", "sudo apt remove -y vlc"),
        ("linux", "arch", {"pacman"}, "vlc", "sudo pacman -Rns vlc"),
        ("linux", "opensuse-tumbleweed", {"zypper"}, "vlc", "sudo zypper remove -y vlc"),
        ("macos", "macos", {"brew"}, "vlc", "brew uninstall vlc"),
        ("windows", "windows", {"winget"}, "vlc", "winget uninstall --id vlc"),
        ("linux", "fedora", {"flatpak"}, "vlc", "flatpak uninstall -y vlc"),
        ("linux", "fedora", set(), "vlc", None),
    ]

    def test_remove_matrix_heads(self):
        for os_name, distro, available, package, expected in self.REMOVE_MATRIX:
            with self.subTest(os=os_name, distro=distro, available=sorted(available)):
                best = resolver.best_remove_command(
                    package, os_name=os_name, distro_id=distro, available=frozenset(available)
                )
                self.assertEqual(best, expected)

    def test_uninstall_extraction_skips_wipe_flags(self):
        cases = [
            ("flatpak uninstall --delete-data com.spotify.Client", "com.spotify.Client"),
            ("sudo pacman -Rns vlc", "vlc"),
            ("sudo dnf remove -y vlc", "vlc"),
            ("uninstall vlc", "vlc"),
            ("ls -la /tmp", None),
        ]
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(resolver.extract_uninstall_package(command), expected)

    def test_rewrite_chain_orders_native_before_fallback(self):
        chain = resolver.rewrite_install(
            "vlc", os_name="linux", distro_id="fedora", available=frozenset({"dnf", "flatpak", "snap"})
        )
        self.assertTrue(chain[0].startswith("sudo dnf"), chain)
        self.assertEqual(len(chain), 3, chain)

    def test_unknown_capability_resolves_empty(self):
        self.assertEqual(
            resolver.resolve("nope", os_name="linux", distro_id="ubuntu", available=frozenset({"apt"})),
            [],
        )

    def test_live_probe_uses_which(self):
        with patch("shutil.which", side_effect=lambda name: f"/usr/bin/{name}" if name == "dnf" else None):
            self.assertEqual(resolver.live_available_binaries(), frozenset({"dnf"}))

    def test_distro_detection_from_injected_release(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            release = Path(tmp) / "os-release"
            release.write_text('ID="fedora"\n', encoding="utf-8")
            self.assertEqual(resolver.detect_linux_distro(release), "fedora")
            self.assertEqual(resolver.detect_linux_package_manager("fedora"), "dnf")
            self.assertEqual(resolver.detect_linux_distro(Path(tmp) / "missing"), "unknown")


class SelectorDeterminismTests(unittest.TestCase):
    """select() rewrites installs without the LLM; other intents untouched."""

    def _select(self, command, system_config, available):
        mapper = StrictMapper()
        with patch.object(
            resolver, "live_available_binaries", return_value=frozenset(available)
        ):
            selector = ToolSelector(llm_mapper=mapper, system_config=system_config)
            task = PlannedTask(action="run_command", target=command, raw_input=command)
            tool = selector.select(task)
        return tool, task, mapper

    def test_fedora_rewrites_apt_without_llm(self):
        tool, task, mapper = self._select(
            "sudo apt install -y vlc",
            {"os_name": "linux", "distro_id": "fedora"},
            {"dnf"},
        )
        self.assertEqual(tool, "run_command")
        self.assertEqual(task.target, "sudo dnf install -y vlc")
        self.assertEqual(mapper.calls, [])

    def test_windows_maps_library_package_id(self):
        tool, task, mapper = self._select(
            "sudo apt install -y vlc",
            {"os_name": "windows", "distro_id": "windows"},
            {"winget"},
        )
        self.assertEqual(tool, "run_command")
        self.assertEqual(task.target, "winget install VideoLAN.VLC")
        self.assertEqual(mapper.calls, [])

    def test_native_command_left_alone(self):
        tool, task, mapper = self._select(
            "sudo apt install -y vlc",
            {"os_name": "linux", "distro_id": "ubuntu"},
            {"apt"},
        )
        self.assertEqual(tool, "run_command")
        self.assertEqual(task.target, "sudo apt install -y vlc")
        self.assertEqual(mapper.calls, [])

    def test_uninstall_rewrites_to_native_remove(self):
        tool, task, mapper = self._select(
            "flatpak uninstall --delete-data org.videolan.VLC",
            {"os_name": "linux", "distro_id": "fedora"},
            {"dnf", "flatpak"},
        )
        self.assertEqual(tool, "run_command")
        self.assertEqual(task.target, "sudo dnf remove -y vlc")
        self.assertEqual(mapper.calls, [])

    def test_unknown_ref_left_for_guard(self):
        tool, task, mapper = self._select(
            "flatpak uninstall --delete-data com.spotify.Client",
            {"os_name": "linux", "distro_id": "fedora"},
            {"dnf", "flatpak"},
        )
        self.assertEqual(tool, "run_command")
        self.assertEqual(task.target, "flatpak uninstall --delete-data com.spotify.Client")
        self.assertEqual(mapper.calls, [])

    def test_non_install_commands_untouched(self):
        tool, task, mapper = self._select(
            "ls -la /tmp",
            {"os_name": "linux", "distro_id": "fedora"},
            {"dnf"},
        )
        self.assertEqual(tool, "run_command")
        self.assertEqual(task.target, "ls -la /tmp")
        self.assertEqual(mapper.calls, [])

    def test_unknown_action_still_uses_llm_for_intent(self):
        mapper = StrictMapper(fallback="help")
        selector = ToolSelector(llm_mapper=mapper, system_config={"os_name": "linux"})
        task = PlannedTask(action="mystery", target="", raw_input="show help")
        self.assertEqual(selector.select(task), "help")
        self.assertEqual(mapper.calls, ["show help"])


if __name__ == "__main__":
    unittest.main()
