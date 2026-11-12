"""Workflow routing (lab step 8): classification + knowledge match + risk rules -> a proposed route (not a completed action)."""
from __future__ import annotations

URGENT = "URGENT_HUMAN_ESCALATION"
TECH = "TECHNICAL_HUMAN_REVIEW"
NO_ARTICLE = "CREATE_TICKET_AND_ESCALATE"
AUTO = "PROPOSE_AUTO_RESOLUTION"
REVIEW = "HUMAN_REVIEW"
FEEDBACK = "PRODUCT_FEEDBACK_QUEUE"
INFO = "REQUEST_INFORMATION_OR_ESCALATE"

ROUTE_CLASS = {URGENT: "URG", TECH: "REV", NO_ARTICLE: "REV", REVIEW: "REV", AUTO: "AUTO", FEEDBACK: "FEED", INFO: "INFO"}
CLASS_LABEL = {"URG": "Urgent escalation", "REV": "Agent review", "AUTO": "Proposed answer", "FEED": "Product feedback", "INFO": "Ask for details"}


def route_query(classification: dict, match_score: float, article_found: bool, policy: dict | None = None, evidence: dict | None = None) -> str:
    p = {"knowledge_match_threshold": 70, "review_threshold": 40, **(policy or {})}
    priority, complexity = classification["priority"], classification["complexity"]
    if priority == "P1":                       # critical incidents bypass ordinary automation whatever the score (TC10)
        return URGENT
    if classification.get("ambiguous"):        # nothing to ground an answer on: ask the customer first
        return INFO
    if classification["intent"] == "Feature Request":
        return FEEDBACK
    if complexity == "High":
        return TECH
    if not article_found:
        return NO_ARTICLE
    if match_score >= p["knowledge_match_threshold"] and complexity == "Low" and priority in ("P3", "P4") and auto_evidence_ok(evidence):
        return AUTO
    if match_score >= p["review_threshold"]:
        return REVIEW
    return INFO


MIN_MARGIN = 0.03          # chosen on the training queries: with these three checks none of the auto-answers on that set was wrong


def auto_evidence_ok(evidence: dict | None) -> bool:
    """Guard on sending an answer without a person: the classifier and the search must agree on the topic, the best article must clearly beat the
    runner-up, and at least one of the article's own keywords must appear in the query.  evidence=None (the manual's baseline) means no guard."""
    if evidence is None:
        return True
    return bool(evidence.get("intent_match")) and float(evidence.get("margin", 0)) >= MIN_MARGIN and int(evidence.get("keyword_hits", 0)) >= 1


def route_class(route: str) -> str:
    return ROUTE_CLASS.get(route, "REV")


TEAM_BY_INTENT = {"Login / Access": "Access Support", "Billing": "Billing Support", "Technical Error": "Technical Support",
                  "Unresolved Issue": "Technical Support", "Feature Request": "Product Feedback", "General Information": "Tier 1 Support"}


def assign_team(classification: dict, route: str) -> str:
    if route == URGENT or classification["priority"] == "P1":
        return "Incident Response"
    return TEAM_BY_INTENT.get(classification["intent"], "Tier 1 Support")
