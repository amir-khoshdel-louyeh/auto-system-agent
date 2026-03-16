import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class EventLogger:
    """Writes structured interaction events to a local JSONL log file."""

    def __init__(self, log_path: Path | None = None) -> None:
        default_path = Path.home() / ".auto_system_agent" / "logs" / "events.jsonl"
        self._log_path = log_path or default_path

    def log(self, event: dict[str, Any]) -> None:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **event,
        }
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as file_obj:
                file_obj.write(json.dumps(payload, ensure_ascii=True) + "\n")
        except OSError:
            # Logging must never break agent execution.
            return
