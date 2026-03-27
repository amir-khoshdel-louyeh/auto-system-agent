import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from auto_system_agent.tools.install_tool import build_install_command, extract_known_apps, verify_install_environment


class InstallToolTests(unittest.TestCase):
    def test_build_install_command_uses_apt_on_debian_family(self):
        with patch("auto_system_agent.tools.install_tool.detect_os", return_value="linux"):
            with patch("auto_system_agent.tools.install_tool.detect_linux_package_manager", return_value="apt"):
                result = build_install_command("vlc")

        self.assertTrue(result.success)
        self.assertEqual(result.data["command"][:3], ["sudo", "apt", "install"])

    def test_build_install_command_uses_dnf_on_fedora_family(self):
        with patch("auto_system_agent.tools.install_tool.detect_os", return_value="linux"):
            with patch("auto_system_agent.tools.install_tool.detect_linux_package_manager", return_value="dnf"):
                result = build_install_command("vlc")

        self.assertTrue(result.success)
        self.assertEqual(result.data["command"][:3], ["sudo", "dnf", "install"])

    def test_build_install_command_uses_pacman_on_arch_family(self):
        with patch("auto_system_agent.tools.install_tool.detect_os", return_value="linux"):
            with patch("auto_system_agent.tools.install_tool.detect_linux_package_manager", return_value="pacman"):
                result = build_install_command("vlc")

        self.assertTrue(result.success)
        self.assertEqual(result.data["command"][:3], ["sudo", "pacman", "-S"])

    def test_build_install_command_matches_synonym_with_confidence(self):
        with patch("auto_system_agent.tools.install_tool.detect_os", return_value="linux"):
            with patch("auto_system_agent.tools.install_tool.detect_linux_package_manager", return_value="apt"):
                result = build_install_command("chrome")

        self.assertTrue(result.success)
        self.assertEqual(result.data["matched_app"], "google chrome")
        self.assertGreaterEqual(result.data["match_confidence"], 0.9)

    def test_extract_known_apps_detects_aliases(self):
        known = extract_known_apps("please install chrome and vlc media player")

        self.assertIn("google chrome", known)
        self.assertIn("vlc", known)

    def test_verify_install_environment_checks_sudo_and_package_manager(self):
        with patch.object(shutil, "which", side_effect=lambda name: None if name == "dnf" else "/usr/bin/sudo"):
            result = verify_install_environment(["sudo", "dnf", "install", "-y", "vlc"])

        self.assertFalse(result.success)
        self.assertIn("Package manager not found", result.message)


if __name__ == "__main__":
    unittest.main()
