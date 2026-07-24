"""Phase 1 item [12]: RBAC roles and the permission matrix (FR-02, §5).

Four frozen MVP roles:

- ``OWNER``    - manage workspace, members and workflow versions;
- ``OPERATOR`` - create and run permitted workflows;
- ``APPROVER`` - decide approval requests;
- ``VIEWER``   - read runs and audit records.

The matrix is project-owned policy: authorization checks call
:func:`has_permission` at both the API layer (route dependencies, item [13])
and the service layer, so knowing an identifier is never enough (FR-02).
Roles are plain ``str`` enums so they embed directly in JWT claims and
database columns without adapters.
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    OWNER = "OWNER"
    OPERATOR = "OPERATOR"
    APPROVER = "APPROVER"
    VIEWER = "VIEWER"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class Permission(str, Enum):
    WORKSPACE_MANAGE = "workspace:manage"
    MEMBER_MANAGE = "member:manage"
    WORKFLOW_EDIT = "workflow:edit"
    WORKFLOW_RUN = "workflow:run"
    APPROVAL_DECIDE = "approval:decide"
    RUN_READ = "run:read"
    AUDIT_READ = "audit:read"


#: Frozen permission matrix (proposal §5). Everything not listed is denied.
PERMISSION_MATRIX: dict[Permission, frozenset[Role]] = {
    Permission.WORKSPACE_MANAGE: frozenset({Role.OWNER}),
    Permission.MEMBER_MANAGE: frozenset({Role.OWNER}),
    Permission.WORKFLOW_EDIT: frozenset({Role.OWNER}),
    Permission.WORKFLOW_RUN: frozenset({Role.OWNER, Role.OPERATOR}),
    Permission.APPROVAL_DECIDE: frozenset({Role.OWNER, Role.APPROVER}),
    Permission.RUN_READ: frozenset({Role.OWNER, Role.OPERATOR, Role.APPROVER, Role.VIEWER}),
    Permission.AUDIT_READ: frozenset({Role.OWNER, Role.OPERATOR, Role.APPROVER, Role.VIEWER}),
}

ALL_ROLES: tuple[Role, ...] = (Role.OWNER, Role.OPERATOR, Role.APPROVER, Role.VIEWER)


def parse_role(value: object) -> Role:
    """Convert a claim/DB/env value to a Role; unknown values are rejected.

    Fails closed: a garbage role string never becomes a privileged role
    and never defaults to OWNER.
    """
    if isinstance(value, Role):
        return value
    if isinstance(value, str):
        try:
            return Role(value.strip().upper())
        except ValueError:
            pass
    raise ValueError(f"unknown role: {value!r}")


def has_permission(role: object, permission: Permission) -> bool:
    """True when ``role`` is granted ``permission`` (unknown role => False)."""
    try:
        parsed = parse_role(role)
    except ValueError:
        return False
    return parsed in PERMISSION_MATRIX[permission]


def permissions_for(role: object) -> frozenset[Permission]:
    """Every permission granted to ``role`` (empty for unknown roles)."""
    try:
        parsed = parse_role(role)
    except ValueError:
        return frozenset()
    return frozenset(perm for perm, allowed in PERMISSION_MATRIX.items() if parsed in allowed)
