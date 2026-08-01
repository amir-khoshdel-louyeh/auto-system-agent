"""Phase 6 item [55]: frozen intents, slots and the canonical plan schema.

The offline planner is deterministic project-owned logic: a supported request
maps to exactly one :class:`IntentSpec`, whose *declared* slots the extractor
must fill. Nothing here talks to an LLM, Redis or PostgreSQL, so the
vocabulary is unit-testable and the LLM adapter (a later item) can only ever
produce the same typed plan.

Canonical plan → workflow: :meth:`Plan.to_document` emits the frozen DSL
mapping (nodes/edges/policy) that the existing loader + validator already
understand, so a planned workflow goes through the *same* validation and
policy path as a hand-authored one (Phase 6 gate).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Frozen intent vocabulary (FR-06). Adding an intent is a versioned change.
INTENTS: tuple[str, ...] = (
    "create_folder",
    "write_file",
    "create_workspace",
    "install_app",
    "run_command",
    "system_info",
    "list_directory",
    "help",
    "unknown",
)

#: Intents that produce an executable DAG; the rest end in clarification/help.
EXECUTABLE_INTENTS: tuple[str, ...] = (
    "create_folder",
    "write_file",
    "create_workspace",
    "install_app",
    "run_command",
    "system_info",
    "list_directory",
)

#: Frozen slot vocabulary with the value type the extractor must produce.
SLOT_TYPES: dict[str, str] = {
    "path": "path",
    "folder_names": "list[path]",
    "content": "text",
    "workspace_name": "text",
    "app": "text",
    "command": "text",
    "metric": "enum",
    "topic": "text",
}

#: Metrics the `system_info` intent may request (frozen enum).
SYSTEM_METRICS: tuple[str, ...] = ("disk", "memory", "cpu", "uptime", "load", "processes")

#: Read-only commands backing each system metric (no shell interpolation).
METRIC_COMMANDS: dict[str, str] = {
    "disk": "df -h",
    "memory": "free -h",
    "cpu": "nproc",
    "uptime": "uptime",
    "load": "cat /proc/loadavg",
    "processes": "ps -eo pid,comm --sort=-%cpu | head -20",
}

#: Node type every MVP intent compiles to (shell unless stated otherwise).
DEFAULT_NODE_TYPE = "shell"


@dataclass(frozen=True)
class IntentSpec:
    """One supported intent and the slots it needs."""

    name: str
    description: str
    required_slots: tuple[str, ...] = ()
    optional_slots: tuple[str, ...] = ()
    node_type: str = DEFAULT_NODE_TYPE
    #: Intents that must be gated by an approval node in the compiled DAG.
    requires_approval: bool = False
    #: Example utterances used by the dataset and the rules (item [56]/[57]).
    examples: tuple[str, ...] = ()


INTENT_SPECS: dict[str, IntentSpec] = {
    "create_folder": IntentSpec(
        name="create_folder",
        description="Create one or more folders inside the approved workspace.",
        required_slots=("folder_names",),
        optional_slots=("path",),
        examples=(
            "create three folders called reports, logs and backups",
            "make a folder named demo-app",
            "mkdir reports",
        ),
    ),
    "write_file": IntentSpec(
        name="write_file",
        description="Write text content to a file inside the approved workspace.",
        required_slots=("path", "content"),
        examples=(
            "write a README with the text hello team",
            "create notes.md containing meeting at ten",
            "put 'hello' into demo-app/README.md",
        ),
    ),
    "create_workspace": IntentSpec(
        name="create_workspace",
        description="Scaffold a named workspace: folders plus a README.",
        required_slots=("workspace_name",),
        optional_slots=("folder_names", "content"),
        examples=(
            "create a workspace called demo-app with three folders and a README",
            "set up the alpha workspace",
        ),
    ),
    "install_app": IntentSpec(
        name="install_app",
        description="Install an application through the platform package manager.",
        required_slots=("app",),
        requires_approval=True,
        examples=("install firefox", "please install vlc", "I need htop installed"),
    ),
    "run_command": IntentSpec(
        name="run_command",
        description="Run one explicitly requested allow-listed shell command.",
        required_slots=("command",),
        examples=("run ls -la", "execute: df -h", "please run git status"),
    ),
    "system_info": IntentSpec(
        name="system_info",
        description="Report one read-only system metric.",
        required_slots=("metric",),
        examples=(
            "how much disk space is free",
            "show me the memory usage",
            "what is the uptime",
        ),
    ),
    "list_directory": IntentSpec(
        name="list_directory",
        description="List the entries of a directory.",
        required_slots=("path",),
        examples=("list the files in ~/Downloads", "show what is in the reports folder"),
    ),
    "help": IntentSpec(
        name="help",
        description="Explain what the planner can do.",
        optional_slots=("topic",),
        examples=("help", "what can you do"),
    ),
    "unknown": IntentSpec(
        name="unknown",
        description="Not a supported automation request.",
        examples=("tell me a joke", "what is the capital of France"),
    ),
}


@dataclass(frozen=True)
class PlanStep:
    """One canonical plan step; a node plus its dependency edges."""

    id: str
    type: str
    params: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    compensation: str = ""

    def to_node(self) -> dict[str, Any]:
        """DSL node mapping (id, type and flat parameters)."""
        node: dict[str, Any] = {"id": self.id, "type": self.type}
        node.update(self.params)
        if self.compensation:
            node["compensation"] = self.compensation
        return node


@dataclass(frozen=True)
class Plan:
    """Typed planner output: one intent, its slots, and canonical steps."""

    intent: str
    slots: dict[str, Any] = field(default_factory=dict)
    steps: tuple[PlanStep, ...] = ()
    confidence: float = 0.0
    raw_input: str = ""
    needs_clarification: bool = False
    clarification: str = ""
    planner: str = "offline"

    @property
    def executable(self) -> bool:
        """True when the plan can be compiled to a runnable workflow."""
        return (
            self.intent in EXECUTABLE_INTENTS and bool(self.steps) and not self.needs_clarification
        )

    def edges(self) -> list[list[str]]:
        """Dependency edges as the DSL's ``[[src, dst], ...]`` pairs."""
        return [[dependency, step.id] for step in self.steps for dependency in step.depends_on]

    def to_document(
        self,
        name: str,
        *,
        version: int = 1,
        triggers: list[Any] | None = None,
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Emit the frozen DSL document for this plan (never partial)."""
        if not self.executable:
            raise ValueError(f"plan for intent {self.intent!r} is not executable")
        return {
            "workflow": name,
            "version": int(version),
            "triggers": list(triggers) if triggers else [{"manual": True}],
            "nodes": [step.to_node() for step in self.steps],
            "edges": self.edges(),
            "policy": dict(policy) if policy else {},
        }


def spec_for(intent: str) -> IntentSpec:
    """Return the frozen spec for ``intent`` (unknown names fall back to `unknown`)."""
    return INTENT_SPECS.get(intent, INTENT_SPECS["unknown"])


def missing_slots(intent: str, slots: dict[str, Any]) -> tuple[str, ...]:
    """Required slots that are absent or blank, in declaration order."""
    spec = spec_for(intent)
    return tuple(slot for slot in spec.required_slots if not _filled(slots.get(slot)))


def _filled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return len(value) > 0
    return True
