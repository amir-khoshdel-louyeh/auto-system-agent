import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from auto_system_agent.settings import LLMSettings, SettingsStore


class SettingsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.settings_path = Path(self._tmp_dir.name) / "settings.json"
        self.store = SettingsStore(path=self.settings_path)
        self._original_env = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._original_env)
        self._tmp_dir.cleanup()

    def test_save_and_load_provider_mode(self):
        settings = LLMSettings(
            provider_mode="custom",
            url="http://local-llm/v1/chat/completions",
            api_key="abc123",
            model="gpt-4o-mini",
            timeout=9.5,
        )

        self.store.save(settings)
        loaded = self.store.load()

        self.assertEqual(loaded.provider_mode, "custom")
        self.assertEqual(loaded.url, "http://local-llm/v1/chat/completions")
        self.assertEqual(loaded.api_key, "abc123")
        self.assertEqual(loaded.model, "gpt-4o-mini")
        self.assertEqual(loaded.timeout, 9.5)

    def test_resolve_uses_custom_values_when_mode_is_custom(self):
        settings = LLMSettings(
            provider_mode="custom",
            url="http://custom/v1/chat/completions",
            api_key="custom-key",
            model="custom-model",
            timeout=11,
        )

        os.environ["AUTO_AGENT_DEFAULT_LLM_URL"] = "http://bundled/v1/chat/completions"
        os.environ["AUTO_AGENT_DEFAULT_LLM_API_KEY"] = "bundled-key"

        resolved = self.store.resolve_llm_config(settings)

        self.assertEqual(resolved["url"], "http://custom/v1/chat/completions")
        self.assertEqual(resolved["api_key"], "custom-key")
        self.assertEqual(resolved["model"], "custom-model")
        self.assertEqual(resolved["timeout"], 11.0)

    def test_resolve_uses_bundled_env_values(self):
        settings = LLMSettings(provider_mode="bundled")

        os.environ["AUTO_AGENT_DEFAULT_LLM_URL"] = "http://bundled/v1/chat/completions"
        os.environ["AUTO_AGENT_DEFAULT_LLM_API_KEY"] = "bundled-key"
        os.environ["AUTO_AGENT_DEFAULT_LLM_MODEL"] = "bundled-model"
        os.environ["AUTO_AGENT_DEFAULT_LLM_TIMEOUT"] = "12"

        resolved = self.store.resolve_llm_config(settings)

        self.assertEqual(resolved["url"], "http://bundled/v1/chat/completions")
        self.assertEqual(resolved["api_key"], "bundled-key")
        self.assertEqual(resolved["model"], "bundled-model")
        self.assertEqual(resolved["timeout"], 12.0)

    def test_load_normalizes_invalid_provider_mode(self):
        self.settings_path.write_text(
            json.dumps({"provider_mode": "unknown", "url": "", "api_key": "", "model": "", "timeout": 8}),
            encoding="utf-8",
        )

        loaded = self.store.load()

        self.assertEqual(loaded.provider_mode, "bundled")


if __name__ == "__main__":
    unittest.main()
