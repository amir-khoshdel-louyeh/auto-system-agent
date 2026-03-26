from dataclasses import dataclass, field
from typing import Any, Literal

from auto_system_agent.models import PlannedTask


TaskAction = Literal[
    "install_app",
    "create_folder",
    "compress",
    "move_path",
    "delete_path",
    "list_files",
    "run_command",
    "help",
    "unknown",
]


@dataclass
class IntermediateTask:
    action: TaskAction
    target: str = ""
    raw_input: str = ""
    options: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.raw_input.strip():
            raise ValueError("raw_input is required")

        if self.action == "move_path":
            destination = str(self.options.get("destination", "")).strip()
            if not self.target.strip() or not destination:
                raise ValueError("move_path requires source target and destination")
            return

        if self.action in {"help", "unknown"}:
            return

        if not self.target.strip():
            raise ValueError(f"{self.action} requires target")

    def to_planned_task(self) -> PlannedTask:
        self.validate()
        return PlannedTask(
            action=self.action,
            target=self.target,
            raw_input=self.raw_input,
            options=dict(self.options),
        )
