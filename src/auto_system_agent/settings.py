import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class LLMSettings:
    url: str = ""
    api_key: str = ""
    model: str = "gpt-4o-mini"
    timeout: float = 8.0


class SettingsStore:
    """Persists app settings under the user's home directory."""

    def __init__(self, path: Path | None = None) -> None:
        default_path = Path.home() / ".auto_system_agent" / "settings.json"
        self._path = path or default_path

    def load(self) -> LLMSettings:
        if not self._path.exists():
            return LLMSettings()

        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return LLMSettings()

        timeout_value = payload.get("timeout", 8.0)
        try:
            timeout = float(timeout_value)
        except (TypeError, ValueError):
            timeout = 8.0

        return LLMSettings(
            url=str(payload.get("url", "")).strip(),
            api_key=str(payload.get("api_key", "")).strip(),
            model=str(payload.get("model", "gpt-4o-mini")).strip() or "gpt-4o-mini",
            timeout=timeout,
        )

    def save(self, settings: LLMSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "url": settings.url.strip(),
            "api_key": settings.api_key.strip(),
            "model": settings.model.strip() or "gpt-4o-mini",
            "timeout": float(settings.timeout),
        }
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
