"""Phase 2 item [25]: `POST /workflows/validate` (FR-04, gate for Phase 2).

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

router = APIRouter(prefix="/workflows", tags=["Workflows"])


class ValidateIn(BaseModel):
    dsl: str = Field(min_length=1, max_length=200_000)
    dsl_type: Literal["yaml", "json"] = "yaml"


def _error_out(stage: str, message: str, line: int, column: int) -> dict:
    return {"stage": stage, "message": message, "line": line, "column": column}


def _response(body: dict, *, valid: bool) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_200_OK if valid else 422, content=body)


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
