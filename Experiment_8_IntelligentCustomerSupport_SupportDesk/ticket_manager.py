"""Ticket creation and lifecycle rules (lab step 9 and 10).  Pure functions: the database layer applies them."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import workflow_router as wr

ACTIVE = {"OPEN", "PENDING_APPROVAL", "IN_REVIEW", "APPROVED_FOR_RESPONSE", "ESCALATED", "ON_HOLD", "AWAITING_CUSTOMER", "REOPENED"}
PAUSE_STATES = {"AWAITING_CUSTOMER", "ON_HOLD"}
ALLOWED_ACTIONS = {"APPROVE", "EDIT", "HOLD", "REQUEST_INFORMATION", "ESCALATE", "RESOLVE"}
ACTION_TARGET = {"APPROVE": "APPROVED_FOR_RESPONSE", "EDIT": "IN_REVIEW", "HOLD": "ON_HOLD", "REQUEST_INFORMATION": "AWAITING_CUSTOMER", "ESCALATE": "ESCALATED", "RESOLVE": "RESOLVED"}

TRANSITIONS = {
    "OPEN": {"PENDING_APPROVAL", "IN_REVIEW", "ESCALATED", "AWAITING_CUSTOMER", "ON_HOLD", "RESOLVED", "CLOSED"},
    "PENDING_APPROVAL": {"APPROVED_FOR_RESPONSE", "IN_REVIEW", "ESCALATED", "ON_HOLD", "AWAITING_CUSTOMER"},
    "IN_REVIEW": {"IN_REVIEW", "APPROVED_FOR_RESPONSE", "ESCALATED", "ON_HOLD", "AWAITING_CUSTOMER", "RESOLVED"},
    "APPROVED_FOR_RESPONSE": {"AWAITING_CUSTOMER", "RESOLVED", "ESCALATED", "IN_REVIEW"},
    "ESCALATED": {"ESCALATED", "IN_REVIEW", "APPROVED_FOR_RESPONSE", "ON_HOLD", "AWAITING_CUSTOMER", "RESOLVED"},
    "ON_HOLD": {"IN_REVIEW", "ESCALATED", "AWAITING_CUSTOMER", "RESOLVED"},
    "AWAITING_CUSTOMER": {"IN_REVIEW", "RESOLVED", "REOPENED", "ESCALATED", "CLOSED"},
    "RESOLVED": {"CLOSED", "REOPENED"},
    "CLOSED": {"REOPENED"},
    "REOPENED": {"IN_REVIEW", "ESCALATED", "ON_HOLD", "AWAITING_CUSTOMER", "RESOLVED"},
}
REOPEN_WINDOW_DAYS = 7
PRIORITY_ORDER = ["P1", "P2", "P3", "P4"]


def can_transition(src: str, dst: str) -> bool:
    return dst in TRANSITIONS.get(src, set())


def check_action(status: str, action: str) -> str:
    """Return the target status for a review action or raise ValueError (a closed ticket cannot silently return to an active state)."""
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"Invalid review action '{action}'")
    target = ACTION_TARGET[action]
    if not can_transition(status, target):
        raise ValueError(f"A ticket in state {status} cannot move to {target} with {action}")
    return target


def initial_status(route: str, auto_dispatch: bool) -> str:
    return {wr.URGENT: "ESCALATED", wr.TECH: "IN_REVIEW", wr.NO_ARTICLE: "IN_REVIEW", wr.REVIEW: "IN_REVIEW", wr.AUTO: "PENDING_APPROVAL",
            wr.FEEDBACK: "OPEN", wr.INFO: "AWAITING_CUSTOMER"}.get(route, "OPEN")


def new_ticket_id() -> str:
    return "TKT-" + uuid.uuid4().hex[:8].upper()


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def create_ticket(query: dict, classification: dict, route: str, article: dict | None, score: float, policy: dict, now: datetime, auto_dispatch: bool = True) -> dict:
    pri = classification["priority"]
    team = wr.assign_team(classification, route)
    return {"ticket_id": new_ticket_id(), "query_id": query["query_id"], "customer_id": query["customer_id"], "intent": classification["intent"],
            "complexity": classification["complexity"], "priority": pri, "base_priority": pri, "route": route, "status": initial_status(route, auto_dispatch),
            "assigned_team": team, "article_id": article["article_id"] if article else None, "match_score": score,
            "proposed_response": article["response"] if article and route in (wr.AUTO, wr.REVIEW, wr.TECH, wr.NO_ARTICLE, wr.FEEDBACK, wr.URGENT) else "",
            "created_at": iso(now), "updated_at": iso(now),
            "first_response_due": iso(now + timedelta(minutes=policy["sla_minutes"][pri])), "resolution_due": iso(now + timedelta(minutes=policy["resolution_minutes"][pri])),
            "resolution_summary": None}


def raise_priority(priority: str) -> str:
    i = PRIORITY_ORDER.index(priority)
    return PRIORITY_ORDER[max(0, i - 1)]


def reopen_priority(priority: str) -> str:
    """Reopening raises priority one level, but never above P2 (P1 is reserved for incidents)."""
    return priority if PRIORITY_ORDER.index(priority) <= 1 else raise_priority(priority)
