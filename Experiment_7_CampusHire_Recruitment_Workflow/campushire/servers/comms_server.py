"""MCP server: notification drafts, reviewer edits, message approval and simulated dispatch with retry.

A message can only be dispatched when (1) a reviewer has decided on the candidate, (2) the reviewer approved the exact message text,
(3) the draft matches the final status.  Outcomes are SENT, FAILED or NOT_SENT; nothing is ever sent twice.
"""
from __future__ import annotations

import time
from typing import Any

from mcp.server.fastmcp import FastMCP
from typing_extensions import TypedDict

import notifications as nt

from .. import rbac, store
from ..config import load_settings
from ..errors import InputError, SecurityError, ToolFailure
from .common import READ_ONLY, SENDS, WRITES_LOCAL, risk_meta, run_blocking

mcp = FastMCP("campushire-comms", instructions="Creates DRAFT notifications only. Dispatch needs reviewer approval of the message text.", log_level="WARNING")

NOT_SENDABLE = {"ON_HOLD", "REQUIRES_REVIEW"}


class Drafts(TypedDict):
    drive_id: str
    created: int
    problems: list[dict[str, Any]]
    drafts: list[dict[str, Any]]


class DraftState(TypedDict):
    draft_id: str
    state: str
    detail: str


class Dispatched(TypedDict):
    draft_id: str
    candidate_id: str
    outcome: str
    attempts: int
    detail: str


def _template_for(final_status: str) -> str:
    return "REQUEST_INFO" if final_status == "AWAITING_INFORMATION" else final_status


def _public(d: dict) -> dict:
    return {"draft_id": d["draft_id"], "candidate_id": d["candidate_id"], "to": d["to_addr"], "subject": d["subject"], "message": d["message"],
            "template": d["template"], "state": d["state"], "attempts": d["attempts"], "detail": d["detail"]}


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("MEDIUM"))
async def generate_drafts(drive_id: str, items: list[dict[str, Any]], actor: str, trace_id: str = "") -> Drafts:
    """Create DRAFT notifications. items: [{candidate_id, status}] where status is a proposed status or REQUEST_INFO. One live draft per candidate; sent ones are kept."""
    def work():
        rbac.authorize(actor, "run", drive_id=drive_id, action="create notification drafts", trace_id=trace_id)
        cands = {c["candidate_id"]: c for c in store.candidates(drive_id)}
        others = list(cands.values())
        out, problems = [], []
        for it in items:
            cid, status = it.get("candidate_id"), it.get("status")
            if cid not in cands:
                problems.append({"candidate_id": cid, "problem": "candidate is not in this drive"})
                continue
            d = nt.generate_notification(cands[cid], status)
            issues = nt.draft_problems(d, cands[cid], others)
            if issues:
                problems.append({"candidate_id": cid, "problem": "; ".join(issues)})
                store.add_audit("draft_blocked", drive_id, cid, notification_status="BLOCKED", error_details="; ".join(issues), actor=actor, trace_id=trace_id)
                continue
            did = store.save_draft(drive_id, d)
            store.add_audit("draft_created", drive_id, cid, notification_status="DRAFT", proposed_status=status, actor=actor, trace_id=trace_id)
            out.append(_public(store.get_draft(did)))
        return {"drive_id": drive_id, "created": len(out), "problems": problems, "drafts": out}
    return await run_blocking("generate_drafts", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def list_drafts(drive_id: str, state: str = "") -> dict[str, Any]:
    """List notification drafts of a drive, optionally filtered by state (DRAFT, APPROVED, SENT, FAILED, NOT_SENT)."""
    def work():
        rows = [_public(d) for d in store.drafts(drive_id, state or None)]
        return {"drive_id": drive_id, "count": len(rows), "drafts": rows}
    return await run_blocking("list_drafts", work)


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("MEDIUM"))
async def edit_draft(draft_id: str, subject: str, message: str, actor: str, trace_id: str = "") -> DraftState:
    """Reviewer edit of a draft. The new text is checked (no scores, no final-rejection wording, no other candidate) and the draft goes back to DRAFT."""
    def work():
        d = store.get_draft(draft_id)
        rbac.authorize(actor, "review", drive_id=d["drive_id"], candidate_id=d["candidate_id"], action="edit a notification", trace_id=trace_id)
        if d["state"] == "SENT":
            raise InputError("A sent notification cannot be edited")
        cands = store.candidates(d["drive_id"])
        me = next(c for c in cands if c["candidate_id"] == d["candidate_id"])
        issues = nt.draft_problems({"subject": subject, "message": message, "state": "DRAFT"}, me, cands)
        if issues:
            raise InputError("Edited text refused: " + "; ".join(issues))
        store.update_draft(draft_id, subject=subject, message=message, state="DRAFT", edited_by=actor, approved_by=None, detail="edited by reviewer")
        store.add_audit("draft_edited", d["drive_id"], d["candidate_id"], notification_status="DRAFT", actor=actor, trace_id=trace_id)
        return {"draft_id": draft_id, "state": "DRAFT", "detail": "edited, needs approval again"}
    return await run_blocking("edit_draft", work)


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("MEDIUM"))
async def approve_draft(draft_id: str, actor: str, trace_id: str = "") -> DraftState:
    """Reviewer approves the exact message text. Only possible after the reviewer has decided on the candidate and when the draft matches that decision."""
    def work():
        d = store.get_draft(draft_id)
        who = rbac.authorize(actor, "review", drive_id=d["drive_id"], candidate_id=d["candidate_id"], action="approve a notification", trace_id=trace_id)
        rev = store.latest_reviews(d["drive_id"]).get(d["candidate_id"])
        if not rev:
            raise InputError("The candidate has no reviewer decision yet - decide first, then approve the message")
        if rev["final_status"] in NOT_SENDABLE:
            raise InputError(f"Notification not allowed while the candidate is {rev['final_status']}")
        if d["template"] != _template_for(rev["final_status"]):
            raise InputError("The draft does not match the reviewer's decision - regenerate the draft")
        if d["state"] == "SENT":
            return {"draft_id": draft_id, "state": "SENT", "detail": "already sent"}
        cands = store.candidates(d["drive_id"])
        me = next(c for c in cands if c["candidate_id"] == d["candidate_id"])
        issues = nt.draft_problems({"subject": d["subject"], "message": d["message"], "state": "DRAFT"}, me, cands)
        if issues:
            raise InputError("Message cannot be approved: " + "; ".join(issues))
        store.update_draft(draft_id, state="APPROVED", approved_by=actor, detail=f"approved by {who['name']}")
        store.add_audit("draft_approved", d["drive_id"], d["candidate_id"], notification_status="APPROVED", final_status=rev["final_status"], actor=actor, trace_id=trace_id)
        return {"draft_id": draft_id, "state": "APPROVED", "detail": f"approved by {who['name']}"}
    return await run_blocking("approve_draft", work)


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("MEDIUM"))
async def withhold_draft(draft_id: str, reason: str, actor: str, trace_id: str = "") -> DraftState:
    """Mark a draft NOT_SENT (candidate on hold, reviewer declined to send)."""
    def work():
        d = store.get_draft(draft_id)
        rbac.authorize(actor, "review", drive_id=d["drive_id"], candidate_id=d["candidate_id"], action="withhold a notification", trace_id=trace_id)
        if d["state"] == "SENT":
            return {"draft_id": draft_id, "state": "SENT", "detail": "already sent"}
        store.update_draft(draft_id, state="NOT_SENT", detail=reason[:200])
        store.add_audit("notification_not_sent", d["drive_id"], d["candidate_id"], notification_status="NOT_SENT", error_details=reason[:200], actor=actor, trace_id=trace_id)
        return {"draft_id": draft_id, "state": "NOT_SENT", "detail": reason[:200]}
    return await run_blocking("withhold_draft", work)


@mcp.tool(annotations=SENDS, meta=risk_meta("HIGH"))
async def dispatch_notification(draft_id: str, actor: str, trace_id: str = "") -> Dispatched:
    """Send an APPROVED draft through the (simulated) mail gateway. Failed deliveries are retried up to the configured limit; the outcome is SENT or FAILED."""
    def work():
        d = store.get_draft(draft_id)
        rbac.authorize(actor, "dispatch", drive_id=d["drive_id"], candidate_id=d["candidate_id"], action="send a notification", trace_id=trace_id)
        if d["state"] == "SENT":
            return {"draft_id": draft_id, "candidate_id": d["candidate_id"], "outcome": "SENT", "attempts": d["attempts"], "detail": "already sent - not sent again"}
        if d["state"] != "APPROVED":
            raise SecurityError(f"Dispatch refused: the message is {d['state']}, it must be approved by a reviewer first")
        rev = store.latest_reviews(d["drive_id"]).get(d["candidate_id"])
        if not rev or rev["final_status"] in NOT_SENDABLE or d["template"] != _template_for(rev["final_status"]):
            raise SecurityError("Dispatch refused: no matching reviewer decision")
        limit = 1 + max(0, load_settings().notification_retries)
        attempts, detail, outcome = 0, "", "FAILED"
        while attempts < limit:
            attempts += 1
            try:
                nt.send_via_gateway({"to": d["to_addr"], "subject": d["subject"], "message": d["message"]})
                outcome, detail = "SENT", f"delivered on attempt {attempts}"
                break
            except ToolFailure as exc:
                detail = f"{exc} (attempt {attempts} of {limit})"
                store.add_audit("notification_attempt_failed", d["drive_id"], d["candidate_id"], notification_status="RETRY" if attempts < limit else "FAILED",
                                error_details=detail, actor=actor, trace_id=trace_id)
                time.sleep(0.02 * attempts)
            except InputError as exc:                      # invalid address: retrying cannot help
                detail = str(exc)
                break
        store.update_draft(draft_id, state=outcome, attempts=attempts, detail=detail)
        store.add_audit("notification_dispatched", d["drive_id"], d["candidate_id"], notification_status=outcome, final_status=rev["final_status"],
                        error_details=None if outcome == "SENT" else detail, actor=actor, trace_id=trace_id)
        return {"draft_id": draft_id, "candidate_id": d["candidate_id"], "outcome": outcome, "attempts": attempts, "detail": detail}
    return await run_blocking("dispatch_notification", work)


if __name__ == "__main__":
    mcp.run()
