"""Phase 1-2 engine package: DSL + validator + executor.

Own grammar, validation and compilation live here. Adapted only from
source_MIT/llm-flow-engine `dsl_loader.py` (no direct import); the
validator core (Kahn cycle check) mirrors the shape of
source_MIT/flow-forge `backend/main.py` `/pipelines/parse` without copying;
the scheduler mirrors the shape of source_MIT/optiflow
`execution_service.py` (ready-set, parallel, retry, timeout) without the
unsafe `exec()` branch, profiler or AI-suggest.
"""

__all__ = ["dsl", "validator", "executor"]
