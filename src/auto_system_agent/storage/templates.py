"""A4 plan templates: save/replay validated shell plans (P4.4).

A template stores a canonical plan (JSON list of {action, target}) with a
running success rate. Only validated plans are saved: every step parses
and no step carries a DENY verdict.
"""

import json
import time
from dataclasses import dataclass
from pathlib import Path

_ALLOWED_ACTIONS = {"run_command", "help"}


@dataclass
class Template:
    """One saved plan: id, name, canonical JSON, running success rate."""

    id: int | None = None
    name: str = ""
    canonical_plan_json: str = "[]"
    success_rate: float = 100.0
    runs: int = 0
    created_ts: float = 0.0

    def steps(self) -> list[dict]:
        """Decoded plan steps (validated at save time)."""
        data = json.loads(self.canonical_plan_json)
        return data if isinstance(data, list) else []


def validate_plan(steps: list[dict]) -> tuple[bool, str]:
    """Check steps are allow-listed, parseable and never DENY."""
    from auto_system_agent.safety.command_guard import assess_command, parse_command

    if not steps:
        return False, "plan is empty"
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            return False, f"step {index} is not an object"
        action = str(step.get("action") or "")
        target = str(step.get("target") or "")
        if action not in _ALLOWED_ACTIONS:
            return False, f"step {index} uses unsupported action {action!r}"
        if action == "run_command":
            if not target.strip():
                return False, f"step {index} has an empty command"
            try:
                parse_command(target)
            except Exception as exc:
                return False, f"step {index} does not parse: {exc}"
            try:
                assessment = assess_command(target)
            except Exception as exc:
                return False, f"step {index} cannot be scored: {exc}"
            if assessment["verdict"] == "DENY":
                reason = (assessment["reasons"] or ["deny-list"])[0]
                return False, f"step {index} is DENY: {reason}"
    return True, "ok"


@dataclass
class TemplateStore:
    """SQLite-backed template store (audit database template table)."""

    repository: object | None = None
    db_path: Path | str | None = None

    def __post_init__(self) -> None:
        if self.repository is None:
            from auto_system_agent.storage.repository import AuditRepository

            self.repository = (
                AuditRepository(self.db_path) if self.db_path is not None else AuditRepository()
            )

    @staticmethod
    def _row_to_template(row: dict) -> Template:
        return Template(
            id=row.get("id"),
            name=str(row.get("name") or ""),
            canonical_plan_json=str(row.get("canonical_plan_json") or "[]"),
            success_rate=float(row.get("success_rate", 100.0)),
            runs=int(row.get("runs", 0)),
            created_ts=float(row.get("created_ts", 0.0)),
        )

    def save(self, name: str, steps: list[dict]) -> Template:
        """Validate and store a plan; raises ValueError when invalid."""
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("template name is empty")
        ok, reason = validate_plan(steps)
        if not ok:
            raise ValueError(f"invalid plan: {reason}")
        canonical = json.dumps(
            [{"action": str(step["action"]), "target": str(step.get("target") or "")} for step in steps]
        )
        existing = self.get(clean_name)
        template_id = self.repository.upsert_template(
            name=clean_name,
            canonical_plan_json=canonical,
            success_rate=existing.success_rate if existing else 100.0,
            runs=existing.runs if existing else 0,
            created_ts=existing.created_ts if existing and existing.created_ts else time.time(),
        )
        return Template(
            id=template_id,
            name=clean_name,
            canonical_plan_json=canonical,
            success_rate=existing.success_rate if existing else 100.0,
            runs=existing.runs if existing else 0,
            created_ts=existing.created_ts if existing else time.time(),
        )

    def get(self, name: str) -> Template | None:
        row = self.repository.get_template((name or "").strip())
        return self._row_to_template(row) if row is not None else None

    def list_names(self) -> list[str]:
        return [str(row["name"]) for row in self.repository.list_templates()]

    def replay(self, name: str, raw_input: str = "") -> list:
        """Rebuild validated PlannedTasks for a saved template."""
        from auto_system_agent.models import PlannedTask

        template = self.get(name)
        if template is None:
            raise KeyError(f"unknown template: {name}")
        tasks = [
            PlannedTask(action=str(step["action"]), target=str(step.get("target") or ""), raw_input=raw_input)
            for step in template.steps()
        ]
        ok, reason = validate_plan(
            [{"action": task.action, "target": task.target or ""} for task in tasks]
        )
        if not ok:
            raise ValueError(f"stored plan no longer validates: {reason}")
        return tasks

    def record_run(self, name: str, success: bool) -> Template:
        """Fold one replay outcome into the running success rate."""
        template = self.get(name)
        if template is None:
            raise KeyError(f"unknown template: {name}")
        runs = template.runs + 1
        previous = template.success_rate * template.runs
        rate = round((previous + (100.0 if success else 0.0)) / runs, 1)
        self.repository.upsert_template(
            name=template.name,
            canonical_plan_json=template.canonical_plan_json,
            success_rate=rate,
            runs=runs,
            created_ts=template.created_ts,
        )
        template.runs = runs
        template.success_rate = rate
        return template
