"""Notifications with a simulated gateway, retries, idempotency keys and a dead-letter queue (lab steps 11-12, variation 9).

No real mail is sent.  The gateway behaves deterministically by recipient address so every failure path can be tested:
  *@bounce.example   permanent failure (invalid recipient) - never retried, goes straight to the dead-letter queue
  *@denied.example   permanent authorisation error - never retried
  *@ratelimit.example  transient 429 on the first attempt, then accepted
  flakyN@...         the first N attempts fail transiently, then the message is accepted
  anything else      accepted on the first attempt
"""
from __future__ import annotations

import hashlib
import re
import time

EVENTS = {
    "CREATED": ("customer", "We received your request ({ticket_id})", "Hello, we have received your request and created ticket {ticket_id}. A {team} specialist will respond within {sla}."),
    "ASSIGNED": ("agent", "New ticket {ticket_id} assigned to your queue", "Ticket {ticket_id} ({priority}, {intent}) was assigned to {team}. First response is due in {sla}."),
    "ESCALATED": ("specialist", "Escalation: ticket {ticket_id} needs specialist review", "Ticket {ticket_id} ({priority}) requires specialist review by {team}. Reason: {reason}."),
    "INFO_REQUESTED": ("customer", "More details needed for {ticket_id}", "To help with ticket {ticket_id} we need a little more detail: what you were doing, what you expected, and any error text shown. Reply to this message with the details."),
    "RESPONSE": ("customer", "Re: your request {ticket_id}", "{response}\n\nIf this does not solve the problem, reply to this message and we will reopen ticket {ticket_id}."),
    "RESOLVED": ("customer", "Ticket {ticket_id} resolved", "Ticket {ticket_id} has been marked as resolved. {summary}"),
    "SLA_AT_RISK": ("lead", "SLA at risk: {ticket_id}", "Ticket {ticket_id} ({priority}, {team}) has used {used} of its {window}-minute {which} target."),
    "SLA_BREACH": ("lead", "SLA breached: {ticket_id}", "Ticket {ticket_id} ({priority}, {team}) has exceeded its {which} target of {window} minutes."),
    "REOPENED": ("agent", "Ticket {ticket_id} was reopened", "Ticket {ticket_id} was reopened: {reason}. Priority is now {priority}."),
}
CUSTOMER_FACING = {"CREATED", "INFO_REQUESTED", "RESPONSE", "RESOLVED"}
NEEDS_APPROVAL = {"RESPONSE"}           # agent-authored or KB answers are shown to the customer only after approval; acknowledgements are safe templates


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


def address_for(recipient_type: str, ticket: dict, users: dict | None = None) -> str:
    if recipient_type == "customer":
        cid = str(ticket["customer_id"]).lower()
        for dom in ("bounce", "denied", "ratelimit"):          # simulated mailbox behaviour used by the delivery tests
            if cid.startswith(dom):
                return f"{cid}@{dom}.example"
        return f"{cid}@customers.example"
    team = (ticket.get("assigned_team") or "support").lower().replace(" ", "-")
    return {"agent": f"{team}@agents.example", "specialist": f"{team}@agents.example", "lead": "support-leads@agents.example"}.get(recipient_type, "support@agents.example")


def _fmt_minutes(m: int) -> str:
    return f"{m // 60} hours" if m >= 120 and m % 60 == 0 else f"{m} minutes"


def generate_notification(ticket: dict, event: str, policy: dict | None = None, **extra) -> dict:
    rtype, subject, body = EVENTS.get(event, ("agent", "Ticket {ticket_id} updated", "Ticket {ticket_id} has been updated."))
    sla = _fmt_minutes((policy or {}).get("sla_minutes", {}).get(ticket.get("base_priority") or ticket["priority"], 60)) if policy else "the target time"
    ctx = {"ticket_id": ticket["ticket_id"], "team": ticket.get("assigned_team") or "support", "priority": ticket["priority"], "intent": ticket["intent"], "sla": sla,
           "response": extra.get("response") or ticket.get("final_response") or ticket.get("proposed_response") or "", "summary": extra.get("summary") or ticket.get("resolution_summary") or "",
           "reason": extra.get("reason") or "", "used": extra.get("used", ""), "window": extra.get("window", ""), "which": extra.get("which", "")}
    to = extra.get("to_addr") or address_for(rtype, ticket)
    msg = body.format(**ctx)
    return {"ticket_id": ticket["ticket_id"], "event": event, "recipient_type": rtype, "to_addr": to, "subject": subject.format(**ctx), "message": msg,
            "state": "DRAFT", "needs_approval": event in NEEDS_APPROVAL, "idempotency_key": idempotency_key(ticket["ticket_id"], event, to, msg)}


def idempotency_key(ticket_id: str, event: str, to_addr: str, message: str) -> str:
    return hashlib.sha256(f"{ticket_id}|{event}|{to_addr}|{message}".encode()).hexdigest()[:20]


def gateway_send(to_addr: str, attempt: int, sim_fail_first: int = 0) -> str:
    """The simulated provider.  Returns a provider message id or raises TransientError / PermanentError."""
    dom = to_addr.split("@")[-1].lower()
    local = to_addr.split("@")[0].lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", to_addr.lower()):
        raise PermanentError("invalid recipient address")
    if dom == "bounce.example":
        raise PermanentError("550 mailbox does not exist")
    if dom == "denied.example":
        raise PermanentError("535 authentication rejected")
    if dom == "ratelimit.example" and attempt < 2:
        raise TransientError("429 rate limit, retry later")
    m = re.match(r"flaky(\d+)$", local)
    fails = int(m.group(1)) if m else sim_fail_first
    if attempt <= fails:
        raise TransientError(f"451 temporary failure (attempt {attempt})")
    return "prov-" + hashlib.sha1(f"{to_addr}{attempt}".encode()).hexdigest()[:10]


def send_with_retry(to_addr: str, max_attempts: int = 3, backoff_s: float = 0.01, sim_fail_first: int = 0, sleep=time.sleep) -> dict:
    """Retry transient failures with exponential backoff (backoff_s, 2x, 4x ...); permanent errors stop immediately.  Every attempt is returned."""
    attempts = []
    for n in range(1, max_attempts + 1):
        try:
            pid = gateway_send(to_addr, n, sim_fail_first)
            attempts.append({"attempt": n, "result": "SENT", "error": ""})
            return {"status": "SENT", "attempts": attempts, "provider_id": pid}
        except PermanentError as exc:
            attempts.append({"attempt": n, "result": "PERMANENT_FAILURE", "error": str(exc)})
            return {"status": "DEAD_LETTER", "attempts": attempts, "error": str(exc), "permanent": True}
        except TransientError as exc:
            attempts.append({"attempt": n, "result": "TRANSIENT_FAILURE", "error": str(exc)})
            if n == max_attempts:
                return {"status": "DEAD_LETTER", "attempts": attempts, "error": str(exc), "permanent": False}
            sleep(backoff_s * 2 ** (n - 1))
    return {"status": "DEAD_LETTER", "attempts": attempts, "error": "no attempts", "permanent": False}
