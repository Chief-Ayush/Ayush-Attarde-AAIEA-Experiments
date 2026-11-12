"""Notification drafts and the simulated mail gateway (the lab manual's notifications.py).

Rules enforced here: every draft is marked DRAFT, contains the candidate's name and application reference only (never a score or
another candidate's data), and the "not shortlisted" text never says that a final rejection has happened.  Nothing is sent by this
module; `send_via_gateway` is a simulation and refuses addresses on the bounce test domain.
"""
from __future__ import annotations

import re

from campushire.errors import InputError, ToolFailure

BOUNCE_DOMAIN = "bounce.example"          # addresses on this domain are refused by the simulated gateway (delivery-failure tests)

TEMPLATES = {
    "SHORTLIST_REVIEW": ("Application Update",
                         "Your application for the {role} position is being considered for the next stage of the recruitment process. "
                         "The HR team will review your application and communicate the next steps."),
    "MANUAL_REVIEW": ("Application Under Review",
                      "Your application for the {role} position is currently under review. The recruitment team will communicate any further updates."),
    "NOT_SHORTLISTED_REVIEW": ("Application Review Update",
                               "Your application for the {role} position is undergoing a review by the recruitment team. "
                               "You will be informed of the outcome after the review."),
    "REQUEST_INFO": ("Additional Information Needed",
                     "To continue reviewing your application for the {role} position, the recruitment team needs a little more information. "
                     "A member of the team will contact you through the official application portal."),
}
DRAFT_STATUSES = list(TEMPLATES)


def _safe_name(name: str) -> str:
    """Names are inserted into a mail body: remove line breaks and control characters (header / body injection)."""
    return re.sub(r"[\x00-\x1f\x7f<>]", " ", str(name or "")).strip()[:80] or "Candidate"


def generate_notification(candidate: dict, status: str) -> dict:
    """Return a DRAFT {candidate_id, to, subject, message, status} for a proposed workflow status."""
    if status not in TEMPLATES:
        raise InputError(f"No notification template for status '{status}'")
    subject, body = TEMPLATES[status]
    name, cid = _safe_name(candidate.get("candidate_name")), candidate["candidate_id"]
    role = _safe_name(candidate.get("application_role", "advertised"))
    message = (f"Dear {name},\n\n{body.format(role=role)}\n\nApplication reference: {cid}\n\nRegards,\nCampus Recruitment Team\n\n"
               "[DRAFT - not sent until approved by an HR reviewer]")
    return {"candidate_id": cid, "to": candidate.get("email", ""), "subject": subject, "message": message, "template": status, "state": "DRAFT"}


def draft_problems(draft: dict, candidate: dict, others: list[dict] | None = None) -> list[str]:
    """Checks used by the tests and the security suite: what must never appear in a draft."""
    text = f"{draft.get('subject', '')}\n{draft.get('message', '')}"
    problems = []
    if "DRAFT" not in text and draft.get("state") == "DRAFT":
        problems.append("draft is not marked DRAFT")
    if re.search(r"\bscore\b|\b\d{2,3}(\.\d+)?\s*/\s*100\b|\bpercent", text, re.I):
        problems.append("draft reveals scoring information")
    if re.search(r"\b(reject(ed|ion)?|unsuccessful|not selected|declined)\b", text, re.I):
        problems.append("draft implies a final decision")
    for o in others or []:
        same_name = o.get("candidate_name") == candidate.get("candidate_name")        # two applicants may share a name; the reference number tells them apart
        if o["candidate_id"] != candidate["candidate_id"] and (o["candidate_id"] in text or (o.get("candidate_name") and not same_name and o["candidate_name"] in text)):
            problems.append(f"draft mentions another candidate ({o['candidate_id']})")
            break
    return problems


def send_via_gateway(draft: dict) -> dict:
    """Simulated SMTP gateway: no network traffic.  Raises ToolFailure for the bounce domain (a delivery failure)."""
    to = draft.get("to", "")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$", to):
        raise InputError("Recipient address is missing or invalid")
    if to.lower().endswith("@" + BOUNCE_DOMAIN):
        raise ToolFailure("550 mailbox unavailable (simulated delivery failure)")
    return {"accepted": True, "gateway": "simulated-smtp", "to": to}
