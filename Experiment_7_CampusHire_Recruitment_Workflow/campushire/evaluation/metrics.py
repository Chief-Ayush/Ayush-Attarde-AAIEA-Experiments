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
