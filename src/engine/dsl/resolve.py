"""Placeholder expansion `${a.b.c}` over plain data (adapted, stdlib-only).

Upstream `llm_flow_engine/utils.py::resolve_placeholders` only handles
`${word.word}` and logs via loguru. Ours accepts dotted paths of any depth
(e.g. `${model1_answer.output}` inside longer strings, as in the upstream
`demo_qa.yaml`), recurses into dict/list/tuple, and leaves unknown keys
untouched so the validator — not the loader — reports bad references.
"""

from __future__ import annotations

import re
from typing import Any

PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][\w]*(?:\.[\w]+)*)\}")


def list_placeholders(value: Any) -> list[str]:
    """Collect dotted placeholder paths (`a.b`) found in strings."""
    found: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, str):
            found.extend(PLACEHOLDER_RE.findall(item))
        elif isinstance(item, dict):
            for v in item.values():
                visit(v)
        elif isinstance(item, (list, tuple)):
            for v in item:
                visit(v)

    visit(value)
    return found


def _lookup(path: str, context: dict[str, Any]) -> tuple[bool, Any]:
    head, _, rest = path.partition(".")
    if head not in context:
        return (False, None)
    current: Any = context[head]
    if not rest:
        return (True, current)
    for part in rest.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif hasattr(current, part):
            current = getattr(current, part)
        else:
            return (False, None)
    return (True, current)


def resolve_placeholders(value: Any, context: dict[str, Any]) -> Any:
    """Return `value` with known `${path}` entries replaced from `context`."""
    if isinstance(value, str):
        matches = PLACEHOLDER_RE.findall(value)
        if not matches:
            return value
        if len(matches) == 1 and value.strip() == "${" + matches[0] + "}":
            ok, resolved = _lookup(matches[0], context)
            return resolved if ok else value
        out = value
        for path in matches:
            ok, resolved = _lookup(path, context)
            if ok:
                out = out.replace("${" + path + "}", str(resolved))
        return out
    if isinstance(value, dict):
        return {k: resolve_placeholders(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_placeholders(v, context) for v in value]
    if isinstance(value, tuple):
        return tuple(resolve_placeholders(v, context) for v in value)
    return value
