import json
import os
from dataclasses import dataclass
from pathlib import Path


OLLAMA_DEFAULT_URL = "http://localhost:11434/v1/chat/completions"
OLLAMA_DEFAULT_MODEL = "llama3.1"


@dataclass
class LLMSettings:
    provider_mode: str = "local"
    url: str = ""
    api_key: str = ""
    model: str = OLLAMA_DEFAULT_MODEL
    timeout: float = 30.0
    gui_timeout_seconds: float = 300.0
    install_retries: int = 2
    confirm_high_risk: bool = True
    window_geometry: str = "920x560"
    system_config: dict | None = None  # persisted SystemConfig dict (see system_info.SystemConfig)


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

        timeout_value = payload.get("timeout", 30.0)
        try:
            timeout = float(timeout_value)
        except (TypeError, ValueError):
            timeout = 30.0

        gui_timeout_value = payload.get("gui_timeout_seconds", 300.0)
        try:
            gui_timeout_seconds = float(gui_timeout_value)
        except (TypeError, ValueError):
            gui_timeout_seconds = 300.0

        retries_value = payload.get("install_retries", 2)
        try:
            install_retries = int(retries_value)
        except (TypeError, ValueError):
            install_retries = 2

        confirm_high_risk = bool(payload.get("confirm_high_risk", True))

        window_geometry = self._normalize_window_geometry(payload.get("window_geometry", "920x560"))

        # System config persisted as dict; keep as-is for lazy migration
        system_config = payload.get("system_config")
        if not isinstance(system_config, dict):
            system_config = None

        return LLMSettings(
            provider_mode=self._normalize_provider_mode(payload.get("provider_mode", "local")),
            url=str(payload.get("url", "")).strip(),
            api_key=str(payload.get("api_key", "")).strip(),
            model=str(payload.get("model", OLLAMA_DEFAULT_MODEL)).strip() or OLLAMA_DEFAULT_MODEL,
            timeout=timeout,
            gui_timeout_seconds=gui_timeout_seconds,
            install_retries=max(0, install_retries),
            confirm_high_risk=confirm_high_risk,
            window_geometry=window_geometry,
            system_config=system_config,
        )

    def save(self, settings: LLMSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "provider_mode": self._normalize_provider_mode(settings.provider_mode),
            "url": settings.url.strip(),
            "api_key": settings.api_key.strip(),
            "model": settings.model.strip() or OLLAMA_DEFAULT_MODEL,
            "timeout": float(settings.timeout),
            "gui_timeout_seconds": float(settings.gui_timeout_seconds),
            "install_retries": int(settings.install_retries),
            "confirm_high_risk": bool(settings.confirm_high_risk),
            "window_geometry": self._normalize_window_geometry(settings.window_geometry),
            "system_config": settings.system_config if isinstance(settings.system_config, dict) else None,
        }
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def get_system_config(self, settings: LLMSettings):
        """Return SystemConfig from settings or freshly detected if missing."""
        from auto_system_agent.system_info import SystemConfig, detect_system_config, system_config_from_dict

        if isinstance(settings.system_config, dict) and settings.system_config:
            return system_config_from_dict(settings.system_config)
        # No persisted config – detect and persist lazily is handled by GUI window
        return detect_system_config()

    def resolve_llm_config(self, settings: LLMSettings) -> dict:
        """Build runtime config from local/API sources. Mandatory startup choice decides mode."""
        provider_mode = self._normalize_provider_mode(settings.provider_mode)

        if provider_mode == "api":
            url = (
                settings.url.strip()
                or os.getenv("AUTO_AGENT_OLLAMA_URL", "").strip()
                or os.getenv("AUTO_AGENT_LLM_URL", "").strip()
                or OLLAMA_DEFAULT_URL
            )
            api_key = settings.api_key.strip() or os.getenv("AUTO_AGENT_OLLAMA_API_KEY", "").strip() or os.getenv("AUTO_AGENT_LLM_API_KEY", "").strip()
            model = (
                settings.model.strip()
                or os.getenv("AUTO_AGENT_OLLAMA_MODEL", "").strip()
                or os.getenv("AUTO_AGENT_LLM_MODEL", "").strip()
                or OLLAMA_DEFAULT_MODEL
            )
            timeout = self._coerce_timeout(settings.timeout, fallback=30.0)
            return {
                "url": url,
                "api_key": api_key,
                "model": model,
                "timeout": timeout,
            }

        # Local mode (ollama) - uses local instance; settings url/model if provided else defaults
        url = (
            settings.url.strip()
            or os.getenv("AUTO_AGENT_OLLAMA_URL", "").strip()
            or os.getenv("AUTO_AGENT_DEFAULT_LLM_URL", "").strip()
            or os.getenv("AUTO_AGENT_LLM_URL", "").strip()
            or OLLAMA_DEFAULT_URL
        )
        api_key = (
            settings.api_key.strip()
            or os.getenv("AUTO_AGENT_OLLAMA_API_KEY", "").strip()
            or os.getenv("AUTO_AGENT_DEFAULT_LLM_API_KEY", "").strip()
            or os.getenv("AUTO_AGENT_LLM_API_KEY", "").strip()
        )
        model = (
            settings.model.strip()
            or os.getenv("AUTO_AGENT_OLLAMA_MODEL", "").strip()
            or os.getenv("AUTO_AGENT_DEFAULT_LLM_MODEL", "").strip()
            or os.getenv("AUTO_AGENT_LLM_MODEL", "").strip()
            or OLLAMA_DEFAULT_MODEL
        )
        timeout = self._coerce_timeout(settings.timeout, fallback=30.0)
        # For local mode ignore env timeout; use settings timeout directly (fallback already handled)
        # Keep backward compat: if env timeout was set but settings timeout is default, prefer env? No, prefer settings.
        return {
            "url": url,
            "api_key": api_key,
            "model": model,
            "timeout": timeout,
        }

    def _normalize_provider_mode(self, value: object) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"local", "api"}:
            return normalized
        # Backward compatibility: bundled -> local, custom -> api
        if normalized == "bundled":
            return "local"
        if normalized == "custom":
            return "api"
        # Also accept ollama/remote aliases
        if normalized in {"ollama", "local_ollama"}:
            return "local"
        if normalized in {"remote", "openai", "external"}:
            return "api"
        return "local"

    def _coerce_timeout(self, value: object, fallback: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(fallback)

    def _normalize_window_geometry(self, value: object) -> str:
        import re

        text = str(value or "").strip() or "920x560"
        if re.match(r"^\d+x\d+(?:[+-]\d+[+-]\d+)?$", text):
            try:
                wh_part = text.split("+")[0].split("-")[0]
                w_str, h_str = wh_part.lower().split("x")
                w, h = int(w_str), int(h_str)
                if 400 <= w <= 3840 and 300 <= h <= 2160:
                    return text
            except Exception:
                pass
        return "920x560"
