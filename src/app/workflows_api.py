"""Phase 2 item [25] / Phase 8 items [79], [104]: workflow authoring API.

`POST /workflows/validate` is the Phase 2 contract; `POST /workflows/dry-run`
adds the Phase 8 safe preview (same validation, plus every node's policy
verdict and explanation) without persisting or dispatching anything;
`POST /workflows` (item [104]) publishes the validated document as one
immutable `(workflow_id, version)` the run API can start.

Validates a DSL document without touching persistence: the report carries
`valid`, the parsed `name`/`version` and every finding as
`{stage, message, line, column}`. Invalid documents are *rejected* with
HTTP 422 (valid ones 200), and nothing is ever stored or deployed — the
persistence routes of Phase 3 call this same engine first.

Authentication: any authenticated role may validate (read-only check);
anonymous callers get 401.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from engine.dsl.loader import DSLParseError, load_text
from engine.validator.validator import validate

from .deps import CurrentUser, require_permission
from .roles import Permission
from .settings import Settings, get_settings

router = APIRouter(prefix="/workflows", tags=["Workflows"])

#: Frozen stage vocabulary (item [26]): every finding reports exactly one
#: of these, so clients can switch on `stage` without string guessing.
STAGES: tuple[str, ...] = (
    "yaml",  # DSL text is not parseable as YAML (parser line/col)
    "json",  # DSL text is not parseable as JSON (parser line/col)
    "schema",
    "type",
    "edge",
    "cycle",
    "policy",
)


class ValidateIn(BaseModel):
    dsl: str = Field(min_length=1, max_length=200_000)
    dsl_type: Literal["yaml", "json"] = "yaml"


class DryRunIn(ValidateIn):
    workspace_root: str = Field(default="", max_length=1024)


def _error_out(stage: str, message: str, line: int, column: int) -> dict:
    """One finding with the frozen shape; positions are always >= 1.

    The loader falls back to 0:0 when a parser cannot pinpoint the exact
    column; we normalise to 1:1 (document start) so clients can rely on
    ``line >= 1 and column >= 1`` for every entry.
    """
    return {
        "stage": stage,
        "message": str(message),
        "line": max(int(line), 1),
        "column": max(int(column), 1),
    }


def _response(body: dict, *, valid: bool) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_200_OK if valid else 422, content=body)


@router.post("")
def create_workflow(
    body: ValidateIn,
    user: CurrentUser = Depends(require_permission(Permission.WORKFLOW_EDIT)),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    """Validate and publish one immutable workflow version (item [104]).

    Same validation and the same frozen finding shape as `/validate`; an
    invalid document is a 422 and nothing is persisted, so a failed publish
    can never leave a partially-registered workflow behind.
    """
    from .db import init_db
    from .models import WorkflowStore

    try:
        workflow = load_text(body.dsl, body.dsl_type)
    except DSLParseError as exc:
        stage = "yaml" if body.dsl_type == "yaml" else "json"
        return _response(
            {
                "valid": False,
                "name": "",
                "version": 0,
                "errors": [_error_out(stage, str(exc), exc.line, exc.col)],
            },
            valid=False,
        )
    errors = [_error_out(err.code, err.message, err.line, err.col) for err in validate(workflow)]
    if errors:
        return _response(
            {"valid": False, "name": workflow.name, "version": workflow.version, "errors": errors},
            valid=False,
        )
    row = WorkflowStore(init_db(settings.sqlite_path)).publish(
        workflow, body.dsl, created_by=user.username
    )
    return _response(
        {
            "valid": True,
            "workflow_id": row["workflow_id"],
            "name": workflow.name,
            "version": int(row["version"]),
        },
        valid=True,
    )


@router.post("/dry-run")
def dry_run_workflow(
    body: DryRunIn,
    user: CurrentUser = Depends(require_permission(Permission.RUN_READ)),
) -> JSONResponse:
    """Validate and gate a workflow without running it (item [79])."""
    from safety.dryrun import dry_run

    report = dry_run(
        body.dsl,
        dsl_type=body.dsl_type,
        role=user.role.value,
        workspace_root=body.workspace_root,
    )
    return _response(report.as_dict(), valid=report.valid)


@router.post("/validate")
def validate_workflow(
    body: ValidateIn,
    _: CurrentUser = Depends(require_permission(Permission.RUN_READ)),
) -> JSONResponse:
    """Validate DSL text; 200 when valid, 422 with all findings otherwise."""
    try:
        workflow = load_text(body.dsl, body.dsl_type)
    except DSLParseError as exc:
        stage = "yaml" if body.dsl_type == "yaml" else "json"
        return _response(
            {
                "valid": False,
                "name": "",
                "version": 0,
                "errors": [_error_out(stage, str(exc), exc.line, exc.col)],
            },
            valid=False,
        )

    errors = [_error_out(err.code, err.message, err.line, err.col) for err in validate(workflow)]
    report = {
        "valid": not errors,
        "name": workflow.name,
        "version": workflow.version,
        "errors": errors,
    }
    return _response(report, valid=not errors)
