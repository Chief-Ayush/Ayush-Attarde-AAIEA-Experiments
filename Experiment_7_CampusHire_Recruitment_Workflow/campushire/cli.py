"""Command line: end-to-end console run (lab step 12), demo data and evaluation.

    python -m campushire.cli run [--file candidates.csv] [--mode parallel] [--rules 1.0] [--review auto|none]
    python -m campushire.cli demo
    python -m campushire.cli evaluate
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid

from . import store
from .config import PATHS, Settings, load_settings
from .mcp_client import get_hub, reset_hub
from .workflow import Workflow, auto_policy, run_drive

ADMIN, REVIEWER = "arjun.admin", "priya.reviewer"


def _short(s, n):
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "~"


def new_drive(hub, name: str, drive_id: str | None = None) -> str:
    r = hub.call("create_drive", {"name": name, "actor": ADMIN, **({"drive_id": drive_id} if drive_id else {})}, settings=load_settings())
    if not r.ok:
        raise SystemExit(r.error)
    return r.data["drive_id"]


def print_run(drive_id: str, res: dict) -> None:
    st = res["state"]
    k = st.get("counts") or {}
    print(f"\n=== CampusHire workflow  trace {res['trace'].trace_id}  drive {drive_id} ===")
    print(f"Source: {st.get('source')}   records: {k.get('total', 0)}   accepted: {k.get('valid', 0)}   quarantined: {k.get('rejected', 0)} "
          f"(duplicates {k.get('duplicates', 0)})   already imported: {k.get('skipped', 0)}")
    if st.get("status") == "failed":
        print("WORKFLOW FAILED:", "; ".join(st.get("errors") or []))
        return
    q = store.queue(drive_id)
    print(f"\n{'ID':<7}{'Name':<22}{'Role':<26}{'Score':>7}  {'Proposed':<24}{'Decision':<24}{'Message':<9}")
    print("-" * 121)
    for x in q:
        print(f"{x['candidate_id']:<7}{_short(x['candidate_name'], 21):<22}{_short(x['role'], 25):<26}{x['score']:>7.2f}  {x['proposed_status']:<24}"
              f"{(x['final_status'] or '-'):<24}{x['notification_state']:<9}")
    rej = store.rejected(drive_id)
    if rej:
        print(f"\nQuarantined ({len(rej)}) - saved in rejected_records.csv:")
        for r in rej:
            print(f"  row {r['row']:>3}  {r['candidate_id'] or '(no id)':<8}{r['code']:<20}{_short(r['reason'], 70)}")
    rep = st.get("report") or {}
    print("\n--- Summary ---")
    print(f"Proposed:       {rep.get('proposed')}")
    print(f"Final:          {rep.get('final')}")
    print(f"Messages:       {rep.get('notifications')}   delivery success rate: {rep.get('delivery_success_rate')}")
    print(f"Review:         {rep.get('reviewed')} of {rep.get('scored')} decided ({rep.get('review_completion', 0):.0%}); overrides: {len(rep.get('overrides', []))}")
    print(f"Time:           {res['total_ms'] / 1000:.2f} s, {res['tool_calls']} MCP calls, {len(res['failures'])} failed call(s); per step (ms): {st.get('timings')}")
    print(f"Audit chain:    {store.verify_audit_chain()}")


def cmd_run(a) -> None:
    hub = get_hub()
    s = load_settings().with_(execution_mode=a.mode, rule_version=a.rules or "")
    did = new_drive(hub, f"Console run {time.strftime('%Y-%m-%d %H:%M')}", f"CONSOLE-{time.strftime('%H%M%S')}")
    if a.review == "none":
        wf = Workflow(hub, s)
        wf.start(did, a.file, REVIEWER)
        res = {"state": wf.state, "trace": wf.run.trace, "total_ms": 0, "tool_calls": wf.run.calls, "failures": wf.run.failures}
        print("Paused for human review (no decisions given).")
    else:
        res = run_drive(a.file, REVIEWER, drive_id=did, reviewer=REVIEWER, settings=s, hub=hub)
    print_run(did, res)
    print("\n", store.export_audit_csv(), "-> audit_log.csv")
    reset_hub()


def cmd_demo(a) -> None:
    """Seed a drive for the dashboard: 120 records, part of the queue decided, one override, one hold, one information request."""
    hub = get_hub()
    s = load_settings().with_(execution_mode="parallel", rule_version="")
    did = new_drive(hub, "Campus drive 2026", "DEMO-2026")
    wf = Workflow(hub, s)
    wf.start(did, "candidates.csv", REVIEWER)
    q = store.queue(did)
    flags = {r["candidate_id"]: r["flags"] for r in store.results(did)}
    decs = []
    for x in q:
        item = {**x, "flags": flags.get(x["candidate_id"], [])}
        if x["proposed_status"] == "NOT_SHORTLISTED_REVIEW":
            continue                                                  # left for the reviewers
        decs.append(auto_policy(item, REVIEWER))
    over = next(d for d in decs if d["action"] == "APPROVE" and next(x for x in q if x["candidate_id"] == d["candidate_id"])["proposed_status"] == "MANUAL_REVIEW"
                and next(x for x in q if x["candidate_id"] == d["candidate_id"])["score"] >= 70)
    over.update(action="EDIT", new_status="SHORTLIST_REVIEW", override_reason="Strong project portfolio reviewed in person", approve_message=True)
    decs = decs[: int(len(decs) * 0.75)] if a.partial else decs
    if over not in decs:
        decs.append(over)
    wf.resume(decs)
    print(f"Demo drive {did} ready: {len(decs)} of {len(q)} candidates decided.")
    print(store.export_audit_csv())
    reset_hub()


def cmd_eval(a) -> None:
    from .evaluation.runner import run_all
    rep = run_all(progress=lambda f, m: print(f"{f:5.0%}  {m}", flush=True))
    h = rep["headline"]
    print("\nQuality index:", h["quality_index"])
    for k in ("data_validation_rate_pct", "workflow_success_rate_pct", "notification_draft_rate_pct", "test_pass_rate_pct", "avg_time_per_valid_record_ms",
              "human_review_completion_rate_pct", "routing_accuracy_pct", "security_pass_pct", "failure_pass_pct", "hitl_pass_pct", "functional_pass_pct", "hard_pass_pct"):
        print(f"  {k:<36}{h.get(k)}")
    print("Report:", rep["report_dir"])
    reset_hub()


def cmd_export(a) -> None:
    """results/observation_table.csv (per-candidate table of the latest console run) and results/testing_summary.csv (all test cases of the latest evaluation)."""
    import csv
    import json
    out = PATHS.root / "results"
    out.mkdir(exist_ok=True)
    drives = [d for d in store.list_drives() if d["drive_id"].startswith("CONSOLE-")]
    if drives:
        did = drives[0]["drive_id"]
        q = store.queue(did)
        with open(out / "observation_table.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["candidate_id", "name", "role", "skills", "experience", "assessment", "score", "rule_version", "proposed_status", "reviewer_action", "final_status",
                        "message_state", "criterion_explanation"])
            cands = {c["candidate_id"]: c for c in store.candidates(did)}
            for x in sorted(q, key=lambda r: r["candidate_id"]):
                c = cands[x["candidate_id"]]
                w.writerow([x["candidate_id"], x["candidate_name"], x["role"], c["skills_score"], c["experience_score"], c["assessment_score"], x["score"], x["rule_version"],
                            x["proposed_status"], x["reviewer_action"] or "", x["final_status"] or "", x["notification_state"],
                            "; ".join(f"{p['label']} {p['contribution']:g}/{p['max']:g}" for p in x["breakdown"])])
        print("observation_table.csv:", len(q), "rows from", did)
    rep = PATHS.reports / "latest_report.json"
    if rep.exists():
        r = json.loads(rep.read_text(encoding="utf-8"))
        rows = []
        for sec in ("functional", "hitl", "security", "failure", "hard"):
            for t in r["tables"].get(sec, []):
                rows.append({"suite": sec, **{k: t[k] for k in ("id", "category", "test", "expected", "observed", "result")}})
        pd_cols = ["suite", "id", "category", "test", "expected", "observed", "result"]
        with open(out / "testing_summary.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=pd_cols)
            w.writeheader()
            w.writerows(rows)
        (out / "evaluation_headline.json").write_text(json.dumps(r["headline"], indent=1), encoding="utf-8")
        print("testing_summary.csv:", len(rows), "cases")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="campushire")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="end-to-end console run")
    r.add_argument("--file", default="candidates.csv")
    r.add_argument("--mode", choices=["sequential", "batch", "parallel"], default="parallel")
    r.add_argument("--rules", default="", help="rule version, e.g. 1.0 or 1.1 (default: active)")
    r.add_argument("--review", choices=["auto", "none"], default="auto", help="auto = demo reviewer policy, none = stop at the review step")
    d = sub.add_parser("demo", help="seed a demo drive for the dashboard")
    d.add_argument("--partial", action="store_true", default=True)
    sub.add_parser("evaluate", help="run the full evaluation")
    sub.add_parser("export", help="write results/observation_table.csv and results/testing_summary.csv")
    a = p.parse_args(argv)
    {"run": cmd_run, "demo": cmd_demo, "evaluate": cmd_eval, "export": cmd_export}[a.cmd](a)


if __name__ == "__main__":
    main()
