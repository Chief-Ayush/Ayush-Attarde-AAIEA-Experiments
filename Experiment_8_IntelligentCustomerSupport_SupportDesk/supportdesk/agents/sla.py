"""SLAAgent: scans every open ticket for at-risk and breached deadlines and applies priority aging (tickets server: sla_scan)."""
from __future__ import annotations

import time

from .base import EMPTY_DISPATCH, HUMAN_STATUSES, Runner, StageError, State, agent, fail, timed


@agent("SLAAgent")
def make(run: Runner):
    s = run.s  # noqa: F841

    def sla_check(state: State) -> dict:
        t0 = time.perf_counter()
        r = run.call("sla_scan", {"actor": state["actor"], "apply_aging": s.aging, "trace_id": state["trace_id"]}, must=False, phase=10)
        return {"sla": r.data if r.ok else {}, "timings": timed(state, "sla", t0)}


    return sla_check
