"""Phase 8 item [77]: the HTTP client the client screens use.

One small stdlib client (``urllib``) with a bearer token, so the Tkinter
client talks to the API exactly like any other client and holds no business
rules of its own: validation, policy, approvals and audit all stay server
side.

Endpoints that answer with a *report* and a non-200 status (validation and
dry-run use 422 for an invalid document) still return their JSON body, so the
UI can render the findings instead of treating them as a transport failure.
"""

from __future__ import annotations

import json
from typing import Any
from urllib import error, parse, request

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT = 10.0

#: Statuses whose JSON body is still a useful report for these screens.
REPORT_STATUSES = frozenset({200, 422})


class ApiError(RuntimeError):
    """A request failed at the transport or API level."""

    def __init__(self, message: str, *, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class WorkbenchClient:
    """Thin typed wrapper over the endpoints the workbench needs."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        token: str = "",
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    # -- transport --------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        report: bool = False,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + parse.urlencode({k: v for k, v in params.items() if v != ""})
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=self.timeout) as response:  # nosec B310
                return _json(response.read())
        except error.HTTPError as exc:
            payload = _json(exc.read())
            if report and exc.code in REPORT_STATUSES:
                return payload
            detail = payload.get("detail") if isinstance(payload, dict) else ""
            raise ApiError(
                f"{method} {path} failed ({exc.code}): {detail or exc.reason}",
                status=exc.code,
            ) from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise ApiError(f"cannot reach {self.base_url}: {exc}") from exc

    # -- auth -------------------------------------------------------------
    def login(self, username: str, password: str) -> str:
        """Exchange credentials for a token and keep it for later calls."""
        payload = self._request(
            "POST", "/auth/login", body={"username": username, "password": password}
        )
        self.token = str(payload.get("access_token", ""))
        return self.token

    # -- workflow ---------------------------------------------------------
    def validate(self, dsl: str, dsl_type: str = "yaml") -> dict[str, Any]:
        return self._request(
            "POST", "/workflows/validate", body={"dsl": dsl, "dsl_type": dsl_type}, report=True
        )

    def dry_run(
        self, dsl: str, *, dsl_type: str = "yaml", workspace_root: str = ""
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/workflows/dry-run",
            body={"dsl": dsl, "dsl_type": dsl_type, "workspace_root": workspace_root},
            report=True,
        )

    def save(self, dsl: str, dsl_type: str = "yaml") -> dict[str, Any]:
        """Publish a validated workflow as one immutable version (item [104])."""
        return self._request(
            "POST", "/workflows", body={"dsl": dsl, "dsl_type": dsl_type}, report=True
        )

    def explain(self, node: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/policy/explain", body=node)

    # -- runs / audit -----------------------------------------------------
    def run(self, workflow_id: str, version: int, *, trigger: str = "manual") -> dict[str, Any]:
        """Start one persisted version; answers at once with the RUNNING id."""
        return self._request(
            "POST",
            "/executions",
            body={"workflow_id": workflow_id, "version": version, "trigger": trigger},
        )

    def execution(self, execution_id: str) -> dict[str, Any]:
        return self._request("GET", f"/executions/{parse.quote(execution_id)}")

    def replay(self, execution_id: str) -> dict[str, Any]:
        return self._request("POST", f"/executions/{parse.quote(execution_id)}/replay")

    def executions(self, limit: int = 50) -> dict[str, Any]:
        return self._request("GET", "/executions", params={"limit": limit})

    def timeline(self, execution_id: str) -> dict[str, Any]:
        return self._request("GET", f"/executions/{parse.quote(execution_id)}/timeline")

    def audit(self, execution_id: str, *, limit: int = 100) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/executions/{parse.quote(execution_id)}/audit",
            params={"limit": limit},
        )

    def verify(self, execution_id: str) -> dict[str, Any]:
        return self._request("GET", f"/executions/{parse.quote(execution_id)}/audit/verify")

    # -- approvals --------------------------------------------------------
    def approvals(self, execution_id: str = "") -> dict[str, Any]:
        return self._request("GET", "/approvals", params={"execution_id": execution_id})

    def decide(
        self,
        execution_id: str,
        node_id: str,
        verdict: str,
        *,
        reason: str = "",
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/approvals/{parse.quote(execution_id)}/{parse.quote(node_id)}/decisions",
            body={"verdict": verdict, "reason": reason},
        )


def _json(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ApiError(f"response is not JSON: {exc}") from exc
    return value if isinstance(value, dict) else {"value": value}
