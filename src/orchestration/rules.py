"""Phase 6 item [57]: high-precision intent rules (offline first).

Rules are ordered and regex-based: the first match wins, so a specific
pattern (``mkdir``, an explicit ``run``/``execute`` verb, a workspace
request) is never shadowed by a generic one. Each rule carries a fixed
confidence — the planner's threshold (item [59]) decides whether the verdict
is trusted, clarified or handed to the optional LLM adapter.

The rules read the *normalized* form from :mod:`orchestration.normalize`
(lowercased, filler-free), which is why the patterns here are lowercase and
free of leading politeness.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .normalize import normalize


@dataclass(frozen=True)
class Rule:
    """One ordered intent rule with a fixed confidence."""

    name: str
    intent: str
    pattern: re.Pattern[str]
    confidence: float


@dataclass(frozen=True)
class IntentMatch:
    """The winning rule for one request."""

    intent: str
    confidence: float
    rule: str


def _rule(name: str, intent: str, pattern: str, confidence: float) -> Rule:
    return Rule(name=name, intent=intent, pattern=re.compile(pattern), confidence=confidence)


#: Ordered rules — the first match wins (specific before generic).
RULES: tuple[Rule, ...] = (
    _rule(
        "help-exact",
        "help",
        r"^(help|what can you do|show me the help|what commands do you support|"
        r"how do i use this|explain what you can do|list your capabilities|"
        r"what can i ask you|what can you help with)$",
        0.98,
    ),
    _rule("run-verb", "run_command", r"^(run|execute|exec)\b", 0.92),
    _rule(
        "workspace",
        "create_workspace",
        r"\b(workspaces?|project)\b.*\b(create|set up|scaffold|make|new)\b|"
        r"\b(create|set up|scaffold|make|new|need)\b.*\bworkspaces?\b",
        0.88,
    ),
    _rule(
        "install",
        "install_app",
        r"\b(install(?:ed|ing)?|get me)\b|\bi (?:need|want|would like)\b.*\binstalled\b",
        0.90,
    ),
    _rule("mkdir", "create_folder", r"^(mkdir|rmdir)\b", 0.95),
    _rule(
        "make-folder",
        "create_folder",
        r"\b(create|make|new|set up|i need|add)\b.*\b(folders?|director(y|ies)|dir)\b|"
        r"\b(folders?|director(y|ies))\b.*\b(called|named)\b",
        0.90,
    ),
    _rule(
        "write-file-with-extension",
        "write_file",
        r"\b[\w./-]+\.[a-z0-9]{1,6}\b.*\b(write|save|put|create|generate|add|containing)\b|"
        r"\b(write|save|put|create|generate|add)\b.*\b[\w./-]+\.[a-z0-9]{1,6}\b",
        0.90,
    ),
    _rule(
        "write-file-by-name",
        "write_file",
        r"\b(write|create|add|save|put)\b.*\b(readme|file|notes)\b",
        0.85,
    ),
    _rule("ls-verb", "list_directory", r"^(ls|ll|dir)\b", 0.92),
    _rule(
        "list-contents",
        "list_directory",
        r"\b(list|show|display|what files)\b.*\b(files?|contents?|director(y|ies)|folders?)\b",
        0.85,
    ),
    _rule(
        "system-metric",
        "system_info",
        r"\b(disk|space|storage|memory|ram|cpus?|cores?|processor|uptime|reboot|"
        r"load average|load|processes|process)\b",
        0.85,
    ),
)


def match_normalized(normalized: str) -> IntentMatch | None:
    """First matching rule for an already normalized request (None: no rule)."""
    for rule in RULES:
        if rule.pattern.search(normalized):
            return IntentMatch(intent=rule.intent, confidence=rule.confidence, rule=rule.name)
    return None


def classify(text: str) -> IntentMatch | None:
    """Normalize ``text`` and return the winning rule, or None."""
    return match_normalized(normalize(text))
