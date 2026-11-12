"""ReportAgent: queue and delivery summary for the batch."""
from __future__ import annotations

import time

from .base import EMPTY_DISPATCH, HUMAN_STATUSES, Runner, StageError, State, agent, fail, timed


@agent("ReportAgent")
def make(run: Runner):
    s = run.s  # noqa: F841

    def report(state: State) -> dict:
        t0 = time.perf_counter()
        qs = run.call("queue_summary", {"batch_id": state["batch_id"]}, must=False, phase=11)
        ns = run.call("notification_stats", {"batch_id": state["batch_id"]}, must=False, phase=11)
        ok = state.get("status") != "failed" and not (state.get("valid") and not state.get("tickets"))
        return {"report": {"queue": qs.data if qs.ok else {}, "notifications": ns.data if ns.ok else {}}, "status": "completed" if ok else "failed", "timings": timed(state, "report", t0)}


    return report
