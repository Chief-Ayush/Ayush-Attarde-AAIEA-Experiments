"""Small metric helpers and the six metrics named in the lab manual."""
from __future__ import annotations

import numpy as np


def percentile(xs, q):
    xs = [x for x in xs if x is not None]
    return float(np.percentile(xs, q)) if xs else None


def pct(a: float, b: float) -> float:
    return round(100 * a / b, 2) if b else 0.0


def lab_metrics(report: dict, total_ms: float, tests_passed: int, tests_total: int, scored_ok: int) -> dict:
    """data validation rate, workflow success rate, notification draft rate, test pass rate, average time per valid record, review completion rate."""
    valid, total = report.get("candidates", 0), report.get("candidates", 0) + report.get("quarantined", 0)
    drafts = sum(report.get("notifications", {}).values())
    return {
        "records": total, "valid_records": valid, "quarantined": report.get("quarantined", 0),
        "data_validation_rate_pct": pct(valid, total),
        "workflow_success_rate_pct": pct(scored_ok, valid),
        "notification_draft_rate_pct": pct(drafts, valid),
        "test_pass_rate_pct": pct(tests_passed, tests_total),
        "avg_time_per_valid_record_ms": round(total_ms / valid, 1) if valid else None,
        "human_review_completion_rate_pct": pct(report.get("reviewed", 0), report.get("scored", 0)),
    }


def prf(y_true: list, y_pred: list, labels: list) -> dict:
    """Per-class precision / recall / F1 and macro-F1 (classes with no true samples and no predictions are skipped)."""
    out, f1s = {}, []
    for c in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == c and p == c)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != c and p == c)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == c and p != c)
        if tp + fp + fn == 0:
            continue
        pr, rc = tp / (tp + fp) if tp + fp else 0.0, tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * pr * rc / (pr + rc) if pr + rc else 0.0
        out[c] = {"precision": round(100 * pr, 1), "recall": round(100 * rc, 1), "f1": round(100 * f1, 1), "support": tp + fn}
        f1s.append(f1)
    return {"classes": out, "macro_f1": round(100 * sum(f1s) / len(f1s), 1) if f1s else 0.0}


def confusion(y_true: list, y_pred: list, labels: list) -> dict:
    return {t: {p: sum(1 for a, b in zip(y_true, y_pred) if a == t and b == p) for p in labels} for t in labels}
