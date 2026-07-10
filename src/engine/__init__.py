"""Phase 1 engine package: DSL + validator + (phase 2) executor.

Own grammar, validation and compilation live here. Adapted only from
source_MIT/llm-flow-engine `dsl_loader.py` (no direct import); the
validator core (Kahn cycle check) mirrors the shape of
source_MIT/flow-forge `backend/main.py` `/pipelines/parse` without copying.
"""

__all__ = ["dsl", "validator"]
