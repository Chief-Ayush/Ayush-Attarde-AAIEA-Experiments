"""Command line: end-to-end console run, demo data and evaluation.

    python -m supportdesk.cli run [--file customer_queries.csv] [--mode parallel] [--review auto|none]
    python -m supportdesk.cli demo
    python -m supportdesk.cli evaluate
    python -m supportdesk.cli export
"""
from __future__ import annotations

import argparse
import time

from . import store
from .config import PATHS, Settings, load_settings
from .mcp import get_hub, reset_hub
from .workflow import HUMAN_STATUSES, Workflow, auto_agent_policy, run_batch

ACTOR = "riya.agent"


def _short(s, n):
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "~"


def print_run(batch_id: str, res: dict) -> None:
    st = res["state"]
    k = st.get("counts") or {}
    print(f"\n=== TaskNest SupportDesk workflow  trace {res['trace'].trace_id}  batch {batch_id} ===")
    print(f"Source: {st.get('source')}   records: {k.get('total', 0)}   accepted: {k.get('valid', 0)}   rejected: {k.get('rejected', 0)}")
    if st.get("status") == "failed":
        print("WORKFLOW FAILED:", "; ".join(st.get("errors") or []))
        return
    tk = store.list_tickets(batch_id=batch_id, limit=5000)
    print(f"\n{'Ticket':<14}{'Query':<7}{'Intent':<20}{'Cx':<8}{'Pri':<5}{'Route':<34}{'Match':>6}  {'Status':<20}")
    print("-" * 116)
    for t in sorted(tk, key=lambda x: (x["priority"], x["query_id"])):
        print(f"{t['ticket_id']:<14}{t['query_id']:<7}{t['intent']:<20}{t['complexity']:<8}{t['priority']:<5}{t['route']:<34}{float(t['match_score'] or 0):>6.0f}  {t['status']:<20}")
    rej = store.rejected_rows(batch_id)
    if rej:
        print(f"\nRejected ({len(rej)}) - saved in rejected_queries.csv:")
        for r in rej:
            print(f"  row {r['row']:>3}  {r['query_id'] or '(no id)':<8}{r['code']:<18}{_short(r['reason'], 70)}")
    rep = st.get("report") or {}
    print("\n--- Summary ---")
    print(f"Queues:         {(rep.get('queue') or {}).get('by_priority')}")
    print(f"Messages:       {rep.get('notifications')}")
    print(f"Time:           {res['total_ms'] / 1000:.2f} s, {res['tool_calls']} MCP calls, {len(res['failures'])} failed call(s); per step (ms): {st.get('timings')}")
    print(f"Audit chain:    {store.verify_audit_chain()}")


def cmd_run(a) -> None:
    hub = get_hub()
    s = load_settings().with_(execution_mode=a.mode)
    bid = f"CONSOLE-{time.strftime('%H%M%S')}"
    if a.review == "none":
        wf = Workflow(hub, s)
        wf.start(bid, a.file, ACTOR)
        res = {"state": wf.state, "trace": wf.run.trace, "total_ms": 0, "tool_calls": wf.run.calls, "failures": wf.run.failures}
        print("Paused for agent review (no decisions given).")
    else:
        res = run_batch(a.file, ACTOR, batch_id=bid, settings=s, hub=hub)
    print_run(bid, res)
    print("\n", store.export_audit_csv() if hasattr(store, "export_audit_csv") else "", "-> audit_log.csv")
    reset_hub()


def cmd_demo(a) -> None:
    """Seed the dashboard: run the sample queries, let agents decide most tickets, leave a few in the queues."""
    hub = get_hub()
    s = load_settings().with_(execution_mode="parallel")
    bid = "DEMO-" + time.strftime("%m%d")
    wf = Workflow(hub, s)
    wf.start(bid, "customer_queries.csv", ACTOR)
    q = [t for t in wf.run.call("list_tickets", {"batch_id": bid, "status": HUMAN_STATUSES, "limit": 5000}).data["tickets"] if not t["first_response_at"]]
    keep = {t["ticket_id"] for t in q if t["priority"] in ("P1",)}
    decs = [auto_agent_policy(t) for t in q if t["ticket_id"] not in keep][: max(0, len(q) - 4)]
    wf.resume(decs)
    print(f"Demo batch {bid} ready: {len(decs)} of {len(q)} human-queue tickets decided.")
    reset_hub()


def cmd_eval(a) -> None:
    from .evaluation.runner import run_all
    rep = run_all(progress=lambda f, m: print(f"{f:5.0%}  {m}", flush=True))
    h = rep["headline"]
    print("\nQuality index:", h["quality_index"])
    for k in ("intent_accuracy_pct", "priority_accuracy_pct", "retrieval_top1_pct", "safe_route_pct", "auto_answer_precision_pct", "resolution_rate_pct", "notification_success_pct",
              "audit_completeness_pct", "functional_pass_pct", "agent_pass_pct", "security_pass_pct", "reliability_pass_pct", "hard_pass_pct"):
        print(f"  {k:<32}{h.get(k)}")
    print("Report:", rep["report_dir"])
    reset_hub()


def cmd_export(a) -> None:
    """results/testing_summary.csv (all test cases of the latest evaluation) and results/evaluation_headline.json."""
    import csv
    import json
    out = PATHS.root / "results"
    out.mkdir(exist_ok=True)
    rep = PATHS.reports / "latest_report.json"
    if not rep.exists():
        raise SystemExit("Run the evaluation first.")
    r = json.loads(rep.read_text(encoding="utf-8"))
    rows = [{"suite": sec, **{k: t.get(k, "") for k in ("id", "category", "test", "expected", "observed", "result")}} for sec in ("functional", "agent", "security", "reliability", "hard") for t in r["tables"].get(sec, [])]
    with open(out / "testing_summary.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["suite", "id", "category", "test", "expected", "observed", "result"])
        w.writeheader()
        w.writerows(rows)
    (out / "evaluation_headline.json").write_text(json.dumps(r["headline"], indent=1), encoding="utf-8")
    print("testing_summary.csv:", len(rows), "cases")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="supportdesk")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="end-to-end console run")
    r.add_argument("--file", default="customer_queries.csv")
    r.add_argument("--mode", choices=["sequential", "batch", "parallel"], default="parallel")
    r.add_argument("--review", choices=["auto", "none"], default="auto", help="auto = demo agent policy, none = stop at the review pause")
    sub.add_parser("demo", help="seed demo tickets for the dashboard")
    sub.add_parser("evaluate", help="run the full evaluation")
    sub.add_parser("export", help="write results/testing_summary.csv")
    a = p.parse_args(argv)
    {"run": cmd_run, "demo": cmd_demo, "evaluate": cmd_eval, "export": cmd_export}[a.cmd](a)


if __name__ == "__main__":
    main()
