"""TicketAgent: creates one ticket per query with team, SLA deadlines and history (tickets server: create_tickets)."""
from __future__ import annotations

import time

from .base import EMPTY_DISPATCH, HUMAN_STATUSES, Runner, StageError, State, agent, fail, timed


@agent("TicketAgent")
def make(run: Runner):
    s = run.s  # noqa: F841

    def make_tickets(state: State) -> dict:
        t0 = time.perf_counter()
        cls = {c["query_id"]: c for c in state.get("classified") or []}
        match = {m["query_id"]: m for m in state.get("matches") or []}
        items = []
        for r in state.get("routes") or []:
            m = match.get(r["query_id"], {})
            items.append({"query_id": r["query_id"], "classification": cls[r["query_id"]], "route": r["route"], "article_id": m.get("article_id"), "match_score": m.get("match_score", 0)})
        outs = run.many("create_tickets", [{"items": ch, "batch_id": state["batch_id"], "actor": state["actor"], "trace_id": state["trace_id"]} for ch in run.chunks(items)], phase=6)
        made, errs = [], []
        for o in outs:
            made += o.data["tickets"] if o.ok else []
            errs += [] if o.ok else [f"create_tickets: {o.error}"]
        return {"tickets": made, "errors": (state.get("errors") or []) + errs, "timings": timed(state, "tickets", t0), "status": "ticketed"}


    return make_tickets
