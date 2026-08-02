"""Phase 6 item [58]: deterministic slot extraction and canonical values.

Each intent has one extractor. Extractors read the case-preserving
:func:`~orchestration.normalize.prepare` form (a file's content or a folder
name keeps the user's spelling) and fall back to the lowercased matching form
only where the vocabulary is closed (metrics, app names).

Extraction is total: an extractor returns the slots it found and simply
omits what it could not determine, so the planner (item [59]) can ask for the
missing pieces instead of inventing a value.
"""

from __future__ import annotations

import re
from typing import Any

from .intents import SYSTEM_METRICS
from .normalize import normalize, normalize_path, prepare, strip_filler

_FOLDER_KEYWORD = re.compile(r"\b(folders|folder|directories|directory|dir)\b", re.IGNORECASE)
_MKDIR = re.compile(r"^\s*mkdir\s+(?:-\w+\s+)*([^\s].*)$", re.IGNORECASE)
_CONNECTORS = {"and", "or", "&", ",", "called", "named", "plus", "with"}
_COUNTS = {
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "a",
    "an",
}
_STOPWORDS = {"the", "with", "containing", "in", "into", "to", "for", "readme", "please"}
_SAFE_NAME = re.compile(r"[\w.@+-]+")
_EXTENSION_FILE = re.compile(r"([\w./~-]+\.[A-Za-z0-9]{1,6})\b")
_QUOTED = re.compile(r"[\"']([^\"']+)[\"']")
_CONTENT_MARKERS: tuple[str, ...] = (
    "with the text",
    "with the title",
    "with the content",
    "with content",
    "the content",
    "containing",
    "the message",
    "the text",
)
_TRAILING_TARGET = re.compile(r"\s+(?:to|into|in|as)\s+[\w./~-]+\s*$", re.IGNORECASE)


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return len(value) > 0
    return True


def _name_list(remainder: str) -> list[str]:
    cleaned = re.sub(r"^[\s:,\-]+", "", remainder)
    names: list[str] = []
    for token in re.split(r"[,\s]+", cleaned):
        candidate = token.strip().strip("\"'")
        if not candidate:
            continue
        lowered = candidate.lower()
        if lowered in _COUNTS or lowered in _CONNECTORS:
            continue
        if lowered in _STOPWORDS or not _SAFE_NAME.fullmatch(candidate):
            break
        names.append(candidate)
    return names


def _folder_names(text: str) -> list[str]:
    """Folder names after `mkdir` or after the first folder keyword."""
    mkdir = _MKDIR.match(text)
    if mkdir:
        return _name_list(mkdir.group(1))
    keyword = _FOLDER_KEYWORD.search(text)
    if keyword is None:
        return []
    return _name_list(text[keyword.end() :])


def _workspace_name(text: str) -> str:
    patterns = (
        r"\bworkspace\s+(?:called|named)\s+([\w.@+-]+)",
        r"\bcalled\s+([\w.@+-]+)",
        r"\b(?:create|set up|scaffold|make)\s+(?:a\s+|the\s+|new\s+|team\s+|project\s+)*workspace\s+([\w.@+-]+)",
        r"\bthe\s+([\w.@+-]+)\s+workspace\b",
        r"\b(?:project|team)\s+workspace\s+([\w.@+-]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _write_file(text: str) -> dict[str, str]:
    extensions = _EXTENSION_FILE.findall(text)
    path = normalize_path(extensions[0]) if extensions else ""
    if not path and re.search(r"\bREADME\b", text, re.IGNORECASE):
        path = "README"
    return {"path": path, "content": _file_content(text, path)}


def _file_content(text: str, path: str) -> str:
    quoted = _QUOTED.search(text)
    if quoted:
        return quoted.group(1).strip()
    lowered = text.lower()
    for marker in _CONTENT_MARKERS:
        index = lowered.find(marker)
        if index < 0:
            continue
        remainder = text[index + len(marker) :].strip()
        remainder = _TRAILING_TARGET.sub("", remainder).strip()
        return remainder.strip(" .,;:\"'")
    inline = re.search(
        r"^(?:write|save|put|add|create|generate)\s+(.+?)\s+(?:into|to|in)\s+[\w./~-]+\s*$",
        text,
        re.IGNORECASE,
    )
    if inline:
        value = inline.group(1).strip(" .,;:\"'")
        value = re.sub(r"^(?:the\s+)?(?:file|text)\s+", "", value, flags=re.IGNORECASE)
        return value
    if path and re.search(r"\bREADME\b", path, re.IGNORECASE):
        return ""
    return ""


def _app_name(text: str) -> str:
    for pattern in (
        r"install(?:ed)?\s+(?:the\s+)?(?:app(?:lication)?\s+)?([a-z0-9][\w.+-]*)",
        r"get me\s+(?:the\s+)?(?:app\s+)?([a-z0-9][\w.+-]*)",
        r"i\s+(?:need|want|would like)\s+(?:the\s+)?(?:app\s+)?([a-z0-9][\w.+-]*)",
    ):
        for match in re.finditer(pattern, text):
            candidate = match.group(1)
            if candidate not in {"installed", "install", "please", "the", "app"}:
                return candidate
    return ""


def _command(text: str) -> str:
    stripped = strip_filler(prepare(text))
    verb = re.match(
        r"^(?:run|execute|exec)\b\s*(?:the\s+)?(?:command\b)?\s*[:\-]?\s*(.*)$",
        stripped,
        re.IGNORECASE,
    )
    command = verb.group(1) if verb else stripped
    return command.strip().strip("\"'")


def _metric(normalized: str) -> str:
    # Order matters: a ranking request ("processes ... cpu") is a process
    # metric, not a core count.
    for metric, pattern in (
        ("processes", r"\bprocess(?:es)?\b|\bps\b"),
        ("disk", r"\bdisk\b|\bspace\b|\bstorage\b|\bdf\b"),
        ("memory", r"\bmemory\b|\bram\b"),
        ("uptime", r"\buptime\b|\breboot\b|\bboot\b"),
        ("load", r"\bload\b"),
        ("cpu", r"\bcpus?\b|\bcores?\b|\bprocessors?\b"),
    ):
        if re.search(pattern, normalized):
            return metric
    return ""


def _directory(text: str) -> str:
    if re.search(r"\bworking directory\b", text, re.IGNORECASE):
        return "."
    patterns = (
        r"^(?:ls|ll|dir)\s+(?:-\w+\s+)*([~/][\w./~-]*|[\w./~-]+)\s*$",
        r"\b(?:in|of|inside)\s+([~/][\w./~-]*)\b",
        r"\b(?:in|of|inside)\s+the\s+([\w.@+-]+)\s+(?:folder|directory|dir)\b",
        r"\b(?:contents?\s+of|of)\s+([\w.@+-]+)\b",
        r"\b(?:folder|directory|dir)\s+([~/]?[\w./~-]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return normalize_path(match.group(1))
    return ""


def extract_slots(intent: str, text: str) -> dict[str, Any]:
    """Deterministic slots for one intent (missing values are simply absent)."""
    prepared = prepare(text)
    normalized = normalize(text)
    found: dict[str, Any] = {}
    if intent == "create_folder":
        found["folder_names"] = _folder_names(prepared)
    elif intent == "create_workspace":
        found["workspace_name"] = _workspace_name(prepared)
        found["folder_names"] = _folder_names(prepared)
    elif intent == "write_file":
        found.update(_write_file(prepared))
    elif intent == "install_app":
        found["app"] = _app_name(normalized)
    elif intent == "run_command":
        found["command"] = _command(prepared)
    elif intent == "system_info":
        found["metric"] = _metric(normalized)
    elif intent == "list_directory":
        found["path"] = _directory(prepared)
    return {key: value for key, value in found.items() if _present(value)}


def is_metric(metric: str) -> bool:
    """True when ``metric`` is in the frozen metric vocabulary."""
    return metric in SYSTEM_METRICS
