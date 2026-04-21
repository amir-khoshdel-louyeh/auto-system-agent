import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from auto_system_agent.planner import Planner


def _mock_ollama_response(tasks):
    payload = {"tasks": tasks}
    body = json.dumps({"choices": [{"message": {"content": json.dumps(payload)}}]}).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False
    return mock_resp


class PlannerVariedPhrasingTests(unittest.TestCase):
    def test_ollama_install_is_parsed(self):
        planner = Planner(config={"url": "http://localhost:11434/v1/chat/completions", "model": "llama3.1", "timeout": 5})
        mock_resp = _mock_ollama_response([{"action": "run_command", "target": "sudo apt install -y vlc", "options": {}}])
        with patch("auto_system_agent.planner.request.urlopen", return_value=mock_resp):
            task = planner.plan("install vlc")
            self.assertEqual(task.action, "run_command")
            self.assertEqual(task.target, "sudo apt install -y vlc")

    def test_ollama_multi_step_is_parsed(self):
        planner = Planner(config={"url": "http://localhost:11434/v1/chat/completions", "model": "llama3.1", "timeout": 5})
        mock_resp = _mock_ollama_response([
            {"action": "run_command", "target": "mkdir -p ~/Downloads/demo", "options": {}},
            {"action": "run_command", "target": "ls -la ~/Downloads/demo", "options": {}},
        ])
        with patch("auto_system_agent.planner.request.urlopen", return_value=mock_resp):
            tasks = planner.plan_tasks("create folder demo then list files in demo")
            self.assertEqual(len(tasks), 2)
            self.assertEqual(tasks[0].action, "run_command")
            self.assertEqual(tasks[1].action, "run_command")

    def test_ollama_unavailable_returns_unknown(self):
        planner = Planner(config={"url": "http://localhost:11434/v1/chat/completions", "model": "llama3.1", "timeout": 1})
        with patch("auto_system_agent.planner.request.urlopen", side_effect=Exception("no ollama")):
            task = planner.plan("install vlc")
            self.assertEqual(task.action, "unknown")

    def test_help_via_ollama(self):
        planner = Planner(config={"url": "http://localhost:11434/v1/chat/completions", "model": "llama3.1", "timeout": 5})
        mock_resp = _mock_ollama_response([{"action": "help", "target": "", "options": {}}])
        with patch("auto_system_agent.planner.request.urlopen", return_value=mock_resp):
            task = planner.plan("help")
            self.assertEqual(task.action, "help")

    def test_ollama_unknown_for_chat(self):
        planner = Planner(config={"url": "http://localhost:11434/v1/chat/completions", "model": "llama3.1", "timeout": 5})
        mock_resp = _mock_ollama_response([{"action": "unknown", "target": "", "options": {}}])
        with patch("auto_system_agent.planner.request.urlopen", return_value=mock_resp):
            task = planner.plan("what is the weather?")
            self.assertEqual(task.action, "unknown")


if __name__ == "__main__":
    unittest.main()
