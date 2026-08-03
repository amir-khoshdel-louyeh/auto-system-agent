"""Phase 7 item [65]: PII/secret scrubbing before anything is persisted.

The scrubber is deterministic and closed: a frozen rule table replaces
personal data and credentials with stable placeholders, so the *shape* of a
plan survives (it stays executable and comparable) while the identity and
the secrets do not. `scrub_plan` walks a canonical plan and scrubs every
string, and the caller refuses to persist anything that still looks like PII
(:func:`contains_pii`).

Scrubbing is defence in depth, not a licence to store raw data: the memory
store (item [70]) only ever writes the scrubbed form.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: `(name, pattern, replacement)` — order matters (secrets before numbers).
RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[email]"),
    ("bearer_token", re.compile(r"\b(?:bearer|token)\s+[A-Za-z0-9._~+/-]{12,}=*", re.I), "[token]"),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"), "[api_key]"),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"), "[token]"),
    (
        "aws_key",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "[api_key]",
    ),  # Covers `password=hunter2`, `password: hunter2` and CLI `--password hunter2`.
    # Deliberately over-redacts (`token expires` becomes `[secret]`): losing a
    # word of a shared plan is cheaper than leaking a credential.
    (
        "secret_assignment",
        re.compile(
            r"(?:--)?(?<![A-Za-z0-9])(?:password|passwd|secret|token|api[_-]?key)\b"
            r"\s*(?:[=:]\s*|\s+)[^\s\"']+",
            re.I,
        ),
        "[secret]",
    ),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "[private_key]"),
    ("home_path", re.compile(r"(?:/home/|/Users/)[\w.-]+"), "/home/[user]"),
    ("ipv4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[ip]"),
    ("ipv6", re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){3,7}[0-9a-fA-F]{1,4}\b"), "[ip]"),
    (
        "phone",
        re.compile(
            r"(?<![\w.])(?:\+\d{1,3}[\s-]?)?(?:\(\d{2,4}\)[\s-]?)?\d{3,4}[\s-]?\d{3,4}(?![\w.])"
        ),
        "[phone]",
    ),
    ("credit_card", re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[card]"),
)


@dataclass(frozen=True)
class Finding:
    """One rule hit: which rule, how many times, and where it first matched."""

    rule: str
    count: int
    first_index: int
    placeholder: str


@dataclass(frozen=True)
class ScrubResult:
    """Scrubbed text plus what was removed (for audit, never the value)."""

    text: str
    findings: tuple[Finding, ...] = ()

    @property
    def count(self) -> int:
        return sum(finding.count for finding in self.findings)

    @property
    def clean(self) -> bool:
        return not self.findings

    def rules_hit(self) -> tuple[str, ...]:
        return tuple(finding.rule for finding in self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "rules": [
                {
                    "rule": finding.rule,
                    "count": finding.count,
                    "placeholder": finding.placeholder,
                }
                for finding in self.findings
            ],
        }


def scrub(text: str) -> ScrubResult:
    """Replace every PII/secret match in ``text`` with a placeholder."""
    value = "" if text is None else str(text)
    findings: list[Finding] = []
    for name, pattern, placeholder in RULES:
        matches = list(pattern.finditer(value))
        if not matches:
            continue
        findings.append(
            Finding(
                rule=name,
                count=len(matches),
                first_index=matches[0].start(),
                placeholder=placeholder,
            )
        )
        value = pattern.sub(placeholder, value)
    findings.sort(key=lambda finding: finding.first_index)
    return ScrubResult(text=value, findings=tuple(findings))


def scrub_value(value: Any) -> tuple[Any, ScrubResult]:
    """Recursively scrub strings inside dicts/lists (returns scrubbed copy)."""
    if isinstance(value, str):
        result = scrub(value)
        return result.text, result
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        findings: list[Finding] = []
        for key, item in value.items():
            scrubbed, result = scrub_value(item)
            out[key] = scrubbed
            findings.extend(result.findings)
        return out, ScrubResult(text="", findings=tuple(findings))
    if isinstance(value, list):
        items: list[Any] = []
        nested: list[Finding] = []
        for item in value:
            scrubbed, result = scrub_value(item)
            items.append(scrubbed)
            nested.extend(result.findings)
        return items, ScrubResult(text="", findings=tuple(nested))
    return value, ScrubResult(text="")


def scrub_plan(plan: Any) -> tuple[Any, ScrubResult]:
    """Scrub a canonical plan (any JSON shape) before it is fingerprinted."""
    return scrub_value(plan)


def contains_pii(text: str) -> bool:
    """True when any frozen rule still matches (the store refuses to persist)."""
    return any(pattern.search(str(text or "")) for _, pattern, _ in RULES)
