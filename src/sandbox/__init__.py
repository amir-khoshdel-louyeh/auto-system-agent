"""Phase 8: sandbox contract, enforcement and the plugin manifest (FR-17).

`contract` freezes what a sandbox request and result look like, `runner`
executes one request with namespace + audit-hook isolation, and `manifest`
validates the plugin declaration before anything is registered.
"""

from .contract import (
    CONTRACT_VERSION,
    FILE_ACTIONS,
    KINDS,
    FileChange,
    SandboxError,
    SandboxRequest,
    SandboxResult,
)
from .runner import bwrap_available, run

__all__ = [
    "CONTRACT_VERSION",
    "FILE_ACTIONS",
    "KINDS",
    "FileChange",
    "SandboxError",
    "SandboxRequest",
    "SandboxResult",
    "bwrap_available",
    "run",
]
