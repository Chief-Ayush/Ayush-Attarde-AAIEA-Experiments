"""Role-based access control.  The role and team are always looked up on the server from users.json - a caller cannot claim either."""
from __future__ import annotations

from . import store
from .config import PERMISSIONS, load_users
from .errors import SecurityError


def role_of(user_id: str) -> str:
    u = load_users().get(user_id or "")
    return u["role"] if u else ""


def can(user_id: str, permission: str) -> bool:
    return permission in PERMISSIONS.get(role_of(user_id), set())


def authorize(user_id: str, permission: str, *, ticket: dict | None = None, action: str = "", trace_id: str = "") -> dict:
    """Return {user_id, name, role, team}; otherwise write an access_denied audit record and raise SecurityError.
    Agents may act only on tickets of their own team; leads and admins on any ticket."""
    tid = ticket["ticket_id"] if ticket else ""
    qid = ticket["query_id"] if ticket else ""
    u = load_users().get(user_id or "")
    if not u:
        store.add_audit("access_denied", qid, tid, actor=user_id or "(none)", trace_id=trace_id, error_details=f"unknown user attempted '{action or permission}'")
        raise SecurityError(f"Unknown user '{user_id}' - action '{action or permission}' refused")
    if permission not in PERMISSIONS.get(u["role"], set()):
        store.add_audit("access_denied", qid, tid, actor=user_id, trace_id=trace_id, error_details=f"role {u['role']} may not {action or permission}")
        raise SecurityError(f"{u['name']} ({u['role']}) is not allowed to {action or permission}")
    if ticket and u["role"] == "agent" and permission in ("review", "dispatch") and ticket.get("assigned_team") != u.get("team"):
        store.add_audit("access_denied", qid, tid, actor=user_id, trace_id=trace_id, error_details=f"agent of {u.get('team')} may not {action or permission} on a {ticket.get('assigned_team')} ticket")
        raise SecurityError(f"{u['name']} works in {u.get('team')} and cannot act on a {ticket.get('assigned_team')} ticket")
    return {"user_id": user_id, "name": u["name"], "role": u["role"], "team": u.get("team", "")}
