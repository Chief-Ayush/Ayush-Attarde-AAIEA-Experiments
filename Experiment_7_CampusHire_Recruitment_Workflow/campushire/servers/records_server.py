"""MCP server: drives, human review decisions, audit log, SLA reminders and summary reports."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP
from typing_extensions import TypedDict

import recruitment_workflow as rw

from .. import rbac, store
from ..config import PATHS, PERMISSIONS, REVIEW_ACTIONS, load_settings
from ..errors import InputError
from .common import DESTRUCTIVE, READ_ONLY, WRITES_LOCAL, risk_meta, run_blocking

mcp = FastMCP("campushire-records", instructions="System of record: drives, reviewer decisions and the append-only audit log.", log_level="WARNING")


class Decision(TypedDict):
    drive_id: str
    candidate_id: str
    action: str
    proposed_status: str
    final_status: str
    override: bool
    reviewer: str
    reviewer_role: str


class Access(TypedDict):
    user_id: str
    name: str
    role: str
    permissions: list[str]
    allowed: bool


class ChainStatus(TypedDict):
    ok: bool
    rows: int
    first_bad_id: int | None


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("MEDIUM"))
async def create_drive(name: str, actor: str, rule_version: str = "", drive_id: str = "") -> dict[str, Any]:
    """Create a hiring drive (needs the 'run' permission). The rule version is recorded; the active version is used when none is given."""
    def work():
        rbac.authorize(actor, "run", action="create a drive")
        ver = rule_version or rw.load_rules(PATHS.rules_json).get("active_version")
        return store.create_drive(name, actor, ver, drive_id or None)
    return await run_blocking("create_drive", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def list_drives() -> dict[str, Any]:
    """All hiring drives with candidate and quarantine counts."""
    return await run_blocking("list_drives", lambda: {"drives": store.list_drives()})


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def check_access(actor: str, permission: str) -> Access:
    """Role and permissions of a user (the role is read from users.json on the server)."""
    def work():
        role = rbac.role_of(actor)
        u = rbac.load_users().get(actor, {})
        return {"user_id": actor, "name": u.get("name", ""), "role": role, "permissions": sorted(PERMISSIONS.get(role, set())), "allowed": rbac.can(actor, permission)}
    return await run_blocking("check_access", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def get_queue(drive_id: str) -> dict[str, Any]:
    """Review queue: every scored candidate with score, explanation, proposed status, reviewer decision and notification state."""
    return await run_blocking("get_queue", lambda: {"drive_id": drive_id, "queue": store.queue(drive_id), "rejected": store.rejected(drive_id)})


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("MEDIUM"))
async def record_review(drive_id: str, candidate_id: str, action: str, reviewer: str, new_status: str = "", note: str = "", override_reason: str = "",
                        trace_id: str = "") -> Decision:
    """Record a reviewer decision: APPROVE, EDIT (needs new_status and an override reason), HOLD or REQUEST_INFO (both need a note). Needs the 'review' permission."""
    def work():
        who = rbac.authorize(reviewer, "review", drive_id=drive_id, candidate_id=candidate_id, action=f"{action} a candidate", trace_id=trace_id)
        if action not in REVIEW_ACTIONS:
            raise InputError(f"Action must be one of {REVIEW_ACTIONS}")
        res = {r["candidate_id"]: r for r in store.results(drive_id)}
        if candidate_id not in res:
            raise InputError(f"Candidate {candidate_id} has no score in this drive")
        proposed = res[candidate_id]["proposed_status"]
        if action == "EDIT":
            if new_status not in rw.ROUTES:
                raise InputError(f"EDIT needs a new status from {rw.ROUTES}")
            if new_status != proposed and len(override_reason.strip()) < 5:
                raise InputError("A documented override reason is required when the proposed status is changed")
        if action in ("HOLD", "REQUEST_INFO") and len(note.strip()) < 3:
            raise InputError(f"{action} needs a note explaining why")
        final = rw.review_action(proposed, action, new_status or None)
        override = action == "EDIT" and final != proposed
        store.add_review(drive_id, candidate_id, action, reviewer, who["role"], final, note.strip(), override_reason.strip() if override else "")
        store.add_audit("review_decision", drive_id, candidate_id, proposed_status=proposed, reviewer_action=action, final_status=final,
                        error_details=(f"override: {override_reason.strip()}" if override else (note.strip() or None)), actor=f"{reviewer} ({who['role']})", trace_id=trace_id)
        return {"drive_id": drive_id, "candidate_id": candidate_id, "action": action, "proposed_status": proposed, "final_status": final, "override": override,
                "reviewer": reviewer, "reviewer_role": who["role"]}
    return await run_blocking("record_review", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def candidate_trail(drive_id: str, candidate_id: str) -> dict[str, Any]:
    """Everything recorded about one candidate: audit events, reviewer decisions and notification."""
    def work():
        return {"candidate_id": candidate_id, "audit": list(reversed(store.query_audit(drive_id, candidate_id, limit=200))),
                "reviews": store.review_history(drive_id, candidate_id), "drafts": [d for d in store.drafts(drive_id) if d["candidate_id"] == candidate_id]}
    return await run_blocking("candidate_trail", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def query_audit(drive_id: str = "", candidate_id: str = "", event: str = "", limit: int = 200) -> dict[str, Any]:
    """Read audit records (newest first)."""
    return await run_blocking("query_audit", lambda: {"records": store.query_audit(drive_id or None, candidate_id or None, event or None, min(limit, 2000))})


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def verify_audit() -> ChainStatus:
    """Verify the audit hash chain; reports the first row that was changed, if any."""
    return await run_blocking("verify_audit", store.verify_audit_chain)


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("MEDIUM"))
async def export_audit(actor: str) -> dict[str, Any]:
    """Append new audit records to audit_log.csv (existing lines are never rewritten)."""
    def work():
        rbac.authorize(actor, "view", action="export the audit log")
        return store.export_audit_csv()
    return await run_blocking("export_audit", work)


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("MEDIUM"))
async def sla_check(drive_id: str, actor: str, as_of_offset_hours: float = 0.0, sla_hours: int = 0) -> dict[str, Any]:
    """Find candidates waiting longer than the SLA for a reviewer decision (or on hold / awaiting information) and write one reminder each.
    `as_of_offset_hours` moves 'now' forward so the reminders can be demonstrated and tested."""
    def work():
        rbac.authorize(actor, "run", drive_id=drive_id, action="run SLA reminders")
        limit = sla_hours or load_settings().sla_hours
        now = datetime.fromisoformat(store.now_iso(as_of_offset_hours))
        reviews = store.latest_reviews(drive_id)
        with store.tx() as con:
            done = {r["candidate_id"] for r in con.execute("SELECT candidate_id FROM reminders WHERE drive_id=?", (drive_id,))}
        due = []
        for r in store.results(drive_id):
            rv = reviews.get(r["candidate_id"])
            if rv and rv["final_status"] not in ("ON_HOLD", "AWAITING_INFORMATION"):
                continue
            since = datetime.fromisoformat(rv["decided_at"] if rv else r["processed_at"])
            waited = (now - since).total_seconds() / 3600
            if waited > limit:
                due.append({"candidate_id": r["candidate_id"], "waiting_hours": round(waited, 1), "state": rv["final_status"] if rv else "AWAITING_REVIEW"})
        new = [d for d in due if d["candidate_id"] not in done]
        with store.tx() as con:
            for d in new:
                con.execute("INSERT INTO reminders (drive_id,candidate_id,waiting_hours,created_at) VALUES (?,?,?,?)", (drive_id, d["candidate_id"], d["waiting_hours"], store.now_iso()))
        for d in new:
            store.add_audit("sla_reminder", drive_id, d["candidate_id"], error_details=f"waiting {d['waiting_hours']} h (limit {limit} h), state {d['state']}", actor=actor)
        return {"drive_id": drive_id, "sla_hours": limit, "overdue": len(due), "new_reminders": len(new), "items": due}
    return await run_blocking("sla_check", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def drive_report(drive_id: str) -> dict[str, Any]:
    """Summary report of a drive (variation 7): counts, status distribution, overrides with reasons, notification outcomes, quarantine reasons, SLA state."""
    def work():
        d = store.get_drive(drive_id)
        q = store.queue(drive_id)
        rej = store.rejected(drive_id)
        hist = store.review_history(drive_id)
        dr = store.drafts(drive_id)
        cands = store.candidates(drive_id)
        decided = [x for x in q if x["reviewed"]]
        sent = sum(1 for x in dr if x["state"] == "SENT")
        failed = sum(1 for x in dr if x["state"] == "FAILED")
        attempted = sent + failed
        with store.tx() as con:
            reminders = con.execute("SELECT COUNT(*) n FROM reminders WHERE drive_id=?", (drive_id,)).fetchone()["n"]
        scores = [x["score"] for x in q]
        return {
            "drive": d, "candidates": len(cands), "scored": len(q), "quarantined": len(rej),
            "duplicates": sum(1 for r in rej if r["code"].startswith("DUPLICATE")),
            "quarantine_reasons": dict(Counter(r["code"] for r in rej)),
            "proposed": dict(Counter(x["proposed_status"] for x in q)),
            "final": dict(Counter(x["final_status"] for x in decided)),
            "by_role": dict(Counter(c["application_role"] for c in cands)),
            "reviewed": len(decided), "review_completion": round(len(decided) / len(q), 4) if q else 0.0,
            "overrides": [{"candidate_id": h["candidate_id"], "reviewer": h["reviewer"], "role": h["reviewer_role"], "reason": h["override_reason"], "to": h["final_status"]}
                          for h in hist if h["override_reason"]],
            "actions": dict(Counter(h["action"] for h in hist)),
            "notifications": dict(Counter(x["state"] for x in dr)),
            "delivery_success_rate": round(sent / attempted, 4) if attempted else None,
            "sla_reminders": reminders,
            "score_summary": {"min": min(scores), "max": max(scores), "mean": round(sum(scores) / len(scores), 2)} if scores else {},
            "flagged_text": [c["candidate_id"] for c in cands if c["flags"]],
        }
    return await run_blocking("drive_report", work)


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("MEDIUM"))
async def log_event(drive_id: str, event: str, actor: str, detail: str = "", trace_id: str = "") -> dict[str, Any]:
    """Write a workflow event (run started / completed / failed) to the audit log. Needs the 'run' permission."""
    def work():
        rbac.authorize(actor, "run", drive_id=drive_id, action="record a workflow event", trace_id=trace_id)
        return {"audit_id": store.add_audit(event, drive_id, error_details=detail[:500] or None, actor=actor, trace_id=trace_id)}
    return await run_blocking("log_event", work)


@mcp.tool(annotations=DESTRUCTIVE, meta=risk_meta("HIGH"))
async def reset_drive(drive_id: str, actor: str) -> dict[str, Any]:
    """Clear a drive's working data (admin only). The audit log is never deleted."""
    def work():
        rbac.authorize(actor, "reset", drive_id=drive_id, action="reset a drive")
        store.reset_drive(drive_id, actor)
        return {"drive_id": drive_id, "reset": True}
    return await run_blocking("reset_drive", work)


if __name__ == "__main__":
    mcp.run()
