"""LangGraph workflow: ingest -> validate -> import -> score -> draft notifications -> HUMAN REVIEW (interrupt) -> apply decisions -> dispatch -> report.

Every step is an MCP tool call made through the hub (traced as TRACE/TASK ids).  The graph pauses at `human_review`; the decisions that resume it are
recorded through the records server, so reviewer identity, role, notes, override reasons and message approval are enforced server-side.
The same post-review step (`finalize_drive`) is used by the dashboard, where reviewers decide one candidate at a time.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Callable

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict

from .config import Settings, load_settings
from .mcp_client import MCPHub, ToolResult, get_hub
from .tracing import LOG, Trace

SENDABLE = {"SHORTLIST_REVIEW", "MANUAL_REVIEW", "NOT_SHORTLISTED_REVIEW", "AWAITING_INFORMATION"}


class State(TypedDict, total=False):
    drive_id: str
    source: str
    actor: str
    run_id: str
    trace_id: str
    inspect: dict
    raw_count: int
    records: list
    valid: list
    rejected: list
    skipped: list
    counts: dict
    results: list
    failed_scoring: list
    drafts_created: int
    draft_problems: list
    decisions: list
    finalize: dict
    report: dict
    timings: dict
    errors: list
    status: str


class StageError(Exception):
    pass


class Runner:
    """Thin wrapper that makes traced, retried MCP calls and chunks work according to the execution mode (sequential | batch | parallel)."""

    def __init__(self, hub: MCPHub | None = None, settings: Settings | None = None, trace: Trace | None = None, agent: str = "workflow"):
        self.hub = hub or get_hub()
        self.s = settings or load_settings()
        self.trace = trace or Trace()
        self.agent = agent
        self.calls = 0
        self.failures: list[ToolResult] = []

    def call(self, tool: str, args: dict, *, must: bool = True, phase: int | None = None) -> ToolResult:
        r = self.hub.call(tool, args, settings=self.s, trace=self.trace, phase=phase, agent=self.agent)
        self.calls += 1
        if not r.ok:
            self.failures.append(r)
            if must:
                raise StageError(f"{tool}: {r.error} [{r.category}]")
        return r

    def many(self, tool: str, arg_list: list[dict], *, phase: int | None = None) -> list[ToolResult]:
        """Run one tool over several argument sets: sequentially, or concurrently in parallel mode."""
        if self.s.execution_mode != "parallel" or len(arg_list) < 2:
            return [self.call(tool, a, must=False, phase=phase) for a in arg_list]
        async def go():
            sem = asyncio.Semaphore(self.s.max_parallel)

            async def one(a):
                async with sem:
                    return await self.hub.acall(tool, a, settings=self.s, trace=self.trace, phase=phase, agent=self.agent)
            return await asyncio.gather(*(one(a) for a in arg_list))
        out = self.hub.run(go())
        self.calls += len(out)
        self.failures += [r for r in out if not r.ok]
        return out

    def chunks(self, items: list) -> list[list]:
        n = 1 if self.s.execution_mode == "sequential" else max(1, self.s.chunk_size)
        return [items[i:i + n] for i in range(0, len(items), n)]


# --------------------------------------------------------------------------------------------------------------------- stages
def _log(run: Runner, state: State, event: str, detail: str = "") -> None:
    run.call("log_event", {"drive_id": state["drive_id"], "event": event, "actor": state["actor"], "detail": detail, "trace_id": state["trace_id"]}, must=False)


def _timed(state: State, name: str, t0: float) -> dict:
    t = dict(state.get("timings") or {})
    t[name] = round((time.perf_counter() - t0) * 1000, 1)
    return t


def build_graph(run: Runner, checkpointer=None):
    def ingest(state: State) -> dict:
        t0 = time.perf_counter()
        _log(run, state, "workflow_started", f"source={state['source']} mode={run.s.execution_mode} rules={run.s.rule_version or 'active'}")
        try:
            info = run.call("inspect_source", {"path": state["source"]}, phase=1).data
            if info["missing_headers"]:
                raise StageError(f"Required columns missing: {', '.join(info['missing_headers'])}")
            first = run.call("load_source", {"path": state["source"], "offset": 0, "limit": 1000}, phase=1).data
            recs, total = list(first["records"]), first["count"]
            while len(recs) < total:
                recs += run.call("load_source", {"path": state["source"], "offset": len(recs), "limit": 1000}, phase=1).data["records"]
            return {"inspect": info, "raw_count": total, "records": recs, "timings": _timed(state, "ingest", t0), "status": "ingested"}
        except StageError as exc:
            _log(run, state, "workflow_failed", str(exc))
            return {"errors": (state.get("errors") or []) + [str(exc)], "status": "failed", "timings": _timed(state, "ingest", t0)}

    def validate(state: State) -> dict:
        t0 = time.perf_counter()
        recs = state.get("records") or []
        try:
            v = run.call("validate_records", {"records": recs, "drive_id": state["drive_id"]}, phase=2).data
            imp = run.call("import_records", {"drive_id": state["drive_id"], "valid": v["valid"], "rejected": v["rejected"], "actor": state["actor"],
                                              "trace_id": state["trace_id"]}, phase=2).data
            counts = {**v["counts"], "imported": imp["imported"], "repeats": imp["repeats"]}
            return {"records": [], "valid": v["valid"], "rejected": v["rejected"], "skipped": v["skipped"], "counts": counts, "timings": _timed(state, "validate", t0), "status": "validated"}
        except StageError as exc:
            _log(run, state, "workflow_failed", str(exc))
            return {"errors": (state.get("errors") or []) + [str(exc)], "status": "failed", "timings": _timed(state, "validate", t0)}

    def score(state: State) -> dict:
        t0 = time.perf_counter()
        valid = state.get("valid") or []
        ver = run.s.rule_version
        outs = run.many("score_candidates", [{"candidates": ch, "version": ver} for ch in run.chunks(valid)], phase=3)
        results, failed, errs = [], [], []
        for o in outs:
            if o.ok:
                results += o.data["results"]
                failed += o.data["failed"]
            else:
                errs.append(f"score_candidates: {o.error}")
        if results:
            rid = state["run_id"]
            for ch in run.chunks(results):
                r = run.call("save_results", {"drive_id": state["drive_id"], "results": ch, "run_id": rid, "actor": state["actor"], "trace_id": state["trace_id"]}, must=False, phase=3)
                if not r.ok:
                    errs.append(f"save_results: {r.error}")
        return {"results": results, "failed_scoring": failed, "errors": (state.get("errors") or []) + errs, "timings": _timed(state, "score", t0), "status": "scored"}

    def draft(state: State) -> dict:
        t0 = time.perf_counter()
        items = [{"candidate_id": r["candidate_id"], "status": r["proposed_status"]} for r in state.get("results") or []]
        outs = run.many("generate_drafts", [{"drive_id": state["drive_id"], "items": ch, "actor": state["actor"], "trace_id": state["trace_id"]} for ch in run.chunks(items)], phase=4)
        created, problems, errs = 0, [], []
        for o in outs:
            if o.ok:
                created += o.data["created"]
                problems += o.data["problems"]
            else:
                errs.append(f"generate_drafts: {o.error}")
        return {"drafts_created": created, "draft_problems": problems, "errors": (state.get("errors") or []) + errs, "timings": _timed(state, "draft", t0), "status": "awaiting_review"}

    def human_review(state: State) -> dict:
        queue = run.call("get_queue", {"drive_id": state["drive_id"]}, phase=5).data["queue"]
        decisions = interrupt({"drive_id": state["drive_id"], "waiting_for": "reviewer decisions", "candidates": len(queue),
                               "queue": [{"candidate_id": q["candidate_id"], "score": q["score"], "proposed_status": q["proposed_status"]} for q in queue]})
        return {"decisions": decisions or [], "status": "reviewed"}

    def apply_decisions(state: State) -> dict:
        t0 = time.perf_counter()
        errs, approve_for = [], []
        for d in state.get("decisions") or []:
            r = run.call("record_review", {"drive_id": state["drive_id"], "candidate_id": d["candidate_id"], "action": d["action"], "reviewer": d["reviewer"],
                                           "new_status": d.get("new_status", ""), "note": d.get("note", ""), "override_reason": d.get("override_reason", ""),
                                           "trace_id": state["trace_id"]}, must=False, phase=6)
            if not r.ok:
                errs.append(f"{d['candidate_id']}: {r.error}")
            elif d.get("approve_message", True):
                approve_for.append((d["candidate_id"], d["reviewer"]))
        fin = finalize_drive(run, state["drive_id"], state["actor"], approve_for, state["trace_id"])
        return {"finalize": fin, "errors": (state.get("errors") or []) + errs + fin["errors"], "timings": _timed(state, "review_and_dispatch", t0), "status": "dispatched"}

    def report(state: State) -> dict:
        t0 = time.perf_counter()
        rep = run.call("drive_report", {"drive_id": state["drive_id"]}, must=False, phase=7)
        run.call("export_audit", {"actor": state["actor"]}, must=False, phase=7)
        failed = state.get("status") == "failed" or (bool(state.get("valid")) and not state.get("results"))
        status = "failed" if failed else "completed"
        if state.get("status") != "failed":                         # ingest / validate failures were already logged with their reason
            _log(run, state, "workflow_completed" if status == "completed" else "workflow_failed",
                 f"valid={len(state.get('valid') or [])} scored={len(state.get('results') or [])} " + (f" errors={'; '.join((state.get('errors') or [])[:2])}" if state.get("errors") else ""))
        return {"report": rep.data if rep.ok else {}, "status": status, "timings": _timed(state, "report", t0)}

    def after_ingest(state: State) -> str:
        return "report" if state.get("status") == "failed" else "validate"

    def after_validate(state: State) -> str:
        return "score" if state.get("valid") else "report"

    def after_draft(state: State) -> str:
        return "human_review" if state.get("results") else "report"

    g = StateGraph(State)
    for name, fn in (("ingest", ingest), ("validate", validate), ("score", score), ("draft", draft), ("human_review", human_review),
                     ("apply_decisions", apply_decisions), ("report", report)):
        g.add_node(name, fn)
    g.add_edge(START, "ingest")
    g.add_conditional_edges("ingest", after_ingest, {"report": "report", "validate": "validate"})
    g.add_conditional_edges("validate", after_validate, {"score": "score", "report": "report"})
    g.add_edge("score", "draft")
    g.add_conditional_edges("draft", after_draft, {"human_review": "human_review", "report": "report"})
    g.add_edge("human_review", "apply_decisions")
    g.add_edge("apply_decisions", "report")
    g.add_edge("report", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())


# --------------------------------------------------------------------------------------------------------------------- post-review step
def finalize_drive(run: Runner, drive_id: str, actor: str, approve_for: list[tuple[str, str]] | None = None, trace_id: str = "") -> dict:
    """After reviewer decisions: align drafts with the final status, approve the messages the reviewer approved, hold back the rest, then dispatch.

    approve_for: [(candidate_id, reviewer_id)] - message approvals given together with the decisions.  Drafts a reviewer already approved
    (dashboard) are dispatched as they are.  Idempotent: sent messages are never sent again.
    """
    approve = dict(approve_for or [])
    q = run.call("get_queue", {"drive_id": drive_id}, phase=6).data["queue"]
    drafts = {d["candidate_id"]: d for d in run.call("list_drafts", {"drive_id": drive_id}, phase=6).data["drafts"]}
    regen, errors = [], []
    for item in q:
        rv = item.get("review")
        if not rv:
            continue
        final = rv["final_status"]
        want = "REQUEST_INFO" if final == "AWAITING_INFORMATION" else final
        d = drafts.get(item["candidate_id"])
        if final in ("ON_HOLD", "REQUIRES_REVIEW"):
            continue
        if (not d or d["template"] != want) and (not d or d["state"] != "SENT"):
            regen.append({"candidate_id": item["candidate_id"], "status": want})
    if regen:
        for ch in run.chunks(regen):
            r = run.call("generate_drafts", {"drive_id": drive_id, "items": ch, "actor": actor, "trace_id": trace_id}, must=False, phase=6)
            if not r.ok:
                errors.append(f"generate_drafts: {r.error}")
        drafts = {d["candidate_id"]: d for d in run.call("list_drafts", {"drive_id": drive_id}, phase=6).data["drafts"]}
    withheld, approved, sent, failed, refused = 0, 0, 0, 0, 0
    for item in q:
        rv, cid = item.get("review"), item["candidate_id"]
        d = drafts.get(cid)
        if not rv or not d or d["state"] in ("SENT", "FAILED", "NOT_SENT"):
            continue
        if rv["final_status"] in ("ON_HOLD", "REQUIRES_REVIEW"):
            r = run.call("withhold_draft", {"draft_id": d["draft_id"], "reason": f"candidate is {rv['final_status']}", "actor": rv["reviewer"], "trace_id": trace_id}, must=False, phase=6)
            withheld += r.ok
            continue
        if d["state"] == "DRAFT" and cid in approve:
            r = run.call("approve_draft", {"draft_id": d["draft_id"], "actor": approve[cid], "trace_id": trace_id}, must=False, phase=6)
            if r.ok:
                approved += 1
            else:
                errors.append(f"{cid}: {r.error}")
                continue
    drafts = {d["candidate_id"]: d for d in run.call("list_drafts", {"drive_id": drive_id, "state": "APPROVED"}, phase=6).data["drafts"]}
    todo = [d for d in drafts.values()]
    outs = run.many("dispatch_notification", [{"draft_id": d["draft_id"], "actor": actor, "trace_id": trace_id} for d in todo], phase=6)
    for o in outs:
        if not o.ok:
            refused += 1
            errors.append(f"dispatch: {o.error}")
        elif o.data["outcome"] == "SENT":
            sent += 1
        else:
            failed += 1
    return {"approved": approved, "withheld": withheld, "sent": sent, "failed": failed, "refused": refused, "regenerated": len(regen), "errors": errors}


# --------------------------------------------------------------------------------------------------------------------- entry points
def auto_policy(item: dict, reviewer: str) -> dict:
    """Reviewer policy for unattended runs (evaluation harness, console demo): approve the proposal; hold candidates whose free text tried to steer the system."""
    cid = item["candidate_id"]
    if any(f.get("flag") == "TEXT_INSTRUCTION" for f in item.get("flags", [])):
        return {"candidate_id": cid, "action": "HOLD", "reviewer": reviewer, "note": "Free text tried to instruct the system - checked by a person first"}
    if any(f.get("flag") == "SAME_EMAIL" for f in item.get("flags", [])):
        return {"candidate_id": cid, "action": "REQUEST_INFO", "reviewer": reviewer, "note": "Same e-mail as another applicant - ask the candidate to confirm"}
    return {"candidate_id": cid, "action": "APPROVE", "reviewer": reviewer, "approve_message": True}


class Workflow:
    """Start a drive run, pause at the human review step and resume with decisions."""

    def __init__(self, hub: MCPHub | None = None, settings: Settings | None = None):
        self.run = Runner(hub, settings)
        self.graph = build_graph(self.run)
        self.thread: dict | None = None
        self.state: dict = {}

    def start(self, drive_id: str, source: str, actor: str) -> dict:
        self.thread = {"configurable": {"thread_id": uuid.uuid4().hex}}
        init = {"drive_id": drive_id, "source": source, "actor": actor, "run_id": "RUN-" + uuid.uuid4().hex[:6].upper(), "trace_id": self.run.trace.trace_id,
                "timings": {}, "errors": []}
        t0 = time.perf_counter()
        out = self.graph.invoke(init, self.thread)
        self.t_start = t0
        self.state = out
        return self.pending() or out

    def pending(self) -> dict | None:
        snap = self.graph.get_state(self.thread)
        for t in snap.tasks:
            for i in getattr(t, "interrupts", ()) or ():
                return i.value
        return None

    def resume(self, decisions: list[dict]) -> dict:
        out = self.graph.invoke(Command(resume=decisions), self.thread)
        self.state = out
        return out


def run_drive(source: str, actor: str, *, drive_id: str, reviewer: str, settings: Settings | None = None, hub: MCPHub | None = None,
              policy: Callable[[dict, str], dict] | None = None, decisions: list[dict] | None = None) -> dict:
    """Whole workflow in one call (console demo and evaluation).  Returns the final state plus timing and trace information."""
    wf = Workflow(hub, settings)
    t0 = time.perf_counter()
    pend = wf.start(drive_id, source, actor)
    if wf.pending() is not None:
        queue = wf.run.call("get_queue", {"drive_id": drive_id}).data["queue"]
        cand = {c["candidate_id"]: c for c in queue}
        flags = {r["candidate_id"]: r.get("flags", []) for r in queue}
        decs = decisions if decisions is not None else [(policy or auto_policy)({**q, "flags": flags.get(q["candidate_id"], [])}, reviewer) for q in queue]
        final = wf.resume(decs)
    else:
        final = wf.state
    total = (time.perf_counter() - t0) * 1000
    return {"state": final, "total_ms": round(total, 1), "trace": wf.run.trace, "tool_calls": wf.run.calls, "failures": wf.run.failures, "runner": wf.run}
