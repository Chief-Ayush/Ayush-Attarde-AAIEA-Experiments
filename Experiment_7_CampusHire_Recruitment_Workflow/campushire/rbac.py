"""Role-based access control (variation 9).  The role is always looked up on the server from users.json - a caller cannot claim one."""
from __future__ import annotations

from . import store
from .config import PERMISSIONS, load_users
from .errors import SecurityError


def role_of(user_id: str) -> str:
    u = load_users().get(user_id or "")
    return u["role"] if u else ""


def can(user_id: str, permission: str) -> bool:
    return permission in PERMISSIONS.get(role_of(user_id), set())


def authorize(user_id: str, permission: str, *, drive_id: str = "", candidate_id: str = "", action: str = "", trace_id: str = "") -> dict:
    """Return {user_id, name, role}; otherwise write an access_denied audit record and raise SecurityError."""
    u = load_users().get(user_id or "")
    if not u:
        store.add_audit("access_denied", drive_id, candidate_id, actor=user_id or "(none)", trace_id=trace_id,
                        error_details=f"unknown user attempted '{action or permission}'")
        raise SecurityError(f"Unknown user '{user_id}' - action '{action or permission}' refused")
    if permission not in PERMISSIONS.get(u["role"], set()):
        store.add_audit("access_denied", drive_id, candidate_id, actor=user_id, trace_id=trace_id,
                        error_details=f"role {u['role']} may not {action or permission}")
        raise SecurityError(f"{u['name']} ({u['role']}) is not allowed to {action or permission}")
    return {"user_id": user_id, "name": u["name"], "role": u["role"]}
