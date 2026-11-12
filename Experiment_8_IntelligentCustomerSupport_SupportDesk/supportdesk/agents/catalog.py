"""Agent catalog: what each agent does, which graph nodes it owns and which MCP tools it calls (shown on the Agents page and in the README)."""
from __future__ import annotations

AGENTS = [
    {"agent": "IntakeAgent", "nodes": ["ingest", "validate"], "server": "intake", "tools": ["inspect_source", "load_source", "validate_queries", "import_queries"],
     "role": "Reads the file, rejects bad rows, masks personal data."},
    {"agent": "TriageAgent", "nodes": ["classify"], "server": "knowledge", "tools": ["classify_queries"],
     "role": "Intent, priority, complexity and urgency signals."},
    {"agent": "KnowledgeAgent", "nodes": ["retrieve"], "server": "knowledge", "tools": ["search_knowledge"],
     "role": "Finds the approved help article for each query."},
    {"agent": "RouterAgent", "nodes": ["route"], "server": "knowledge", "tools": ["decide_routes"],
     "role": "Chooses auto-answer, review, specialist team or escalation."},
    {"agent": "TicketAgent", "nodes": ["make_tickets"], "server": "tickets", "tools": ["create_tickets"],
     "role": "Creates tickets with SLA deadlines and an audit trail."},
    {"agent": "NotificationAgent", "nodes": ["notify"], "server": "comms", "tools": ["prepare_notifications", "dispatch_pending"],
     "role": "Queues customer and team messages."},
    {"agent": "ReviewAgent", "nodes": ["agent_review", "apply_actions"], "server": "tickets + comms", "tools": ["list_tickets", "review_ticket", "dispatch_pending"],
     "role": "Waits for a human decision, applies it, sends the replies."},
    {"agent": "SLAAgent", "nodes": ["sla_check"], "server": "tickets", "tools": ["sla_scan"],
     "role": "Flags at-risk and breached tickets, ages priority."},
    {"agent": "ReportAgent", "nodes": ["report"], "server": "tickets + comms", "tools": ["queue_summary", "notification_stats"],
     "role": "Final queue and delivery summary."},
]


def agent_catalog() -> list[dict]:
    return AGENTS
