"""Evaluation runner: runs the suites, writes CSV/JSON under storage/reports/run_*/ and keeps latest_report.json."""
from __future__ import annotations

import json
import time
from datetime import datetime

import numpy as np
import pandas as pd

import workflow_router as wr

from ..config import PATHS
from . import analysis as AN
from . import suites as S
from .accuracy import run_accuracy
from .env import Env, pass_summary
from .hard import run_hard
from .reliability import run_reliability

ALL_SECTIONS = ["functional", "agent", "security", "reliability", "accuracy", "hard", "analysis", "performance"]
SECTION_INFO = {
    "functional": "Functional: the manual's 20 test cases plus input files, ticket lifecycle, SLA and notification edge cases",
    "agent": "Agent workflow: approve, edit, reject, escalate, hold, reassign, reopen, customer replies, priority aging",
    "security": "Security: role-based access, team scope, prompt injection, personal-data masking, formula injection, audit tamper evidence",
    "reliability": "Reliability: retries with backoff, idempotency, dead-letter queue, fault injection per error class, server crash recovery",
    "accuracy": "Accuracy: 147 hand-labelled queries plus 9 invalid records through the full workflow",
    "hard": "Hard cases: negation, past tense, typos, mixed requests, group phrases, injection, awkward wording; known limits stay as failures",
    "analysis": "Comparisons: classifier engines, retrieval methods, workflow ablation, scheduling policies on a staffed queue",
    "performance": "Performance: sequential vs batch vs parallel (3 runs each) and repeat consistency",
}
WEIGHTS = [("intent_accuracy_pct", 0.12), ("priority_accuracy_pct", 0.08), ("retrieval_top1_pct", 0.08), ("safe_route_pct", 0.12), ("auto_answer_precision_pct", 0.10),
           ("functional_pass_pct", 0.10), ("agent_pass_pct", 0.08), ("security_pass_pct", 0.10), ("reliability_pass_pct", 0.10), ("hard_pass_pct", 0.12)]


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


def _cleanup() -> None:
    """The evaluation works in its own EVAL- batches; remove them (and their upload files) so the desk, the SLA monitor and the counts show real work only.
    The audit log keeps its records."""
    from .. import store
    store.purge_prefix("EVAL-")
    for f in PATHS.uploads.glob("eval_*"):
        f.unlink(missing_ok=True)


def run_all(sections: list[str] | None = None, progress=None) -> dict:
    sections = [s for s in ALL_SECTIONS if s in (sections or ALL_SECTIONS)]
    t0 = time.time()
    env = Env()
    tables, summaries, timings = {}, {}, {}
    n, done = len(sections), 0
    acc_df = None

    def tick(frac, msg):
        if progress:
            progress(min(1.0, (done + frac) / n), msg)

    for sec in sections:
        s0 = time.time()
        tick(0, f"running {sec} ...")
        if sec in ("functional", "agent", "security", "reliability"):
            fn = {"functional": S.run_functional, "agent": S.run_agent, "security": S.run_security, "reliability": run_reliability}[sec]
            tables[sec] = fn(env)
            summaries[sec] = pass_summary(tables[sec])
        elif sec == "hard":
            tables["hard"], summaries["hard"] = run_hard(env)
        elif sec == "accuracy":
            acc_df, out, _ = run_accuracy(env)
            tables["accuracy"] = acc_df
            summaries["accuracy"] = out
        elif sec == "analysis":
            tables["classifiers"] = AN.compare_classifiers()
            tables["retrieval"] = AN.compare_retrieval()
            tables["ablation"] = AN.ablation(env, progress=lambda f, m: tick(f * 0.7, m))
            if acc_df is None:
                acc_df, _, _ = run_accuracy(env)
            tk = [{"priority": r.priority, "team": wr.assign_team({"intent": r.intent, "priority": r.priority}, r.route), "route_class": r.route_class, "complexity": r.complexity} for r in acc_df.itertuples()]
            tables["scheduling"], util = AN.sla_comparison(tk)
            summaries["analysis"] = {"utilisation_pct": util.get("Priority queues"), "busy_window_min": AN.BUSY_WINDOW, "staff": AN.STAFF}
        elif sec == "performance":
            tables["performance"], summaries["performance"] = AN.performance(env)
        timings[sec] = round(time.time() - s0, 1)
        done += 1

    h = {}
    a = summaries.get("accuracy", {})
    for k, v in a.items():
        if isinstance(v, (int, float, bool)) and not isinstance(v, dict):
            h[k] = v
    for sec in ("functional", "agent", "security", "reliability"):
        if sec in summaries:
            h[f"{sec}_pass_pct"] = summaries[sec]["pass_rate_pct"]
            h[f"{sec}_tests"] = summaries[sec]["tests"]
    hd = summaries.get("hard")
    if hd:
        h["hard_pass_pct"] = hd["pass_rate_pct"]
        h["hard_cases_pass_pct"] = hd["hard_pass_pct"]
        h["hard_controls_pass_pct"] = hd["control_pass_pct"]
        h["hard_tier1_pass_pct"], h["hard_tier2_pass_pct"] = hd["tier1_pass_pct"], hd["tier2_pass_pct"]
        h["hard_total"], h["hard_failed"] = hd["tests"], hd["failed"]
    p = summaries.get("performance", {})
    for k in ("batch_speedup", "parallel_speedup", "outputs_identical_across_modes", "outputs_identical_across_runs"):
        if k in p:
            h[k] = p[k]
    h["quality_index"] = quality_index(h) if set(sections) == set(ALL_SECTIONS) else None      # a score from some sections only would look better or worse than it is
    report = {"generated_at": datetime.now().isoformat(timespec="seconds"), "sections": sections, "headline": _clean(h), "summaries": _clean({k: {x: y for x, y in v.items() if x not in ("intent_confusion", "intent_classes")} if k == "accuracy" else v for k, v in summaries.items()}),
              "intent_classes": _clean(a.get("intent_classes")), "intent_confusion": _clean(a.get("intent_confusion")),
              "timings_s": timings, "total_s": round(time.time() - t0, 1), "tables": {k: _clean(v.to_dict("records")) for k, v in tables.items()}}
    run_dir = PATHS.reports / f"run_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    for k, v in tables.items():
        v.to_csv(run_dir / f"{k}.csv", index=False)
    report["report_dir"] = run_dir.relative_to(PATHS.root).as_posix()
    txt = json.dumps(report, indent=1, default=str)
    (run_dir / "report.json").write_text(txt, encoding="utf-8")
    if set(sections) == set(ALL_SECTIONS) or not (PATHS.reports / "latest_report.json").exists():       # a partial run never replaces the full report that other pages read
        (PATHS.reports / "latest_report.json").write_text(txt, encoding="utf-8")
    _cleanup()
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
