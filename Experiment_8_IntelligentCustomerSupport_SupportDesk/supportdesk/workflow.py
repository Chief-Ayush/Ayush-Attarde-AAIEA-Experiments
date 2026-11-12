"""Workflow entry points: start a batch, pause at agent review, resume with actions; re-exports the agent plumbing used by the UI and evaluation."""
from __future__ import annotations

import time
import uuid
from typing import Callable

from langgraph.types import Command

from .agents import HUMAN_STATUSES, Runner, State, StageError, agent_for, auto_agent_policy  # noqa: F401
from .config import Settings
from .graph import build_graph
from .mcp import MCPHub


class Workflow:
    """Start a batch run, pause at agent review and resume with actions."""

    def __init__(self, hub: MCPHub | None = None, settings: Settings | None = None):
        self.run = Runner(hub, settings)
        self.graph = build_graph(self.run)
        self.thread: dict | None = None
        self.state: dict = {}

    def start(self, batch_id: str, source: str, actor: str) -> dict:
        self.thread = {"configurable": {"thread_id": uuid.uuid4().hex}}
        init = {"batch_id": batch_id, "source": source, "actor": actor, "run_id": "RUN-" + uuid.uuid4().hex[:6].upper(), "trace_id": self.run.trace.trace_id, "timings": {}, "errors": []}
        out = self.graph.invoke(init, self.thread)
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


def run_batch(source: str, actor: str, *, batch_id: str, settings: Settings | None = None, hub: MCPHub | None = None,
              policy: Callable[[dict], dict] | None = None, decisions: list[dict] | None = None) -> dict:
    """Whole workflow in one call (console demo and evaluation)."""
    wf = Workflow(hub, settings)
    t0 = time.perf_counter()
    wf.start(batch_id, source, actor)
    if wf.pending() is not None:
        q = wf.run.call("list_tickets", {"batch_id": batch_id, "status": HUMAN_STATUSES, "limit": 5000}).data["tickets"]
        q = [t for t in q if not t["first_response_at"]]
        decs = decisions if decisions is not None else [(policy or auto_agent_policy)(t) for t in q]
        final = wf.resume(decs)
    else:
        final = wf.state
    return {"state": final, "total_ms": round((time.perf_counter() - t0) * 1000, 1), "trace": wf.run.trace, "tool_calls": wf.run.calls, "failures": wf.run.failures, "runner": wf.run}

