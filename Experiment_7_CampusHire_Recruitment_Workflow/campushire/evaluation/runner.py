"""EvaluationRunner: runs the suites, writes CSV/JSON under storage/reports/run_*/ and keeps latest_report.json."""
from __future__ import annotations

import json
import time
from datetime import datetime

import numpy as np
import pandas as pd

from ..config import PATHS
from . import suites as S
from .. import store
from .metrics import lab_metrics, pct

ALL_SECTIONS = ["functional", "accuracy", "hitl", "security", "failure", "hard", "performance", "analysis"]
SECTION_INFO = {
    "functional": "Functional - the manual's 15 test cases plus input files, validation and rules edge cases",
    "accuracy": "Accuracy - 120 records checked against an independent reference: validation, score, routing, flags, rule version 1.1, repeat uploads",
    "hitl": "Human review - APPROVE / EDIT / HOLD / REQUEST_INFO, override reasons, message approval, dispatch gate, retries, SLA reminders, workflow pause",
    "security": "Security - role-based access, instruction text in applications, path traversal, formula injection, audit tamper evidence, draft content",
    "failure": "Failure handling - fault injection per MCP error category, server crash recovery, partial failures inside the workflow",
    "hard": "Hard cases - messy numbers, roles, e-mails and identifiers, rounding edges, instructions in other languages and encodings, awkward files; known limits stay as failures",
    "performance": "Performance - sequential vs batch vs parallel (3 runs each), repeat consistency, connection reuse",
    "analysis": "Analysis - rule version 1.0 vs 1.1 and a fairness review of the 120-record drive",
}
WEIGHTS = [("validation_accuracy_pct", 0.12), ("score_accuracy_pct", 0.10), ("routing_accuracy_pct", 0.12), ("flag_recall_pct", 0.06),
           ("functional_pass_pct", 0.12), ("hitl_pass_pct", 0.12), ("security_pass_pct", 0.14), ("failure_pass_pct", 0.12), ("workflow_success_rate_pct", 0.10), ("hard_pass_pct", 0.10)]


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def quality_index(h: dict) -> float:
    num = den = 0.0
    for k, w in WEIGHTS:
        if h.get(k) is not None:
            num += w * h[k]
            den += w
    return round(num / den, 1) if den else 0.0


def run_all(sections: list[str] | None = None, progress=None) -> dict:
    sections = [s for s in ALL_SECTIONS if s in (sections or ALL_SECTIONS)]
    t0 = time.time()
    env = S.Env()
    tables, summaries, timings = {}, {}, {}
    n, done = len(sections), 0
    acc_res = None

    def tick(frac, msg):
        if progress:
            progress(min(1.0, (done + frac) / n), msg)

    for sec in sections:
        s0 = time.time()
        tick(0, f"running {sec} ...")
        if sec in ("functional", "hitl", "security", "failure"):
            df = {"functional": S.run_functional, "hitl": S.run_hitl, "security": S.run_security, "failure": S.run_failure}[sec](env)
            tables[sec] = df
            summaries[sec] = S.pass_summary(df)
        elif sec == "hard":
            from .hard import run_hard
            df, sm = run_hard(env)
            tables["hard"] = df
            summaries["hard"] = sm
        elif sec == "accuracy":
            df, sm, acc_res = S.run_accuracy(env)
            tables["accuracy"] = df
            summaries["accuracy"] = sm
        elif sec == "performance":
            df, sm = S.run_performance(env, progress=lambda f, m: tick(f, m))
            tables["performance"] = df
            summaries["performance"] = sm
        elif sec == "analysis":
            a_ = summaries.get("accuracy") or {}
            cmp, fair = a_.get("_compare"), a_.get("_fairness")
            if not cmp:
                did = env.drive("analysis")
                S.run_drive("candidates.csv", S.REVIEWER, drive_id=did, reviewer=S.REVIEWER, hub=env.hub)
                cmp = env.call("compare_rule_versions", {"drive_id": did, "version_a": "1.0", "version_b": "1.1"}).data
                fair = env.call("fairness_review", {"drive_id": did}).data
            tables["rule_moves"] = pd.DataFrame(cmp["moves"])
            tables["fairness"] = pd.DataFrame([{"dimension": d, **g} for d, gs in fair["groups"].items() for g in gs])
            summaries["analysis"] = {"compare": {k: v for k, v in cmp.items() if k != "moves"}, "fairness_flags": fair["flags"], "criteria_notes": fair["criteria_notes"]}
        timings[sec] = round(time.time() - s0, 1)
        done += 1

    h = {}
    a = summaries.get("accuracy", {})
    for k in ("validation_accuracy_pct", "rejection_code_accuracy_pct", "score_accuracy_pct", "routing_accuracy_pct", "rule_v11_routing_accuracy_pct", "flag_recall_pct",
              "flag_false_positives", "boundary_cases_correct_pct", "batch2_repeats_skipped_pct", "batch2_updates_quarantined_pct"):
        h[k] = a.get(k)
    for sec in ("functional", "hitl", "security", "failure"):
        if sec in summaries:
            h[f"{sec}_pass_pct"] = summaries[sec]["pass_rate_pct"]
    hd = summaries.get("hard")
    if hd:
        h["hard_pass_pct"] = hd["pass_rate_pct"]
        h["hard_cases_pass_pct"] = hd["hard_pass_pct"]
        h["hard_controls_pass_pct"] = hd["control_pass_pct"]
        h["hard_flags_found"], h["hard_flags_expected"] = hd["flags_found"], hd["flags_expected"]
        h["hard_flag_false_positives"] = hd["flag_false_positives"]
        if "accuracy" in tables:                                   # accuracy on the 120 reference rows and the hard records together
            ad = tables["accuracy"]
            av = ad[ad["expect_valid"]]
            for k in ("validation_accuracy_pct", "score_accuracy_pct", "routing_accuracy_pct"):
                h["ref120_" + k] = h.get(k)
            h["ref120_rejection_code_accuracy_pct"] = h.get("rejection_code_accuracy_pct")
            h["rejection_code_accuracy_pct"] = pct(int(ad["code_ok"].sum()) + hd["code_ok"], len(ad) + hd["record_cases"])
            h["validation_accuracy_pct"] = pct(int(ad["validation_ok"].sum()) + hd["validation_ok"], len(ad) + hd["record_cases"])
            h["score_accuracy_pct"] = pct(int(av["score_ok"].sum()) + hd["score_ok"], len(av) + hd["expected_valid"])
            h["routing_accuracy_pct"] = pct(int(av["route_ok"].sum()) + hd["route_ok"], len(av) + hd["expected_valid"])
    secs_for_totals = ("functional", "hitl", "security", "failure", "hard")
    totals = [(summaries[s]["passed"], summaries[s]["tests"]) for s in secs_for_totals if s in summaries]
    if acc_res:
        rep = acc_res["state"]["report"]
        scored_ok = rep.get("scored", 0)
        tp, tt = (sum(x[0] for x in totals), sum(x[1] for x in totals)) if totals else (0, 0)
        lm = lab_metrics(rep, acc_res["total_ms"], tp, tt, scored_ok)
        h |= {"lab_" + k: v for k, v in lm.items()}
        h["data_validation_rate_pct"] = lm["data_validation_rate_pct"]
        h["workflow_success_rate_pct"] = lm["workflow_success_rate_pct"]
        h["notification_draft_rate_pct"] = lm["notification_draft_rate_pct"]
        h["test_pass_rate_pct"] = lm["test_pass_rate_pct"]
        h["avg_time_per_valid_record_ms"] = lm["avg_time_per_valid_record_ms"]
        h["human_review_completion_rate_pct"] = lm["human_review_completion_rate_pct"]
        # Stricter reading of the two manual metrics: "successfully processed" = scored, a draft was produced and a sent message did not fail;
        # "review completed" = a decision is recorded and the application is closed (not ON_HOLD or AWAITING_INFORMATION).
        notif = rep.get("notifications", {})
        drafts_n, failed_n = sum(notif.values()), notif.get("FAILED", 0)
        h["workflow_scored_rate_pct"] = lm["workflow_success_rate_pct"]
        h["workflow_success_rate_pct"] = pct(min(scored_ok, drafts_n - failed_n), lm["valid_records"])
        q = store.queue(summaries["accuracy"]["drive_id"])
        open_n = sum(1 for x in q if x.get("final_status") in ("ON_HOLD", "AWAITING_INFORMATION"))
        h["review_decided_pct"] = lm["human_review_completion_rate_pct"]
        h["human_review_completion_rate_pct"] = pct(rep.get("reviewed", 0) - open_n, rep.get("scored", 0))
        h["lab_open_after_review"] = open_n
        h["lab_fully_processed"] = min(scored_ok, drafts_n - failed_n)
        h["lab_workflow_success_rate_pct"], h["lab_human_review_completion_rate_pct"] = h["workflow_success_rate_pct"], h["human_review_completion_rate_pct"]
        h["delivery_success_rate_pct"] = round(100 * rep["delivery_success_rate"], 2) if rep.get("delivery_success_rate") is not None else None
        h["overrides"] = len(rep.get("overrides", []))
    p = summaries.get("performance", {})
    for k in ("parallel_speedup", "batch_speedup", "ms_per_valid_record", "outputs_identical_across_modes", "score_consistent_across_runs", "connection_reuse_speedup"):
        if k in p:
            h[k] = p[k]
    summaries.get("accuracy", {}).pop("_compare", None)
    summaries.get("accuracy", {}).pop("_fairness", None)
    h["quality_index"] = quality_index(h)
    report = {"generated_at": datetime.now().isoformat(timespec="seconds"), "sections": sections, "headline": _clean(h), "summaries": _clean(summaries),
              "timings_s": timings, "total_s": round(time.time() - t0, 1), "tables": {k: _clean(v.to_dict("records")) for k, v in tables.items()}}
    run_dir = PATHS.reports / f"run_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    for k, v in tables.items():
        v.to_csv(run_dir / f"{k}.csv", index=False)
    report["report_dir"] = run_dir.relative_to(PATHS.root).as_posix()
    txt = json.dumps(report, indent=1, default=str)
    (run_dir / "report.json").write_text(txt, encoding="utf-8")
    (PATHS.reports / "latest_report.json").write_text(txt, encoding="utf-8")
    env.call("export_audit", {"actor": S.ADMIN})
    return report


def list_reports() -> list[dict]:
    out = []
    for d in sorted(PATHS.reports.glob("run_*"), reverse=True):
        try:
            r = json.loads((d / "report.json").read_text(encoding="utf-8"))
            out.append({"run": d.name, "generated_at": r.get("generated_at"), "sections": r.get("sections"), "total_s": r.get("total_s"), "headline": r.get("headline", {})})
        except Exception:  # noqa: BLE001
            continue
    return out


def load_report(run: str | None = None) -> dict | None:
    p = PATHS.reports / run / "report.json" if run else PATHS.reports / "latest_report.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
