import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from auto_system_agent.storage.repository import AuditRepository

_SUMMARY_REF_LIMIT = 2000


def _shorten(value: object) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= _SUMMARY_REF_LIMIT else text[:_SUMMARY_REF_LIMIT] + "…"


class EventLogger:
    """Writes structured interaction events to JSONL and the audit DB."""

    def __init__(
        self,
        log_path: Path | None = None,
        repository: AuditRepository | None = None,
        db_path: Path | str | None = None,
    ) -> None:
        default_path = Path.home() / ".auto_system_agent" / "logs" / "events.jsonl"
        self._log_path = log_path or default_path
        # Lazy repository: constructing a logger must not touch disk; the
        # audit DB is created on the first mirrored write instead.
        self._repository: AuditRepository | None = repository
        self._db_path = Path(db_path) if db_path is not None else None
        self._repository_failed = False

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
            pass
        try:
            self._mirror_to_db(payload)
        except Exception:
            # The DB mirror is best-effort; JSONL stays the source of truth.
            return

    def _mirror_to_db(self, payload: dict[str, Any]) -> None:
        repository = self._repository
        if repository is None and not self._repository_failed:
            try:
                repository = self._repository = (
                    AuditRepository(self._db_path) if self._db_path is not None else AuditRepository()
                )
            except Exception:
                self._repository_failed = True
                return
        if repository is None:
            return
        steps = payload.get("steps")
        if not isinstance(steps, list):
            steps = []
        planned = payload.get("planned_tasks")
        if not isinstance(planned, list):
            planned = []
        canonical_cmd = "; ".join(
            str(item.get("target") or "")
            for item in planned
            if isinstance(item, dict) and str(item.get("target") or "")
        )
        verdict, risk_score, exit_code = self._summarize_outcome(payload, steps)
        try:
            from auto_system_agent.platforms.os_utils import detect_os

            os_name = detect_os()
        except Exception:
            os_name = ""
        execution_id = repository.record_execution(
            nl_intent=str(payload.get("user_input") or ""),
            canonical_cmd=canonical_cmd,
            os_name=os_name,
            verdict=verdict,
            risk_score=risk_score,
            exit_code=exit_code,
            ts_start=str(payload.get("timestamp") or ""),
        )
        for seq, item in enumerate(steps):
            if not isinstance(item, dict):
                continue
            result = item.get("result")
            succeeded = isinstance(result, dict) and bool(result.get("success"))
            message = result.get("message") if isinstance(result, dict) else ""
            chain = item.get("chain") if isinstance(item.get("chain"), dict) else {}
            prefix = ""
            if chain.get("step_hash"):
                prefix = f"chain:{chain.get('step_hash')} prev:{chain.get('prev_hash', '')} "
            repository.record_step(
                execution_id,
                seq=seq,
                state="done" if succeeded else "failed",
                stdout_ref=(prefix + _shorten(message)) if succeeded else "",
                stderr_ref="" if succeeded else (prefix + _shorten(message)),
                retry_count=0,
            )

    @staticmethod
    def _summarize_outcome(
        payload: dict[str, Any], steps: list
    ) -> tuple[str, int | None, int | None]:
        """Derive (verdict, risk_score, exit_code) from step audits/results."""
        best_score: int | None = None
        last_success: bool | None = None
        blocked = False
        for item in steps:
            if not isinstance(item, dict):
                continue
            audit = item.get("audit") if isinstance(item.get("audit"), dict) else {}
            score = audit.get("risk_score")
            if isinstance(score, bool):
                score = None
            if isinstance(score, (int, float)):
                best_score = int(score) if best_score is None else max(best_score, int(score))
            decision = str(audit.get("decision") or "")
            if decision == "blocked":
                blocked = True
            result = item.get("result")
            if isinstance(result, dict) and "success" in result:
                last_success = bool(result.get("success"))
        if blocked:
            verdict = "DENY"
        elif best_score is not None:
            verdict = "DENY" if best_score > 70 else ("CONFIRM" if best_score > 30 else "ALLOW")
        elif str(payload.get("mode") or "") == "confirmation_requested":
            verdict = "CONFIRM"
        else:
            verdict = "ALLOW"
        exit_code = None if last_success is None else (0 if last_success else 1)
        return verdict, best_score, exit_code
