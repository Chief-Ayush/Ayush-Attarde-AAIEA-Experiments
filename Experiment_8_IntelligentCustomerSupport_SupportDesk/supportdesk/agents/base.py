"""Shared agent plumbing: the workflow State, the Runner that makes traced MCP calls (sequential, batch or parallel) and the agent decorator."""
from __future__ import annotations

import asyncio
import functools
import time

from typing_extensions import TypedDict

from ..config import Settings, load_settings
from ..mcp import MCPHub, ToolResult, get_hub
from ..tracing import LOG, Trace

HUMAN_STATUSES = ["IN_REVIEW", "ESCALATED", "PENDING_APPROVAL", "OPEN", "REOPENED"]
EMPTY_DISPATCH = {"sent": 0, "dead_letter": 0, "retries": 0, "blocked": 0, "already_sent": 0, "attempts": 0, "recovered": 0}

class State(TypedDict, total=False):
    batch_id: str
    source: str
    actor: str
    run_id: str
    trace_id: str
    inspect: dict
    raw_count: int
    records: list
    valid: list
    rejected: list
    counts: dict
    classified: list
    matches: list
    routes: list
    tickets: list
    notify: dict
    dispatch: dict
    decisions: list
    applied: dict
    sla: dict
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



def timed(state: State, name: str, t0: float) -> dict:
    t = dict(state.get("timings") or {})
    t[name] = round((time.perf_counter() - t0) * 1000, 1)
    return t


def fail(state: State, stage: str, exc: Exception, t0: float) -> dict:
    LOG.error("workflow stage %s failed: %s", stage, exc)
    return {"errors": (state.get("errors") or []) + [str(exc)], "status": "failed", "timings": timed(state, stage, t0)}


def agent(name: str):
    """Mark a LangGraph node factory with the agent name (used by the catalog and the UI)."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(run):
            nodes = fn(run)
            for n in (nodes if isinstance(nodes, tuple) else (nodes,)):
                n.agent_name = name
            return nodes
        wrapper.agent_name = name
        return wrapper
    return deco
