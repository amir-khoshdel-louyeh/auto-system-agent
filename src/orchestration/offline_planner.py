"""Phase 6 item [58]: the offline planner and its canonical task steps.

Pipeline (proposal §10.2, offline half)::

    raw request -> normalize -> high-precision rule -> slots
                -> canonical steps -> typed Plan -> DSL -> validator/policy

Every step is a *data* description of one workflow node — no shell string is
executed here and no callable is embedded — so the produced plan can be
validated and policy-gated by exactly the same code paths as a hand-written
YAML workflow (Phase 6 gate).

Step construction is deterministic and dependency-aware: independent folders
become parallel nodes, the workspace root is created before its children, and
an install request carries an explicit approval node in front of the shell
node (its `requires_approval` intent metadata).
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from .intents import (
    EXECUTABLE_INTENTS,
    INTENT_SPECS,
    METRIC_COMMANDS,
    Plan,
    PlanStep,
    missing_slots,
    spec_for,
)
from .rules import classify
from .slots import extract_slots

#: Steps the planner will emit before it refuses the plan as too large.
MAX_STEPS = 16

#: Confidence below this is not trusted without clarification (item [59]).
DEFAULT_CONFIDENCE_THRESHOLD = 0.75

#: Fallback question when no rule matched (the capability list is attached).
UNSUPPORTED_QUESTION = (
    "I could not map that request to a supported automation. What would you like to do?"
)

#: One question per missing slot, so the clarification is actionable.
SLOT_QUESTIONS: dict[str, str] = {
    "folder_names": "What should the folder(s) be called?",
    "path": "Which file or directory should I use?",
    "content": "What text should go into the file?",
    "workspace_name": "What should the workspace be called?",
    "app": "Which application should I install?",
    "command": "Which command should I run?",
    "metric": "Which metric do you want: disk, memory, cpu, uptime, load or processes?",
}

#: Questions for an intent whose detail is missing but whose slot is not the
#: one the generic table describes (e.g. listing needs a directory).
INTENT_SLOT_QUESTIONS: dict[tuple[str, str], str] = {
    ("list_directory", "path"): "Which directory should I list?",
    ("write_file", "path"): "Which file should I write?",
}


def capability_text() -> str:
    """Human-readable list of the supported automations."""
    lines = ["I can plan these automations:"]
    lines += [
        f"- {name}: {INTENT_SPECS[name].description}"
        for name in EXECUTABLE_INTENTS
        if name in INTENT_SPECS
    ]
    return "\n".join(lines)


def clarification_for(intent: str, slot: str) -> str:
    """The question that unblocks one missing required slot."""
    return INTENT_SLOT_QUESTIONS.get((intent, slot)) or SLOT_QUESTIONS.get(
        slot, f"I need one more detail: what should '{slot}' be?"
    )


def is_help(text: str) -> bool:
    """True when the request asks for the capability list."""
    match = classify(text)
    return match is not None and match.intent == "help"


@dataclass(frozen=True)
class PlannerConfig:
    """Planner policy knobs (configuration, not scattered constants)."""

    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    max_steps: int = MAX_STEPS
    #: Folders scaffolded by `create_workspace` when the request names none.
    default_folders: tuple[str, ...] = ("logs", "reports", "backups")
    #: Platform-neutral install command; the runtime selector rewrites the
    #: manager for the host (plan.tmp: ToolSelector owns manager choice).
    install_template: str = "sudo apt install -y {app}"


def _slug(value: str, fallback: str = "node") -> str:
    """A node-id-safe slug (ids must be unique and non-empty)."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return slug or fallback


def _unique(base: str, taken: set[str]) -> str:
    candidate = base
    index = 2
    while candidate in taken:
        candidate = f"{base}-{index}"
        index += 1
    taken.add(candidate)
    return candidate


def _quote(value: str) -> str:
    """Single-quote a shell argument (no interpolation, no surprises)."""
    return shlex.quote(str(value))


def folder_nodes(folders: list[str], *, prefix: str = "") -> list[PlanStep]:
    """One `mkdir -p` node per folder — independent nodes run in parallel."""
    taken: set[str] = set()
    steps: list[PlanStep] = []
    for folder in folders:
        path = f"{prefix}{folder}" if prefix else folder
        steps.append(
            PlanStep(
                id=_unique(f"mkdir-{_slug(folder)}", taken),
                type="shell",
                params={"run": f"mkdir -p {_quote(path)}"},
                compensation=f"rmdir {_quote(path)}",
            )
        )
    return steps


def build_steps(
    intent: str, slots: dict, config: PlannerConfig | None = None
) -> tuple[PlanStep, ...]:
    """Canonical steps for a classified intent and its extracted slots."""
    config = config or PlannerConfig()
    if intent == "create_folder":
        return tuple(folder_nodes([str(name) for name in slots.get("folder_names", [])]))

    if intent == "write_file":
        path = str(slots.get("path", ""))
        content = str(slots.get("content", ""))
        if not path or not content:
            return ()
        return (
            PlanStep(
                id=_unique(f"write-{_slug(path, 'file')}", set()),
                type="shell",
                params={"run": f"printf '%s\\n' {_quote(content)} > {_quote(path)}"},
                compensation=f"rm -f {_quote(path)}",
            ),
        )

    if intent == "create_workspace":
        name = str(slots.get("workspace_name", ""))
        if not name:
            return ()
        folders = [str(folder) for folder in slots.get("folder_names", [])] or list(
            config.default_folders
        )
        root_id = _unique(f"create-{_slug(name, 'workspace')}", set())
        steps: list[PlanStep] = [
            PlanStep(
                id=root_id,
                type="shell",
                params={"run": f"mkdir -p {_quote(name)}"},
                compensation=f"rmdir {_quote(name)}",
            )
        ]
        for step in folder_nodes(folders, prefix=f"{name}/"):
            steps.append(
                PlanStep(
                    id=step.id,
                    type=step.type,
                    params=step.params,
                    depends_on=(root_id,),
                    compensation=step.compensation,
                )
            )
        steps.append(
            PlanStep(
                id=_unique("write-readme", {step.id for step in steps}),
                type="shell",
                params={
                    "run": f"printf '%s\\n' {_quote(f'# {name}')} > {_quote(f'{name}/README.md')}"
                },
                depends_on=(root_id,),
                compensation=f"rm -f {_quote(f'{name}/README.md')}",
            )
        )
        return tuple(steps)

    if intent == "install_app":
        app = str(slots.get("app", ""))
        if not app:
            return ()
        approval_id = _unique("approve-install", set())
        return (
            PlanStep(id=approval_id, type="approval", params={"quorum": 1}),
            PlanStep(
                id=_unique(f"install-{_slug(app)}", {approval_id}),
                type="shell",
                params={"run": config.install_template.format(app=_quote(app))},
                depends_on=(approval_id,),
            ),
        )

    if intent == "run_command":
        command = str(slots.get("command", ""))
        if not command:
            return ()
        return (PlanStep(id=_unique("run-command", set()), type="shell", params={"run": command}),)

    if intent == "system_info":
        metric = str(slots.get("metric", ""))
        command = METRIC_COMMANDS.get(metric, "")
        if not command:
            return ()
        return (
            PlanStep(
                id=_unique(f"report-{_slug(metric)}", set()),
                type="shell",
                params={"run": command},
            ),
        )

    if intent == "list_directory":
        path = str(slots.get("path", ""))
        if not path:
            return ()
        return (
            PlanStep(
                id=_unique("list-directory", set()),
                type="shell",
                params={"run": f"ls -la {_quote(path)}"},
            ),
        )
    return ()


class OfflinePlanner:
    """Deterministic planner: rules + slots + canonical steps (FR-06).

    No network, no clock, no randomness: the same request always yields the
    same plan, and anything the planner is not sure about comes back as a
    clarification question instead of a guessed command.
    """

    def __init__(self, config: PlannerConfig | None = None) -> None:
        self.config = config or PlannerConfig()

    def plan(self, text: str) -> Plan:
        """Typed plan for ``text`` — never raises, never guesses silently.

        Below the configured confidence threshold, with a required slot
        missing, or when no rule matched, the plan carries a clarification
        question (or the capability text) and is *not* executable: a caller
        can only act on an explicit, validated plan (FR-06, item [59]).
        """
        raw = "" if text is None else str(text)
        match = classify(raw)
        if match is None:
            return Plan(
                intent="unknown",
                confidence=0.0,
                raw_input=raw,
                needs_clarification=True,
                clarification=UNSUPPORTED_QUESTION,
                message=capability_text(),
                planner="offline",
            )
        if match.intent == "help":
            return Plan(
                intent="help",
                confidence=match.confidence,
                raw_input=raw,
                message=capability_text(),
                planner="offline",
            )

        slots = extract_slots(match.intent, raw)
        missing = missing_slots(match.intent, slots)
        if missing:
            return Plan(
                intent=match.intent,
                slots=slots,
                confidence=match.confidence,
                raw_input=raw,
                needs_clarification=True,
                clarification=clarification_for(match.intent, missing[0]),
                planner="offline",
            )

        steps = build_steps(match.intent, slots, self.config)
        if not steps and match.intent in EXECUTABLE_INTENTS:
            required = spec_for(match.intent).required_slots
            return Plan(
                intent=match.intent,
                slots=slots,
                confidence=match.confidence,
                raw_input=raw,
                needs_clarification=True,
                clarification=clarification_for(match.intent, required[0] if required else "path"),
                planner="offline",
            )
        if len(steps) > self.config.max_steps:
            return Plan(
                intent=match.intent,
                slots=slots,
                confidence=match.confidence,
                raw_input=raw,
                needs_clarification=True,
                clarification=(
                    f"That plan needs {len(steps)} steps; please split it into"
                    f" smaller requests (limit {self.config.max_steps})."
                ),
                planner="offline",
            )
        if match.confidence < self.config.confidence_threshold:
            return Plan(
                intent=match.intent,
                slots=slots,
                steps=steps,
                confidence=match.confidence,
                raw_input=raw,
                needs_clarification=True,
                clarification=(
                    "I am not confident I understood that"
                    f" (guess: {match.intent}). Could you rephrase it?"
                ),
                planner="offline",
            )
        return Plan(
            intent=match.intent,
            slots=slots,
            steps=steps,
            confidence=match.confidence,
            raw_input=raw,
            planner="offline",
        )


def plan_intent(text: str) -> str:
    """The intent name for ``text`` (``unknown`` when no rule matched)."""
    match = classify(text)
    return match.intent if match else "unknown"


def intent_requires_approval(intent: str) -> bool:
    """True when the intent's compiled DAG must start with an approval node."""
    return spec_for(intent).requires_approval
