"""A4 plan templates: save/replay validated shell plans (P4.4).

A template stores a canonical plan (JSON list of {action, target}) with a
running success rate. Only validated plans are saved: every step parses
and no step carries a DENY verdict.
"""

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_TEMPLATES_PATH = Path.home() / ".auto_system_agent" / "templates.json"

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
    from auto_system_agent.command_guard import assess_command, parse_command

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
    """JSON-file template store with atomic saves."""

    path: Path | str = DEFAULT_TEMPLATES_PATH
    _templates: dict[str, Template] = field(default_factory=dict, repr=False)
    _next_id: int = field(default=1, repr=False)

    def __post_init__(self) -> None:
        self._load()

    @property
    def storage_path(self) -> Path:
        return Path(self.path)

    def _load(self) -> None:
        try:
            raw = self.storage_path.read_text(encoding="utf-8")
        except OSError:
            return
        try:
            data = json.loads(raw)
        except ValueError:
            return
        if not isinstance(data, list):
            return
        for item in data:
            if not isinstance(item, dict) or not str(item.get("name") or ""):
                continue
            try:
                template = Template(
                    id=int(item.get("id") or 0) or None,
                    name=str(item["name"]),
                    canonical_plan_json=json.dumps(item.get("steps", [])),
                    success_rate=float(item.get("success_rate", 100.0)),
                    runs=int(item.get("runs", 0)),
                    created_ts=float(item.get("created_ts", 0.0)),
                )
            except (ValueError, TypeError):
                continue
            self._templates[template.name] = template
            if template.id is not None:
                self._next_id = max(self._next_id, template.id + 1)

    def _persist(self) -> None:
        payload = [
            {
                "id": template.id,
                "name": template.name,
                "steps": template.steps(),
                "success_rate": template.success_rate,
                "runs": template.runs,
                "created_ts": template.created_ts,
            }
            for template in self._templates.values()
        ]
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.storage_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp_path.replace(self.storage_path)
        except OSError:
            return

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
        existing = self._templates.get(clean_name)
        template = Template(
            id=existing.id if existing else self._next_id,
            name=clean_name,
            canonical_plan_json=canonical,
            success_rate=existing.success_rate if existing else 100.0,
            runs=existing.runs if existing else 0,
            created_ts=existing.created_ts if existing else time.time(),
        )
        if existing is None:
            self._next_id += 1
        self._templates[clean_name] = template
        self._persist()
        return template

    def get(self, name: str) -> Template | None:
        return self._templates.get((name or "").strip())

    def list_names(self) -> list[str]:
        return sorted(self._templates)

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
        template.runs = runs
        template.success_rate = round((previous + (100.0 if success else 0.0)) / runs, 1)
        self._persist()
        return template
