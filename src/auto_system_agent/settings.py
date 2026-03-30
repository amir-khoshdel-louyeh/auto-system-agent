import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class LLMSettings:
    provider_mode: str = "bundled"
    url: str = ""
    api_key: str = ""
    model: str = "gpt-4o-mini"
    timeout: float = 8.0
    gui_timeout_seconds: float = 45.0
    install_retries: int = 2
    confirm_high_risk: bool = True


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

        gui_timeout_value = payload.get("gui_timeout_seconds", 45.0)
        try:
            gui_timeout_seconds = float(gui_timeout_value)
        except (TypeError, ValueError):
            gui_timeout_seconds = 45.0

        retries_value = payload.get("install_retries", 2)
        try:
            install_retries = int(retries_value)
        except (TypeError, ValueError):
            install_retries = 2

        confirm_high_risk = bool(payload.get("confirm_high_risk", True))

        return LLMSettings(
            provider_mode=self._normalize_provider_mode(payload.get("provider_mode", "bundled")),
            url=str(payload.get("url", "")).strip(),
            api_key=str(payload.get("api_key", "")).strip(),
            model=str(payload.get("model", "gpt-4o-mini")).strip() or "gpt-4o-mini",
            timeout=timeout,
            gui_timeout_seconds=gui_timeout_seconds,
            install_retries=max(0, install_retries),
            confirm_high_risk=confirm_high_risk,
        )

    def save(self, settings: LLMSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "provider_mode": self._normalize_provider_mode(settings.provider_mode),
            "url": settings.url.strip(),
            "api_key": settings.api_key.strip(),
            "model": settings.model.strip() or "gpt-4o-mini",
            "timeout": float(settings.timeout),
            "gui_timeout_seconds": float(settings.gui_timeout_seconds),
            "install_retries": int(settings.install_retries),
            "confirm_high_risk": bool(settings.confirm_high_risk),
        }
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def resolve_llm_config(self, settings: LLMSettings) -> dict:
        """Build runtime config for LLM clients from bundled/custom sources."""
        provider_mode = self._normalize_provider_mode(settings.provider_mode)

        if provider_mode == "custom":
            url = settings.url.strip() or os.getenv("AUTO_AGENT_LLM_URL", "").strip()
            api_key = settings.api_key.strip() or os.getenv("AUTO_AGENT_LLM_API_KEY", "").strip()
            model = settings.model.strip() or os.getenv("AUTO_AGENT_LLM_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
            timeout = self._coerce_timeout(settings.timeout, fallback=8.0)
            return {
                "url": url,
                "api_key": api_key,
                "model": model,
                "timeout": timeout,
            }

        # Bundled mode lets app distributors define a ready-to-use provider.
        url = os.getenv("AUTO_AGENT_DEFAULT_LLM_URL", "").strip() or os.getenv("AUTO_AGENT_LLM_URL", "").strip()
        api_key = os.getenv("AUTO_AGENT_DEFAULT_LLM_API_KEY", "").strip() or os.getenv("AUTO_AGENT_LLM_API_KEY", "").strip()
        model = (
            os.getenv("AUTO_AGENT_DEFAULT_LLM_MODEL", "").strip()
            or os.getenv("AUTO_AGENT_LLM_MODEL", "").strip()
            or "gpt-4o-mini"
        )
        timeout_env = os.getenv("AUTO_AGENT_DEFAULT_LLM_TIMEOUT", "").strip() or os.getenv("AUTO_AGENT_LLM_TIMEOUT", "").strip()
        timeout = self._coerce_timeout(timeout_env, fallback=8.0)

        return {
            "url": url,
            "api_key": api_key,
            "model": model,
            "timeout": timeout,
        }

    def _normalize_provider_mode(self, value: object) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"bundled", "custom"}:
            return normalized
        return "bundled"

    def _coerce_timeout(self, value: object, fallback: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(fallback)
