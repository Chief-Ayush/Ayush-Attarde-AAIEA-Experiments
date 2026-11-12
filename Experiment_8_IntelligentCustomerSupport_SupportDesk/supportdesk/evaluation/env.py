"""Shared helpers for the evaluation suites.  Every case drives the real system through the MCP servers or the LangGraph workflow in its own batch."""
from __future__ import annotations

import csv
import json
import time
import traceback
import uuid
from contextlib import contextmanager

import pandas as pd

from .. import store
from ..config import PATHS, Settings
from ..mcp import MCPHub, get_hub
from ..workflow import HUMAN_STATUSES, Runner, Workflow, auto_agent_policy, run_batch
from .metrics import pct

ADMIN, LEAD, VIEWER = "arjun.admin", "sana.lead", "meera.viewer"
BILLING, TECH, ACCESS, FEEDBACK, TIER1 = "riya.agent", "dev.agent", "isha.agent", "karan.agent", "tara.agent"
FIELDS = ["query_id", "customer_id", "query_text", "created_at"]


class Env:
    def __init__(self, hub: MCPHub | None = None):
        self.hub = hub or get_hub()
        self.s = Settings()

    def call(self, tool, args=None, s=None):
        return self.hub.call(tool, args or {}, settings=s or self.s)

    def tag(self) -> str:
        return uuid.uuid4().hex[:5].upper()

    def batch(self, kind="x") -> str:
        return f"EVAL-{kind}-{self.tag()}"

    def write(self, records: list[dict], fmt="csv") -> str:
        p = PATHS.uploads / f"eval_{uuid.uuid4().hex[:8]}.{fmt}"
        if fmt == "json":
            p.write_text(json.dumps(records), encoding="utf-8")
        else:
            with open(p, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
                w.writeheader()
                w.writerows(records)
        return p.relative_to(PATHS.root).as_posix()

    def q(self, text: str, i: int = 1, cust: str | None = None, pre: str = "T") -> dict:
        return {"query_id": f"{pre}{self.tag()}{i}", "customer_id": cust or f"C{abs(hash(text)) % 90000 + 10000}", "query_text": text, "created_at": "2026-10-22T09:00:00"}

    def run(self, records, kind="r", settings=None, fmt="csv", **kw):
        bid = self.batch(kind)
        res = run_batch(self.write(records, fmt), ADMIN, batch_id=bid, settings=settings or self.s, hub=self.hub, **kw)
        return bid, res

    def ingest_only(self, records, kind="i"):
        """Import + classify + route + create tickets + notify, stopping at the agent-review interrupt."""
        bid = self.batch(kind)
        wf = Workflow(self.hub, self.s)
        wf.start(bid, self.write(records), ADMIN)
        return wf, bid

    def tickets(self, bid: str) -> list[dict]:
        r = self.call("list_tickets", {"batch_id": bid, "limit": 5000})
        return r.data["tickets"] if r.ok else []

    def ticket(self, bid: str, n: int = 0) -> dict:
        return sorted(self.tickets(bid), key=lambda t: t["created_at"])[n]

    def one(self, text: str, kind="o", settings=None, cust=None):
        """Run a single query through the whole workflow with the standard agent policy; returns (batch, ticket)."""
        bid, res = self.run([self.q(text, cust=cust)], kind, settings)
        ts = self.tickets(bid)
        return bid, (ts[0] if ts else None)

    def detail(self, ticket_id: str) -> dict:
        return self.call("get_ticket", {"ticket_id": ticket_id}).data


class Suite:
    def __init__(self, name: str):
        self.name, self.rows = name, []

    def case(self, id: str, category: str, test: str, expected: str):
        def deco(fn):
            t0 = time.perf_counter()
            try:
                ok, observed, detail = fn()
            except Exception as exc:  # noqa: BLE001
                ok, observed, detail = False, f"error: {type(exc).__name__}: {exc}", "".join(traceback.format_exc().splitlines(True)[-3:])[:300]
            self.rows.append({"id": id, "category": category, "test": test, "expected": expected, "observed": observed, "result": "PASS" if ok else "FAIL",
                              "detail": detail, "ms": round((time.perf_counter() - t0) * 1000, 1)})
            return fn
        return deco

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows)


def pass_summary(df: pd.DataFrame) -> dict:
    n, p = len(df), int((df["result"] == "PASS").sum())
    out = {"tests": n, "passed": p, "failed": n - p, "pass_rate_pct": pct(p, n)}
    if "category" in df:
        out["by_category"] = {k: round(100 * float((g["result"] == "PASS").mean()), 1) for k, g in df.groupby("category")}
    return out


@contextmanager
def temp_file(path, text: str | None):
    """Temporarily replace a project file (None = remove it); always restored."""
    keep = path.read_text(encoding="utf-8")
    try:
        if text is None:
            path.unlink()
        else:
            path.write_text(text, encoding="utf-8")
        yield
    finally:
        path.write_text(keep, encoding="utf-8")
