"""Phase 6 item [57]: deterministic normalization for the offline planner.

Normalization is deliberately boring and total: unicode NFKC, straight
quotes, collapsed whitespace, a lowercased matching form and removal of
leading politeness. Everything is a pure function of the input string, so
the same request always classifies the same way (FR-06) and no network,
clock or random source is involved.

Two forms are produced:

- :func:`prepare` — cleaned but case-preserving, used for slot *values*
  (a file's content or a folder name must keep its original spelling);
- :func:`normalize` — the lowercased matching form used by the intent rules.
"""

from __future__ import annotations

import re
import unicodedata

#: Leading politeness removed before intent matching (high precision only).
FILLER_PREFIXES: tuple[str, ...] = (
    "hi",
    "hey",
    "hello",
    "please",
    "kindly",
    "can you",
    "could you",
    "would you",
    "will you",
    "i would like to",
    "i'd like to",
    "i want to",
    "i need you to",
    "for me",
)

_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "`": "'"})
_WHITESPACE = re.compile(r"\s+")
_TRAILING = re.compile(r"[\s.,;:!?\"']+$")


def prepare(text: str) -> str:
    """Case-preserving cleanup: NFKC, straight quotes, single spaces."""
    folded = unicodedata.normalize("NFKC", "" if text is None else str(text))
    folded = folded.translate(_QUOTES)
    return _WHITESPACE.sub(" ", folded).strip()


def strip_filler(text: str) -> str:
    """Remove leading politeness words from an already prepared string."""
    current = text
    while True:
        lowered = current.lower()
        for prefix in FILLER_PREFIXES:
            if lowered == prefix:
                return ""
            if lowered.startswith(prefix + " "):
                current = current[len(prefix) :].lstrip(" ,")
                break
            if lowered.startswith(prefix + ","):
                current = current[len(prefix) + 1 :].lstrip(" ,")
                break
        else:
            return current


def normalize(text: str) -> str:
    """Lowercased matching form: prepared, filler-free, punctuation-trimmed."""
    return _TRAILING.sub("", strip_filler(prepare(text)).lower()).strip()


def tokens(text: str) -> list[str]:
    """Whitespace tokens of the prepared text."""
    return prepare(text).split()


def normalize_path(value: str) -> str:
    """Trim quotes and trailing separators from a path-like slot value."""
    cleaned = prepare(value).strip("\"'")
    cleaned = cleaned.rstrip("/") or cleaned
    return cleaned
