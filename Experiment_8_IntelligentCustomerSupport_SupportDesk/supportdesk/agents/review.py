"""ReviewAgent: pauses the graph for the human agents (interrupt) and applies their decisions through the tickets and comms servers."""
from __future__ import annotations

import time

from langgraph.types import interrupt

from .base import EMPTY_DISPATCH, HUMAN_STATUSES, Runner, StageError, State, agent, fail, timed


@agent("ReviewAgent")
def make(run: Runner):
    s = run.s  # noqa: F841

    def agent_review(state: State) -> dict:
        q = run.call("list_tickets", {"batch_id": state["batch_id"], "status": HUMAN_STATUSES, "limit": 5000}, phase=8).data["tickets"]
        q = [t for t in q if not t["first_response_at"]]
        decisions = interrupt({"batch_id": state["batch_id"], "waiting_for": "agent actions", "tickets": len(q),
                               "queue": [{"ticket_id": t["ticket_id"], "priority": t["priority"], "team": t["assigned_team"], "status": t["status"]} for t in q]})
        return {"decisions": decisions or [], "status": "reviewed"}

    def apply_actions(state: State) -> dict:
        t0 = time.perf_counter()
        errs, done = [], []
        for d in state.get("decisions") or []:
            r = run.call("review_ticket", {"ticket_id": d["ticket_id"], "action": d["action"], "actor": d["actor"], "note": d.get("note", ""),
                                           "edited_response": d.get("edited_response", ""), "team": d.get("team", ""), "trace_id": state["trace_id"]}, must=False, phase=9)
            if r.ok:
                done.append(d["ticket_id"])
            else:
                errs.append(f"{d['ticket_id']}: {r.error}")
        disp = {"sent": 0, "dead_letter": 0, "retries": 0, "blocked": 0, "already_sent": 0, "attempts": 0, "recovered": 0}
        for o in run.many("dispatch_pending", [{"actor": state["actor"], "ticket_ids": ch, "max_attempts": s.notification_attempts, "trace_id": state["trace_id"]} for ch in run.chunks(done)], phase=9):
            if o.ok:
                for k in disp:
                    disp[k] += o.data.get(k, 0)
            else:
                errs.append(f"dispatch_pending: {o.error}")
        return {"applied": {"actions": len(done), "dispatch": disp}, "errors": (state.get("errors") or []) + errs, "timings": timed(state, "review_and_dispatch", t0), "status": "dispatched"}


    return agent_review, apply_actions
