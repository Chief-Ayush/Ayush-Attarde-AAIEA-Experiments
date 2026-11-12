"""Agent behaviour for unattended runs (evaluation, console demo): which signed-in agent handles a team, and what that agent does with a ticket."""
from __future__ import annotations

from ..config import PATHS, load_users


def agent_for(team: str) -> str:
    """Pick a signed-in agent of the right team (the lead covers Incident Response)."""
    users = load_users()
    for uid, u in users.items():
        if u.get("team") == team and u["role"] in ("agent", "lead"):
            return uid
    return next(uid for uid, u in users.items() if u["role"] == "lead")


_KB: dict = {}


def _draft_fits(t: dict) -> bool:
    """The agent reads the draft: it is used only when the article is about the same kind of problem as the ticket."""
    import resolver
    if not _KB:
        try:
            _KB.update({a["article_id"]: a for a in resolver.load_knowledge_base(PATHS.kb_json)})
        except resolver.KnowledgeBaseError:
            return False
    art = _KB.get(t.get("article_id") or "")
    if not (art and art["approved"] and float(t.get("match_score") or 0) >= 70):
        return False
    topical = art["intent"] == t["intent"] or t["intent"] == "Unresolved Issue" or t["priority"] == "P1"
    return topical


def auto_agent_policy(t: dict) -> dict:
    """Agent behaviour for unattended runs (evaluation, console demo): approve a grounded draft, hold anything whose text tried to steer the system,
    ask for details when there is no grounded draft, acknowledge feature requests."""
    actor = agent_for(t["assigned_team"])
    base = {"ticket_id": t["ticket_id"], "actor": actor}
    flags = t.get("flags") or ""
    if '"injection": true' in str(flags):
        return {**base, "action": "HOLD", "note": "Text tried to instruct the system - checked by a person first"}
    if t["route"] == "PRODUCT_FEEDBACK_QUEUE":
        return {**base, "action": "RESOLVE", "note": "Logged with the product team and acknowledged"}
    if t["route"] == "REQUEST_INFORMATION_OR_ESCALATE" or not t.get("has_response") or not _draft_fits(t):
        return {**base, "action": "REQUEST_INFORMATION", "note": "No approved answer found; asked the customer for details"}
    return {**base, "action": "APPROVE", "note": ""}


