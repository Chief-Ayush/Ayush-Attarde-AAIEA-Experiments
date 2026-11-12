"""IntakeAgent: loads the source file and validates, masks and stores the queries (intake server tools)."""
from __future__ import annotations

import time

from .base import EMPTY_DISPATCH, HUMAN_STATUSES, Runner, StageError, State, agent, fail, timed


@agent("IntakeAgent")
def make(run: Runner):
    s = run.s  # noqa: F841

    def ingest(state: State) -> dict:
        t0 = time.perf_counter()
        try:
            info = run.call("inspect_source", {"path": state["source"]}, phase=1).data
            if info["missing_headers"]:
                raise StageError("Required columns missing: " + ", ".join(info["missing_headers"]))
            first = run.call("load_source", {"path": state["source"], "offset": 0, "limit": 1000}, phase=1).data
            recs, total = list(first["records"]), first["count"]
            while len(recs) < total:
                recs += run.call("load_source", {"path": state["source"], "offset": len(recs), "limit": 1000}, phase=1).data["records"]
            return {"inspect": info, "raw_count": total, "records": recs, "timings": timed(state, "ingest", t0), "status": "ingested"}
        except StageError as exc:
            return fail(state, "ingest", exc, t0)

    def validate(state: State) -> dict:
        t0 = time.perf_counter()
        try:
            v = run.call("validate_queries", {"records": state.get("records") or []}, phase=2).data
            imp = run.call("import_queries", {"batch_id": state["batch_id"], "valid": v["valid"], "rejected": v["rejected"], "actor": state["actor"], "trace_id": state["trace_id"]}, phase=2).data
            return {"records": [], "valid": v["valid"], "rejected": v["rejected"], "counts": {**v["counts"], "imported": imp["imported"], "repeats": imp["repeats"]},
                    "timings": timed(state, "validate", t0), "status": "validated"}
        except StageError as exc:
            return fail(state, "validate", exc, t0)


    return ingest, validate
