"""Reliability suite: MCP failure categories, retries with backoff, idempotency, dead-letter queue, concurrency and SLA pause accounting."""
from __future__ import annotations

import asyncio
import json
import time

import pandas as pd

import notifications as nt
import sla_monitor as sla
import ticket_manager as tm
import workflow_router as wr

from .. import store
from ..config import PATHS, Settings, load_policy
from ..mcp import MCPHub
from .env import ADMIN, BILLING, LEAD, TECH, TIER1, Env, Suite, temp_file
from .suites import _evt, _waiting


class faults:
    """Context manager: inject faults for the given tools, always cleared afterwards."""
    def __init__(self, spec: dict):
        self.spec = spec

    def __enter__(self):
        MCPHub.set_faults(self.spec)

    def __exit__(self, *a):
        MCPHub.set_faults(None)


def run_reliability(env: Env) -> pd.DataFrame:
    S = Suite("reliability")
    pol = load_policy()
    cl = [{"query_id": "r1", "query_text": "How do I cancel my subscription?"}]

    @S.case("R01", "mcp", "Transient failure inside a tool (once)", "Controlled retry recovers; the caller sees success after 2 attempts")
    def _():
        with faults({"classify_queries": {"kind": "transient", "count": 1}}):
            r = env.call("classify_queries", {"queries": cl})
        return r.ok and r.attempts == 2, f"ok={r.ok} attempts={r.attempts}", ""

    @S.case("R02", "mcp", "Same failure with retries switched off", "The failure is reported, not hidden")
    def _():
        with faults({"classify_queries": {"kind": "transient", "count": 1}}):
            r = env.call("classify_queries", {"queries": cl}, s=Settings(retry_policy="none"))
        return (not r.ok) and r.category == "EXECUTION" and r.attempts == 1, f"ok={r.ok} category={r.category}", ""

    @S.case("R03", "mcp", "Tool returns an error result (TOOL category)", "Not retried; reported once")
    def _():
        with faults({"classify_queries": {"kind": "tool_error", "count": 3}}):
            r = env.call("classify_queries", {"queries": cl})
        return (not r.ok) and r.category == "TOOL" and r.attempts == 1, f"category={r.category} attempts={r.attempts}", ""

    @S.case("R04", "mcp", "Tool answers too slowly", "TIMEOUT category after the configured limit, then limited retry")
    def _():
        with faults({"classify_queries": {"kind": "slow", "count": 3, "delay_s": 2.0}}):
            t0 = time.perf_counter()
            r = env.call("classify_queries", {"queries": cl}, s=Settings(tool_timeout_s=0.5, max_retries=1, retry_backoff_s=0.01))
            dt = time.perf_counter() - t0
        time.sleep(4.5)                                            # let the slow server threads finish before the next case
        return (not r.ok) and r.category == "TIMEOUT" and r.attempts == 2, f"category={r.category} attempts={r.attempts} after {dt:.1f}s", ""

    @S.case("R05", "mcp", "Server process crashes during a call", "Reconnect and retry; call succeeds")
    def _():
        with faults({"classify_queries": {"kind": "crash", "count": 1}}):
            r = env.call("classify_queries", {"queries": cl})
        r2 = env.call("classify_queries", {"queries": cl})
        return r.ok and r2.ok, f"after crash: ok={r.ok} attempts={r.attempts}; next call ok={r2.ok}", ""

    @S.case("R06", "mcp", "Arguments that do not match the tool's schema", "Rejected before sending (INPUT)")
    def _():
        r = env.call("classify_queries", {"queries": "not a list"})
        r2 = env.call("no_such_tool", {})
        return (not r.ok) and r.category == "INPUT" and r.attempts == 0 and r2.category == "INPUT", f"{r.category} / unknown tool {r2.category}", ""

    @S.case("R07", "workflow", "Knowledge search keeps failing inside a batch", "Run completes; nothing is auto-answered without grounding; errors recorded")
    def _():
        recs = [env.q("How do I cancel my subscription?", 1, "C9101"), env.q("Which browsers can I use with TaskNest?", 2, "C9102")]
        with faults({"search_knowledge": {"kind": "tool_error", "count": 50}}):
            bid, res = env.run(recs, "f1")
        ts = env.tickets(bid)
        auto = [n for t in ts for n in store.list_notifications(t["ticket_id"]) if n["event"] == "RESPONSE" and n["state"] == "SENT"]
        st = res["state"]
        return len(ts) == 2 and not auto and bool(st["errors"]) and all(t["article_id"] is None for t in ts), f"tickets={len(ts)} auto-answers sent={len(auto)} errors={len(st['errors'])}", st["errors"][0][:80] if st["errors"] else ""

    @S.case("R08", "workflow", "Ticket creation fails, then the same file is run again", "First run fails cleanly; the repeat creates the tickets with no duplicates")
    def _():
        recs = [env.q("Which browsers can I use with TaskNest?", 1, "C9111"), env.q("Is there an Android app?", 2, "C9112")]
        path = env.write(recs)
        from ..workflow import run_batch
        bid = env.batch("f2")
        with faults({"create_tickets": {"kind": "tool_error", "count": 5}}):
            r1 = run_batch(path, ADMIN, batch_id=bid, settings=env.s, hub=env.hub)
        n1 = len(env.tickets(bid))
        r2 = run_batch(path, ADMIN, batch_id=bid, settings=env.s, hub=env.hub)
        n2 = len(env.tickets(bid))
        return r1["state"]["status"] == "failed" and n1 == 0 and r2["state"]["status"] == "completed" and n2 == 2, f"run 1: {r1['state']['status']} ({n1} tickets); run 2: {r2['state']['status']} ({n2} tickets, imported {r2['state']['counts']['imported']}, repeats {r2['state']['counts']['repeats']})", ""

    @S.case("R09", "mcp", "Dispatch call fails once before sending", "Retried by the client; each message sent exactly once")
    def _():
        t = _waiting(env, "How do I change the credit card used for our subscription?", "C9121")
        with faults({"dispatch_pending": {"kind": "transient", "count": 1}}):
            r = env.call("dispatch_pending", {"actor": ADMIN, "ticket_ids": [t["ticket_id"]]})
        ns = store.list_notifications(t["ticket_id"])
        again = env.call("dispatch_pending", {"actor": ADMIN, "ticket_ids": [t["ticket_id"]]}).data
        sent = [n for n in ns if n["state"] == "SENT"]
        return r.ok and r.attempts == 2 and all(int(n["attempts"]) == 1 for n in sent) and again["sent"] == 0, f"attempts={r.attempts}; messages sent={len(sent)}, each once; a further dispatch sent {again['sent']}", ""

    def with_customer(cust: str):
        t = _waiting(env, "How do I change the credit card used for our subscription?", cust)
        env.call("dispatch_pending", {"actor": ADMIN, "ticket_ids": [t["ticket_id"]], "max_attempts": 3})
        return t, {n["event"]: n for n in store.list_notifications(t["ticket_id"])}

    @S.case("R10", "notifications", "Permanent failure (mailbox does not exist)", "Not retried; dead-letter queue after one attempt")
    def _():
        t, ns = with_customer("BOUNCE9")
        n = ns["CREATED"]
        return n["state"] == "DEAD_LETTER" and n["attempts"] == 1, f"{n['state']} attempts={n['attempts']}", n["detail"]

    @S.case("R11", "notifications", "Rate-limited provider (429 on first attempt)", "Delivered on the second attempt")
    def _():
        t, ns = with_customer("RATELIMIT1")
        n = ns["CREATED"]
        return n["state"] == "SENT" and n["attempts"] == 2, f"{n['state']} attempts={n['attempts']}", ""

    @S.case("R12", "notifications", "Agent tries to requeue a dead letter; a lead does it with a corrected address", "Agent refused; lead succeeds and the message is delivered")
    def _():
        t, ns = with_customer("BOUNCE8")
        nid = ns["CREATED"]["notification_id"]
        r1 = env.call("requeue_dead_letter", {"notification_id": nid, "actor": BILLING})
        r2 = env.call("requeue_dead_letter", {"notification_id": nid, "actor": LEAD, "to_addr": "bounce8@customers.example"})
        env.call("dispatch_pending", {"actor": ADMIN, "ticket_ids": [t["ticket_id"]]})
        n = store.get_notification(nid)
        return (not r1.ok) and r2.ok and n["state"] == "SENT", f"agent refused={not r1.ok}; lead ok={r2.ok}; final state {n['state']}", ""

    @S.case("R13", "notifications", "Backoff between attempts", "Waits double each time (0.5 s, 1 s)")
    def _():
        waits = []
        res = nt.send_with_retry("flaky5@customers.example", 3, 0.5, sleep=lambda s: waits.append(s))
        return waits == [0.5, 1.0] and res["status"] == "DEAD_LETTER", f"waits={waits} final={res['status']} attempts={len(res['attempts'])}", "sleep is replaced by a recorder so the test is instant"

    @S.case("R14", "notifications", "Idempotency key", "Same event, address and text give the same key; any change gives another")
    def _():
        a = nt.idempotency_key("TKT-1", "CREATED", "a@b.example", "hello")
        b = nt.idempotency_key("TKT-1", "CREATED", "a@b.example", "hello")
        c = nt.idempotency_key("TKT-1", "CREATED", "a@b.example", "hello!")
        d = nt.idempotency_key("TKT-2", "CREATED", "a@b.example", "hello")
        return a == b and a != c and a != d, f"{a} {b == a} {c != a} {d != a}", ""

    @S.case("R15", "concurrency", "Two dispatchers send the same ticket's messages at the same time", "Every message sent exactly once")
    def _():
        t = _waiting(env, "How do I change the credit card used for our subscription?", "C9131")
        args = {"actor": ADMIN, "ticket_ids": [t["ticket_id"]]}

        async def go():
            return await asyncio.gather(*(env.hub.acall("dispatch_pending", args, settings=env.s) for _ in range(4)))
        outs = env.hub.run(go())
        ns = store.list_notifications(t["ticket_id"])
        attempts = [store.notification_attempts(n["notification_id"]) for n in ns]
        sends = sum(1 for a in attempts for x in a if x["result"] == "SENT")
        return all(o.ok for o in outs) and sends == len([n for n in ns if n["state"] == "SENT"]) and all(len(a) <= 1 for a in attempts), f"4 concurrent dispatchers: {sends} sends for {len(ns)} messages", ""

    @S.case("R16", "ingestion", "CSV with only a header row", "Run completes with zero tickets and no error")
    def _():
        bid, res = env.run([], "e")
        return res["state"]["status"] == "completed" and not env.tickets(bid) and not res["state"]["errors"], f"{res['state']['status']}, tickets=0, errors={len(res['state']['errors'])}", ""

    @S.case("R17", "audit", "audit_log.csv matches the database", "Every database audit record appears once in the CSV export")
    def _():
        import csv
        rows = list(csv.DictReader(open(PATHS.audit_csv, encoding="utf-8")))
        with store.tx() as con:
            n = con.execute("SELECT COUNT(*) c FROM audit").fetchone()["c"]
        ids = [r["event_id"] for r in rows]
        return len(rows) == n and len(set(ids)) == len(ids), f"database {n}, csv {len(rows)}, unique ids {len(set(ids))}", ""

    @S.case("R18", "sla", "SLA clock pauses while the ticket waits for the customer", "No breach during the pause; time used afterwards excludes it")
    def _():
        t = _waiting(env, "I cannot log in to the workspace and my deadline is today.", "C9141")
        win = pol["sla_minutes"][t["base_priority"]]
        try:
            r = env.call("review_ticket", {"ticket_id": t["ticket_id"], "action": "REQUEST_INFORMATION", "actor": env_agent(t), "note": "which workspace?"})
            env.call("advance_clock", {"minutes": win * 6, "actor": ADMIN})
            scan = env.call("sla_scan", {"apply_aging": False}).data
            during = env.detail(t["ticket_id"])["first_response"]
            env.call("review_ticket", {"ticket_id": t["ticket_id"], "action": "EDIT", "actor": env_agent(t), "edited_response": "Thanks, here is what to try."})
            after = env.detail(t["ticket_id"])["first_response"]
        finally:
            env.call("reset_clock", {"actor": ADMIN})
        breached = [a for a in scan["new_alerts"] if a["ticket_id"] == t["ticket_id"]]
        return r.ok and during["state"] == "PAUSED" and not breached and after["used_min"] < win, f"paused for {win * 6:.0f} min: state {during['state']}, alerts {len(breached)}; used after resuming {after['used_min']} of {win} min", ""

    @S.case("R19", "sla", "SLA deadlines follow the priority", "first_response_due - created_at equals the configured minutes for P1..P4")
    def _():
        texts = ["The application is down for all our users.", "I cannot log in to the workspace and my deadline is today.", "Google Calendar events are not syncing since Monday.", "Please add a Gantt chart view."]
        wf, bid = env.ingest_only([env.q(x, i, f"C93{i}") for i, x in enumerate(texts)], "sl")
        out, ok = [], True
        for t in env.tickets(bid):
            full = store.get_ticket(t["ticket_id"])
            mins = (tm.parse(full["first_response_due"]) - tm.parse(full["created_at"])).total_seconds() / 60
            good = abs(mins - pol["sla_minutes"][full["base_priority"]]) < 0.01
            ok &= good
            out.append(f"{full['base_priority']}:{mins:.0f}")
        return ok and len({x.split(':')[0] for x in out}) >= 3, ", ".join(sorted(out)), ""

    @S.case("R20", "sla", "SLA alerts are raised once", "A second scan creates no duplicate alert")
    def _():
        t = _waiting(env, "I cannot log in to the workspace and my deadline is today.", "C9151")
        win = pol["sla_minutes"][t["base_priority"]]
        try:
            env.call("advance_clock", {"minutes": win + 5, "actor": ADMIN})
            a = env.call("sla_scan", {"apply_aging": False}).data
            b = env.call("sla_scan", {"apply_aging": False}).data
        finally:
            env.call("reset_clock", {"actor": ADMIN})
        n1 = [x for x in a["new_alerts"] if x["ticket_id"] == t["ticket_id"]]
        n2 = [x for x in b["new_alerts"] if x["ticket_id"] == t["ticket_id"]]
        return bool(n1) and not n2, f"first scan {len(n1)} alert(s), second scan {len(n2)}", ""

    return S.frame()


def env_agent(t: dict) -> str:
    from ..workflow import agent_for
    return agent_for(t["assigned_team"])
