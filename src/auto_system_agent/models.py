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
