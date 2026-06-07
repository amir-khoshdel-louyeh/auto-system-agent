from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class PlannedTask:
    """Structured intent produced by the planner."""

    action: str
    target: Optional[str] = None
    raw_input: str = ""
    options: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    """Standard result returned by tools and executor."""

    success: bool
    message: str
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StepStatus:
    """Structured progress status emitted while executing steps."""

    step: int
    total: int
    tool: str
    state: str
    message: str = ""


@dataclass
class Evaluation:
    """Verdict from the evaluator about a single executed step."""

    verdict: str = "done"
    reason: str = ""
    fixed_command: str = ""


@dataclass
class ReActStep:
    """One Thought -> Act -> Observe record for the ReAct loop."""

    thought: str
    task: PlannedTask
    tool: str
    result: ExecutionResult
