"""MCP server: notifications. Drafts, approvals, simulated delivery with retry (exponential backoff), idempotency keys and a dead-letter queue."""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

import notifications as nt
import resolver
import ticket_manager as tm
import workflow_router as wr

from ... import rbac, store
from ...config import PATHS, load_policy
from ...errors import InputError
from .common import DESTRUCTIVE, READ_ONLY, SENDS, WRITES_LOCAL, risk_meta, run_blocking

mcp = FastMCP("supportdesk-comms", instructions="Creates notification drafts and sends them through a simulated gateway. Customer answers need approval; delivery is retried safely and never duplicated.", log_level="WARNING")

AUTO_INTENTS = {"Login / Access", "Billing", "General Information"}
SYSTEM_ACTOR = "system:auto-policy"


def auto_policy_ok(t: dict, policy: dict) -> tuple[bool, str]:
    """The documented policy under which a knowledge-base answer may be sent without a human (checked here, on the server)."""
    try:
        art = {a["article_id"]: a for a in resolver.load_knowledge_base(PATHS.kb_json)}.get(t.get("article_id") or "")
    except resolver.KnowledgeBaseError:
        return False, "knowledge base unavailable"
    checks = [(t["route"] == wr.AUTO, "route is not PROPOSE_AUTO_RESOLUTION"), (t["status"] == "PENDING_APPROVAL", "ticket is not pending approval"),
              (t["complexity"] == "Low", "complexity is not Low"), (t["priority"] in ("P3", "P4"), "priority is not P3/P4"),
              (float(t["match_score"] or 0) >= policy["auto_dispatch_min_score"], "match score below the auto-dispatch minimum"),
              (t["intent"] in AUTO_INTENTS, "intent is not on the auto-dispatch list"), (bool(art and art.get("approved")), "article missing or not approved"),
              (not (t.get("flags") and '"injection": true' in t["flags"]), "query text was flagged")]
    for ok, why in checks:
        if not ok:
            return False, why
    return True, "policy satisfied"


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("MEDIUM"))
async def prepare_notifications(ticket_ids: list[str], actor: str, auto_dispatch: bool = True, trace_id: str = "") -> dict[str, Any]:
    """Create the notification drafts each new ticket needs: acknowledgement, team assignment, escalation, information request, and the proposed answer (auto-approved only under the documented policy)."""
    def work():
        rbac.authorize(actor, "run", action="prepare notifications", trace_id=trace_id)
        policy = load_policy()
        c = {"drafted": 0, "auto_approved": 0, "awaiting_approval": 0, "duplicates": 0, "policy_blocked": 0}
        for tid in ticket_ids:
            t = store.get_ticket(tid)
            if not t:
                raise InputError(f"Unknown ticket {tid}")
            plan = [("CREATED", {}), ("ASSIGNED", {})]
            if t["route"] in (wr.URGENT, wr.TECH):
                plan.append(("ESCALATED", {"reason": "urgent incident" if t["route"] == wr.URGENT else "complex technical issue"}))
            if t["route"] == wr.INFO:
                plan.append(("INFO_REQUESTED", {"approved_by": SYSTEM_ACTOR}))
            for ev, kw in plan:
                n = store.create_notification(t, ev, actor, policy, trace_id=trace_id, **kw)
                c["duplicates" if n.get("_duplicate") else "drafted"] += 1
            if t["route"] == wr.AUTO:
                ok, why = auto_policy_ok(t, policy) if auto_dispatch else (False, "auto-dispatch is switched off")
                n = store.create_notification(t, "RESPONSE", actor, policy, approved_by=SYSTEM_ACTOR if ok else None, response=t["proposed_response"], trace_id=trace_id)
                if n.get("_duplicate"):
                    c["duplicates"] += 1
                    continue
                c["drafted"] += 1
                if ok:
                    store.set_status_system(tid, "APPROVED_FOR_RESPONSE", SYSTEM_ACTOR, "approved under the auto-dispatch policy", trace_id=trace_id)
                    store.add_audit("auto_approved", t["query_id"], tid, priority=t["priority"], route=t["route"], knowledge_article_id=t["article_id"], match_score=t["match_score"],
                                    ticket_status="APPROVED_FOR_RESPONSE", actor=SYSTEM_ACTOR, trace_id=trace_id, error_details=why)
                    c["auto_approved"] += 1
                else:
                    c["awaiting_approval"] += 1
                    c["policy_blocked"] += 1 if auto_dispatch else 0
        return c
    return await run_blocking("prepare_notifications", work)


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("MEDIUM"))
async def create_notification(ticket_id: str, event: str, actor: str, to_addr: str = "", sim_fail_first: int = 0, trace_id: str = "") -> dict[str, Any]:
    """Create one notification draft for a ticket event (needs 'dispatch' permission for that ticket's team). Used for manual notifications and delivery tests."""
    def work():
        t = store.get_ticket(ticket_id)
        if not t:
            raise InputError(f"Unknown ticket {ticket_id}")
        rbac.authorize(actor, "dispatch", ticket=t, action=f"create a {event} notification", trace_id=trace_id)
        if event not in nt.EVENTS:
            raise InputError(f"Unknown event {event}")
        n = store.create_notification(t, event, actor, load_policy(), sim_fail_first=sim_fail_first, trace_id=trace_id, **({"to_addr": to_addr} if to_addr else {}))
        if sim_fail_first and not n.get("_duplicate"):
            with store.tx() as con:
                con.execute("UPDATE notifications SET sim_fail_first=? WHERE notification_id=?", (sim_fail_first, n["notification_id"]))
        return {"notification_id": n["notification_id"], "state": n.get("state"), "duplicate": bool(n.get("_duplicate"))}
    return await run_blocking("create_notification", work)


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("HIGH"))
async def approve_notification(notification_id: str, actor: str, trace_id: str = "") -> dict[str, Any]:
    """Approve a customer-facing draft for sending. Needs 'dispatch' permission for the ticket's team."""
    def work():
        n = store.get_notification(notification_id)
        if not n:
            raise InputError(f"Unknown notification {notification_id}")
        t = store.get_ticket(n["ticket_id"])
        rbac.authorize(actor, "dispatch", ticket=t, action="approve a notification", trace_id=trace_id)
        store.approve_notification(notification_id, actor)
        store.add_audit("notification_approved", t["query_id"], t["ticket_id"], priority=t["priority"], ticket_status=t["status"], notification_status="APPROVED", actor=actor, trace_id=trace_id, error_details=n["event"])
        return {"notification_id": notification_id, "state": "APPROVED"}
    return await run_blocking("approve_notification", work)


def _after_sent(n: dict, t: dict, actor: str, trace_id: str) -> None:
    ev = n["event"]
    if ev in ("RESPONSE", "INFO_REQUESTED", "RESOLVED"):
        store.mark_first_response(t["ticket_id"])
    if ev == "RESPONSE" and t["status"] in ("APPROVED_FOR_RESPONSE",):
        store.set_status_system(t["ticket_id"], "AWAITING_CUSTOMER", actor, "answer sent; waiting for the customer's confirmation", trace_id=trace_id)


def _after_dead(n: dict, t: dict, actor: str, trace_id: str) -> None:
    if n["event"] in ("RESPONSE", "INFO_REQUESTED", "RESOLVED") and t["status"] == "APPROVED_FOR_RESPONSE":
        store.set_status_system(t["ticket_id"], "IN_REVIEW", actor, "delivery failed; agent attention needed", trace_id=trace_id)


@mcp.tool(annotations=SENDS, meta=risk_meta("HIGH"))
async def dispatch_pending(actor: str, ticket_ids: list[str] | None = None, batch_id: str = "", max_attempts: int = 3, backoff_s: float = 0.01, trace_id: str = "") -> dict[str, Any]:
    """Send drafts that are allowed to go: approved, or not requiring approval. Transient failures are retried with exponential backoff; permanent ones go to the dead-letter queue. Already-sent messages are never sent twice."""
    def work():
        rbac.authorize(actor, "run", action="dispatch notifications", trace_id=trace_id)
        if not 1 <= max_attempts <= 6:
            raise InputError("max_attempts must be between 1 and 6")
        c = {"sent": 0, "dead_letter": 0, "retries": 0, "blocked": 0, "already_sent": 0, "attempts": 0, "recovered": 0}
        tickets = ticket_ids or [t["ticket_id"] for t in store.list_tickets(batch_id=batch_id or None, limit=5000)]
        for tid in tickets:
            t = store.get_ticket(tid)
            for n in reversed(store.list_notifications(tid)):
                if n["state"] == "SENT":
                    c["already_sent"] += 1
                    continue
                if n["state"] in ("DEAD_LETTER", "SENDING"):
                    continue
                if n["needs_approval"] and not n["approved_by"]:
                    c["blocked"] += 1
                    continue
                if not store.claim_notification(n["notification_id"]):
                    c["already_sent"] += 1                       # another dispatcher took it first
                    continue
                res = nt.send_with_retry(n["to_addr"], max_attempts, backoff_s, int(n["sim_fail_first"] or 0))
                store.record_dispatch(n["notification_id"], res, actor, trace_id)
                c["attempts"] += len(res["attempts"])
                c["retries"] += len(res["attempts"]) - 1
                if res["status"] == "SENT":
                    c["sent"] += 1
                    c["recovered"] += len(res["attempts"]) > 1
                    _after_sent(n, store.get_ticket(tid), actor, trace_id)
                else:
                    c["dead_letter"] += 1
                    _after_dead(n, store.get_ticket(tid), actor, trace_id)
        return c
    return await run_blocking("dispatch_pending", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def list_notifications(ticket_id: str = "", state: str = "", limit: int = 300) -> dict[str, Any]:
    """Notifications with their state and attempt history."""
    def work():
        rows = store.list_notifications(ticket_id or None, state or None, limit)
        for r in rows:
            r["attempt_log"] = store.notification_attempts(r["notification_id"])
        return {"notifications": rows, "count": len(rows)}
    return await run_blocking("list_notifications", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def notification_stats(batch_id: str = "") -> dict[str, Any]:
    """Delivery statistics for the evaluation: requests, delivered, retries, permanent failures, drafts never authorised."""
    def work():
        rows = []
        for t in store.list_tickets(batch_id=batch_id or None, limit=20000):
            rows += store.list_notifications(t["ticket_id"], limit=500)
        authorised = [n for n in rows if not (n["needs_approval"] and not n["approved_by"])]
        sent = [n for n in authorised if n["state"] == "SENT"]
        return {"total": len(rows), "authorised": len(authorised), "delivered": len(sent), "dead_letter": sum(n["state"] == "DEAD_LETTER" for n in rows),
                "never_authorised": len(rows) - len(authorised), "retried": sum(1 for n in rows if int(n["attempts"] or 0) > 1), "attempts": sum(int(n["attempts"] or 0) for n in rows),
                "recovered": sum(1 for n in sent if int(n["attempts"] or 0) > 1)}
    return await run_blocking("notification_stats", work)


@mcp.tool(annotations=DESTRUCTIVE, meta=risk_meta("HIGH"))
async def requeue_dead_letter(notification_id: str, actor: str, to_addr: str = "", trace_id: str = "") -> dict[str, Any]:
    """Give a dead-lettered notification another chance (optionally with a corrected address). Needs 'reassign' permission (leads and admins)."""
    def work():
        n = store.get_notification(notification_id)
        if not n:
            raise InputError(f"Unknown notification {notification_id}")
        t = store.get_ticket(n["ticket_id"])
        rbac.authorize(actor, "reassign", ticket=t, action="requeue a failed notification", trace_id=trace_id)
        if n["state"] != "DEAD_LETTER":
            raise InputError("Only dead-lettered notifications can be requeued")
        with store.tx() as con:
            con.execute("UPDATE notifications SET state=?, to_addr=COALESCE(NULLIF(?,''), to_addr), sim_fail_first=0, updated_at=? WHERE notification_id=?",
                        ("APPROVED" if n["approved_by"] or not n["needs_approval"] else "DRAFT", to_addr, store.iso(store.now()), notification_id))
        store.add_audit("notification_requeued", t["query_id"], t["ticket_id"], priority=t["priority"], ticket_status=t["status"], notification_status="REQUEUED", actor=actor, trace_id=trace_id, error_details=n["event"])
        return {"notification_id": notification_id, "state": "REQUEUED"}
    return await run_blocking("requeue_dead_letter", work)


if __name__ == "__main__":
    mcp.run()
