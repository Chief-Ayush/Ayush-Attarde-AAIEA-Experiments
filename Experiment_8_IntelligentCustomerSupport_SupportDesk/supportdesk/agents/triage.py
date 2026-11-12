"""TriageAgent: intent, complexity and priority for every valid query (knowledge server: classify_queries)."""
from __future__ import annotations

import time

from .base import EMPTY_DISPATCH, HUMAN_STATUSES, Runner, StageError, State, agent, fail, timed


@agent("TriageAgent")
def make(run: Runner):
    s = run.s  # noqa: F841

    def classify(state: State) -> dict:
        t0 = time.perf_counter()
        items = [{"query_id": q["query_id"], "query_text": q["query_text"]} for q in state.get("valid") or []]
        outs = run.many("classify_queries", [{"queries": ch, "method": s.classifier} for ch in run.chunks(items)], phase=3)
        res, errs = [], []
        for o in outs:
            res += o.data["results"] if o.ok else []
            errs += [] if o.ok else [f"classify_queries: {o.error}"]
        return {"classified": res, "errors": (state.get("errors") or []) + errs, "timings": timed(state, "classify", t0), "status": "classified"}


    return classify
