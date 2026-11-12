"""MCP server: tickets, agent actions, queues, SLA monitoring and the audit log."""
from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

import resolver
import sla_monitor as sla
import ticket_manager as tm
import workflow_router as wr

from ... import rbac, store
from ...config import PATHS, load_policy
from ...errors import InputError
from .common import READ_ONLY, WRITES_LOCAL, risk_meta, run_blocking

mcp = FastMCP("supportdesk-tickets", instructions="Creates and manages support tickets, enforces who may change them, tracks SLA deadlines and keeps the audit log.", log_level="WARNING")

TEAMS = set(wr.TEAM_BY_INTENT.values()) | {"Incident Response"}
SLIM = ("ticket_id", "query_id", "customer_id", "batch_id", "intent", "complexity", "priority", "base_priority", "route", "status", "assigned_team", "assignee", "article_id", "match_score",
        "confidence", "method", "created_at", "updated_at", "first_response_due", "resolution_due", "first_response_at", "resolved_at", "reopen_count", "aged", "query_text", "flags")


def _decorate(t: dict, at, policy) -> dict:
    out = {k: t.get(k) for k in SLIM}
    out["sla_state"] = sla.overall_state(t, at, policy)
    f = sla.sla_status(t, at, policy, "first_response")
    out["first_response_used_min"], out["first_response_window_min"] = f["used_min"], f["window_min"]
    out["route_class"] = wr.route_class(t["route"])
    out["has_response"] = bool(t.get("proposed_response") or t.get("final_response"))
    return out


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("MEDIUM"))
async def create_tickets(items: list[dict[str, Any]], batch_id: str, actor: str, trace_id: str = "") -> dict[str, Any]:
    """Create one ticket per query (idempotent). items: query_id, classification, route, article_id, match_score. SLA deadlines are computed from the service clock."""
    def work():
        rbac.authorize(actor, "run", action="create tickets", trace_id=trace_id)
        policy = load_policy()
        try:
            kb = {a["article_id"]: a for a in resolver.load_knowledge_base(PATHS.kb_json) if a.get("approved")}
        except resolver.KnowledgeBaseError as exc:
            raise InputError(str(exc)) from exc
        at = store.now()
        made = []
        for it in items:
            q = store.get_query(it["query_id"])
            if not q:
                raise InputError(f"Unknown query {it['query_id']}")
            art = kb.get(it.get("article_id") or "")
            c = it["classification"]
            if it["route"] == wr.URGENT and not (art and art["intent"] == "Technical Error" and resolver.keyword_evidence(q["query_text"], art) >= 1):
                art = None                       # an urgent ticket gets a draft only when the article is really about the outage; never a loosely related one
            if it["route"] == wr.FEEDBACK and not (art and art["intent"] == "Feature Request"):
                art = next((a for a in kb.values() if a["intent"] == "Feature Request"), None)   # a feature idea is always acknowledged with the feature-request article, never an unrelated one
            t = tm.create_ticket(q, c, it["route"], art, float(it.get("match_score") or 0), policy, at)
            flags = json.loads(q.get("flags") or "{}")
            r = store.create_ticket(t, q["query_text"], batch_id, actor, confidence=float(c.get("confidence") or 0), method=str(c.get("method") or ""), flags=flags, trace_id=trace_id)
            made.append({"ticket_id": r["ticket_id"], "query_id": r["query_id"], "status": r["status"], "assigned_team": r["assigned_team"], "priority": r["priority"], "existing": bool(r.get("_existing"))})
        return {"tickets": made, "created": sum(1 for m in made if not m["existing"]), "existing": sum(1 for m in made if m["existing"])}
    return await run_blocking("create_tickets", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def list_tickets(status: list[str] | None = None, team: str = "", priority: str = "", batch_id: str = "", active_only: bool = False, text: str = "", limit: int = 500) -> dict[str, Any]:
    """List tickets with their SLA state, filtered by status, team, priority or batch."""
    def work():
        policy, at = load_policy(), store.now()
        rows = store.list_tickets(status or None, team or None, priority or None, batch_id or None, active_only, text or None, limit)
        return {"tickets": [_decorate(t, at, policy) for t in rows], "count": len(rows), "service_time": store.iso(at)}
    return await run_blocking("list_tickets", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def get_ticket(ticket_id: str) -> dict[str, Any]:
    """One ticket with its history, comments, response versions, notifications and audit trail."""
    def work():
        t = store.get_ticket(ticket_id)
        if not t:
            raise InputError(f"Unknown ticket {ticket_id}")
        policy, at = load_policy(), store.now()
        notes = [{k: n[k] for k in ("notification_id", "event", "recipient_type", "to_addr", "state", "attempts", "needs_approval", "approved_by", "subject", "message", "detail")} for n in store.list_notifications(ticket_id)]
        return {"ticket": {**t, **_decorate(t, at, policy), "proposed_response": t["proposed_response"], "final_response": t["final_response"], "resolution_summary": t["resolution_summary"]},
                "first_response": sla.sla_status(t, at, policy, "first_response"), "resolution": sla.sla_status(t, at, policy, "resolution"),
                "history": store.ticket_history(ticket_id), "versions": store.response_versions(ticket_id), "notifications": notes,
                "audit": [{k: a[k] for k in ("event_id", "timestamp", "event_type", "actor", "ticket_status", "error_details")} for a in store.query_audit(ticket_id=ticket_id, limit=100)]}
    return await run_blocking("get_ticket", work)


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("HIGH"))
async def review_ticket(ticket_id: str, action: str, actor: str, note: str = "", edited_response: str = "", team: str = "", trace_id: str = "") -> dict[str, Any]:
    """Agent action: APPROVE, EDIT, HOLD, REQUEST_INFORMATION, ESCALATE or RESOLVE. The actor's role and team are checked on the server; closed tickets cannot be changed."""
    def work():
        t = store.get_ticket(ticket_id)
        if not t:
            raise InputError(f"Unknown ticket {ticket_id}")
        u = rbac.authorize(actor, "review", ticket=t, action=f"{action} ticket {ticket_id}", trace_id=trace_id)
        if team and team not in TEAMS:
            raise InputError(f"Unknown team {team}")
        policy = load_policy()
        out = store.apply_review(ticket_id, action, actor, note, edited_response or None, team or None, trace_id)
        made = []
        if action == "APPROVE":
            made.append(store.create_notification(out, "RESPONSE", actor, policy, approved_by=actor, response=out["final_response"], trace_id=trace_id))
        elif action == "REQUEST_INFORMATION":
            made.append(store.create_notification(out, "INFO_REQUESTED", actor, policy, approved_by=actor, trace_id=trace_id))
        elif action == "ESCALATE":
            made.append(store.create_notification(out, "ESCALATED", actor, policy, reason=note or "escalated by agent", trace_id=trace_id))
        elif action == "RESOLVE":
            made.append(store.create_notification(out, "RESOLVED", actor, policy, approved_by=actor, summary=out.get("resolution_summary") or "", trace_id=trace_id))
        return {"ticket_id": ticket_id, "status": out["status"], "action": action, "reviewer": u["name"], "notifications": [{"notification_id": n["notification_id"], "event": n.get("event"), "state": n.get("state")} for n in made]}
    return await run_blocking("review_ticket", work)


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("MEDIUM"))
async def customer_reply(ticket_id: str, helped: bool, actor: str, note: str = "", trace_id: str = "") -> dict[str, Any]:
    """Simulated customer reply to a message we sent: helped = true resolves the ticket (customer-confirmed), false reopens it with a higher priority."""
    def work():
        t = store.get_ticket(ticket_id)
        if not t:
            raise InputError(f"Unknown ticket {ticket_id}")
        rbac.authorize(actor, "run", ticket=None, action="record a customer reply", trace_id=trace_id)
        if t["status"] != "AWAITING_CUSTOMER":
            raise InputError(f"Ticket is {t['status']}, not waiting for the customer")
        if helped:
            if not t.get("final_response") and not t.get("proposed_response"):
                raise InputError("Nothing was sent to the customer")
            store.set_status_system(ticket_id, "RESOLVED", f"customer:{t['customer_id']}", "customer confirmed the answer helped", trace_id=trace_id)
            with store.tx() as con:
                con.execute("UPDATE tickets SET resolution_summary=? WHERE ticket_id=?", ("Confirmed by customer: " + (t.get("final_response") or t.get("proposed_response") or "")[:160], ticket_id))
            return {"ticket_id": ticket_id, "status": "RESOLVED", "confirmed": True}
        r = store.reopen_ticket(ticket_id, f"customer:{t['customer_id']}", note or "customer reports the answer did not help", load_policy(), trace_id=trace_id)
        with store.tx() as con:
            con.execute("UPDATE tickets SET status='IN_REVIEW', updated_at=? WHERE ticket_id=?", (store.iso(store.now()), ticket_id))
            store._event(con, ticket_id, "STATUS", "REOPENED", "IN_REVIEW", "system", "reopened tickets go to the agent queue")
        return {"ticket_id": ticket_id, "status": "IN_REVIEW", "reopened": True, "priority": r["priority"]}
    return await run_blocking("customer_reply", work)


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("MEDIUM"))
async def reopen_ticket(ticket_id: str, reason: str, actor: str, trace_id: str = "") -> dict[str, Any]:
    """Reopen a resolved or closed ticket (reason required, 7-day window). Priority rises one level, never above P2, and the SLA clock restarts."""
    def work():
        t = store.get_ticket(ticket_id)
        if not t:
            raise InputError(f"Unknown ticket {ticket_id}")
        rbac.authorize(actor, "review", ticket=t, action=f"reopen ticket {ticket_id}", trace_id=trace_id)
        r = store.reopen_ticket(ticket_id, actor, reason, load_policy(), trace_id=trace_id)
        store.create_notification({**t, **r}, "REOPENED", actor, load_policy(), reason=reason, trace_id=trace_id)
        return {"ticket_id": ticket_id, "status": "REOPENED", "priority": r["priority"], "reopen_count": r["reopen_count"]}
    return await run_blocking("reopen_ticket", work)


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("MEDIUM"))
async def reassign_ticket(ticket_id: str, team: str, actor: str, note: str = "", trace_id: str = "") -> dict[str, Any]:
    """Move a ticket to another team queue. Needs the 'reassign' permission (leads and admins)."""
    def work():
        t = store.get_ticket(ticket_id)
        if not t:
            raise InputError(f"Unknown ticket {ticket_id}")
        rbac.authorize(actor, "reassign", ticket=t, action=f"reassign ticket {ticket_id}", trace_id=trace_id)
        if team not in TEAMS:
            raise InputError(f"Unknown team {team}")
        with store.tx() as con:
            con.execute("UPDATE tickets SET assigned_team=?, assignee=NULL, updated_at=? WHERE ticket_id=?", (team, store.iso(store.now()), ticket_id))
            store._event(con, ticket_id, "ASSIGN", t["assigned_team"], team, actor, note or "reassigned")
        store.add_audit("ticket_reassigned", t["query_id"], ticket_id, priority=t["priority"], ticket_status=t["status"], actor=actor, trace_id=trace_id, error_details=f"{t['assigned_team']} -> {team}")
        return {"ticket_id": ticket_id, "assigned_team": team}
    return await run_blocking("reassign_ticket", work)


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("LOW"))
async def add_comment(ticket_id: str, text: str, actor: str) -> dict[str, Any]:
    """Add an internal comment to a ticket (needs the 'review' permission for that ticket's team)."""
    def work():
        t = store.get_ticket(ticket_id)
        if not t:
            raise InputError(f"Unknown ticket {ticket_id}")
        rbac.authorize(actor, "review", ticket=t, action="comment on a ticket")
        if not text.strip():
            raise InputError("Comment is empty")
        store.add_comment(ticket_id, actor, text.strip()[:1000])
        return {"ticket_id": ticket_id, "ok": True}
    return await run_blocking("add_comment", work)


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("MEDIUM"))
async def sla_scan(actor: str = "system:sla-monitor", apply_aging: bool = True, notify: bool = True, trace_id: str = "") -> dict[str, Any]:
    """Check every active ticket: record new AT_RISK and BREACHED alerts once, optionally promote tickets that waited too long, and notify the support lead."""
    def work():
        policy, at = load_policy(), store.now()
        aged = store.age_tickets(policy, at, trace_id) if apply_aging else []
        active = store.list_tickets(active_only=True, limit=5000)
        alerts = sla.scan(active, at, policy)
        new = store.record_alerts(alerts, at, trace_id)
        if notify:
            for a in new:
                t = store.get_ticket(a["ticket_id"])
                store.create_notification(t, "SLA_BREACH" if a["state"] == "BREACHED" else "SLA_AT_RISK", "system:sla-monitor", policy, used=a["used_min"], window=a["window_min"], which=a["which"].replace("_", " "), trace_id=trace_id)
        states: dict[str, int] = {}
        for t in active:
            s = sla.overall_state(t, at, policy)
            states[s] = states.get(s, 0) + 1
        return {"service_time": store.iso(at), "active": len(active), "states": states, "alerts": alerts, "new_alerts": new, "aged": aged}
    return await run_blocking("sla_scan", work)


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("HIGH"))
async def advance_clock(minutes: float, actor: str, trace_id: str = "") -> dict[str, Any]:
    """Advance the simulated service clock (for demonstrating SLA warnings). Needs the 'clock' permission."""
    def work():
        rbac.authorize(actor, "clock", action="advance the service clock", trace_id=trace_id)
        if not -10000 <= minutes <= 100000:
            raise InputError("minutes out of range")
        off = store.advance_clock(minutes)
        store.add_audit("clock_advanced", actor=actor, trace_id=trace_id, error_details=f"+{minutes} min (offset {off:.0f})")
        return {"offset_minutes": off, "service_time": store.iso(store.now())}
    return await run_blocking("advance_clock", work)


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("HIGH"))
async def reset_clock(actor: str, trace_id: str = "") -> dict[str, Any]:
    """Return the service clock to real time. Needs the 'clock' permission."""
    def work():
        rbac.authorize(actor, "clock", action="reset the service clock", trace_id=trace_id)
        store.reset_clock()
        store.add_audit("clock_reset", actor=actor, trace_id=trace_id)
        return {"offset_minutes": 0.0, "service_time": store.iso(store.now())}
    return await run_blocking("reset_clock", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def queue_summary(batch_id: str = "") -> dict[str, Any]:
    """Active tickets by priority queue and team, SLA states, and the longest waits."""
    def work():
        policy, at = load_policy(), store.now()
        act = store.list_tickets(active_only=True, batch_id=batch_id or None, limit=5000)
        by_p: dict[str, dict] = {p: {"count": 0, "breached": 0, "at_risk": 0} for p in ("P1", "P2", "P3", "P4")}
        by_team: dict[str, int] = {}
        for t in act:
            s = sla.overall_state(t, at, policy)
            d = by_p[t["priority"]]
            d["count"] += 1
            d["breached"] += s == "BREACHED"
            d["at_risk"] += s == "AT_RISK"
            by_team[t["assigned_team"]] = by_team.get(t["assigned_team"], 0) + 1
        waiting = sorted([_decorate(t, at, policy) for t in act if not t["first_response_at"]], key=lambda x: -x["first_response_used_min"])[:5]
        allt = store.list_tickets(batch_id=batch_id or None, limit=5000)
        status: dict[str, int] = {}
        for t in allt:
            status[t["status"]] = status.get(t["status"], 0) + 1
        return {"service_time": store.iso(at), "active": len(act), "total": len(allt), "by_priority": by_p, "by_team": by_team, "by_status": status, "longest_waits": waiting}
    return await run_blocking("queue_summary", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def next_ticket(actor: str, scheduling: str = "priority") -> dict[str, Any]:
    """The next ticket this agent should work on: priority queues (P1 first, then oldest deadline) or plain first-in-first-out. Agents see their own team's queue."""
    def work():
        u = rbac.authorize(actor, "review", action="take the next ticket")
        policy, at = load_policy(), store.now()
        pool = [t for t in store.list_tickets(status=["OPEN", "PENDING_APPROVAL", "IN_REVIEW", "ESCALATED", "REOPENED"], limit=5000) if not t["first_response_at"]]
        if u["role"] == "agent":
            pool = [t for t in pool if t["assigned_team"] == u["team"]]
        if scheduling == "fifo":
            pool.sort(key=lambda t: t["created_at"])
        else:
            pool.sort(key=lambda t: (t["priority"], t["first_response_due"]))
        return {"ticket": _decorate(pool[0], at, policy) if pool else None, "waiting": len(pool), "scheduling": scheduling}
    return await run_blocking("next_ticket", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def list_audit(ticket_id: str = "", event_type: str = "", limit: int = 200) -> dict[str, Any]:
    """Recent audit records (newest first), optionally for one ticket or event type."""
    def work():
        rows = store.query_audit(ticket_id or None, None, event_type or None, limit)
        return {"events": [{k: r[k] for k in store.AUDIT_FIELDS} for r in rows], "count": len(rows)}
    return await run_blocking("list_audit", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def verify_audit() -> dict[str, Any]:
    """Check the audit hash chain: confirms nothing was edited or removed."""
    return await run_blocking("verify_audit", store.verify_audit_chain)


if __name__ == "__main__":
    mcp.run()
