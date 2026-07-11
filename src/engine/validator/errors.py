"""Validator error type: precise message + line/col (never partial deploy)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ValidationError:
    """One validator finding; `code` is the stage: schema/type/edge/cycle/policy."""

    code: str
    message: str
    line: int = 0
    col: int = 0

    def __str__(self) -> str:
        loc = f"{self.line}:{self.col}" if self.line else "0:0"
        return f"[{self.code}] {loc} {self.message}"
