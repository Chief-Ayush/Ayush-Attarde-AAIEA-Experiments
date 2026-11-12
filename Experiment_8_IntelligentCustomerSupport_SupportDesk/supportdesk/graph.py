"""LangGraph wiring: the agents' nodes are connected into the support workflow (conditional edges skip to the report when nothing can proceed)."""
from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .agents import (Runner, State, intake, knowledge, notifier, reporter, review, router, sla, ticketing, triage)


def build_graph(run: Runner, checkpointer=None):
    ingest, validate = intake.make(run)
    classify = triage.make(run)
    retrieve = knowledge.make(run)
    route = router.make(run)
    make_tickets = ticketing.make(run)
    notify = notifier.make(run)
    agent_review, apply_actions = review.make(run)
    sla_check = sla.make(run)
    report = reporter.make(run)

    g = StateGraph(State)
    for name, fn in (("ingest", ingest), ("validate", validate), ("classify", classify), ("retrieve", retrieve), ("route", route), ("make_tickets", make_tickets),
                     ("notify", notify), ("agent_review", agent_review), ("apply_actions", apply_actions), ("sla_check", sla_check), ("report", report)):
        g.add_node(name, fn)
    g.add_edge(START, "ingest")
    g.add_conditional_edges("ingest", lambda st: "report" if st.get("status") == "failed" else "validate", {"report": "report", "validate": "validate"})
    g.add_conditional_edges("validate", lambda st: "classify" if st.get("valid") else "report", {"classify": "classify", "report": "report"})
    g.add_edge("classify", "retrieve")
    g.add_edge("retrieve", "route")
    g.add_edge("route", "make_tickets")
    g.add_conditional_edges("make_tickets", lambda st: "notify" if st.get("tickets") else "report", {"notify": "notify", "report": "report"})
    g.add_edge("notify", "agent_review")
    g.add_edge("agent_review", "apply_actions")
    g.add_edge("apply_actions", "sla_check")
    g.add_edge("sla_check", "report")
    g.add_edge("report", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())
