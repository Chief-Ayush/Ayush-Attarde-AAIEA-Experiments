"""RouterAgent: chooses automatic answer, agent review, urgent escalation, product feedback or request for detail (knowledge server: decide_routes)."""
from __future__ import annotations

import time

from .base import EMPTY_DISPATCH, HUMAN_STATUSES, Runner, StageError, State, agent, fail, timed


@agent("RouterAgent")
def make(run: Runner):
    s = run.s  # noqa: F841

    def route(state: State) -> dict:
        t0 = time.perf_counter()
        match = {m["query_id"]: m for m in state.get("matches") or []}
        items = []
        for c in state.get("classified") or []:
            m = match.get(c["query_id"], {})
            items.append({"query_id": c["query_id"], "classification": c, "match_score": m.get("match_score", 0), "article_found": bool(m.get("article_id")), "evidence": m.get("evidence") or {}})
        outs = run.many("decide_routes", [{"items": ch, "guard": s.auto_guard} for ch in run.chunks(items)], phase=5)
        res, errs = [], []
        for o in outs:
            res += o.data["results"] if o.ok else []
            errs += [] if o.ok else [f"decide_routes: {o.error}"]
        return {"routes": res, "errors": (state.get("errors") or []) + errs, "timings": timed(state, "route", t0), "status": "routed"}


    return route
