"""KnowledgeAgent: finds the best approved article and the evidence for it (knowledge server: search_knowledge)."""
from __future__ import annotations

import time

from .base import EMPTY_DISPATCH, HUMAN_STATUSES, Runner, StageError, State, agent, fail, timed


@agent("KnowledgeAgent")
def make(run: Runner):
    s = run.s  # noqa: F841

    def retrieve(state: State) -> dict:
        t0 = time.perf_counter()
        text = {q["query_id"]: q["query_text"] for q in state.get("valid") or []}
        items = [{"query_id": c["query_id"], "query_text": text[c["query_id"]], "intent": c["intent"]} for c in state.get("classified") or []]
        outs = run.many("search_knowledge", [{"items": ch, "method": s.retrieval} for ch in run.chunks(items)], phase=4)
        res, errs = [], []
        for o in outs:
            res += o.data["results"] if o.ok else []
            errs += [] if o.ok else [f"search_knowledge: {o.error}"]
        return {"matches": res, "errors": (state.get("errors") or []) + errs, "timings": timed(state, "retrieve", t0), "status": "retrieved"}


    return retrieve
