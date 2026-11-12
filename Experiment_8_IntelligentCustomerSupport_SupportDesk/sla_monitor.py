"""SLA tracking (lab step 13): first-response and resolution deadlines with approved pauses.

Active time = elapsed time minus time spent in an approved pause state (AWAITING_CUSTOMER, ON_HOLD).  The deadline is therefore
created_at + SLA minutes + paused time.  States: WITHIN_SLA, AT_RISK (>= 75 % of the target used), BREACHED, PAUSED.
"""
from __future__ import annotations

from datetime import datetime

from ticket_manager import PAUSE_STATES, parse


def _active_minutes(t: dict, end: datetime) -> float:
    start = parse(t.get("clock_start") or t["created_at"])
    total = (end - start).total_seconds() / 60
    paused = float(t.get("paused_total_min") or 0)
    if t.get("sla_paused_at"):
        paused += max(0.0, (end - parse(t["sla_paused_at"])).total_seconds() / 60)
    return max(0.0, total - paused)


def sla_status(t: dict, now: datetime, policy: dict, which: str = "first_response") -> dict:
    window = policy["sla_minutes" if which == "first_response" else "resolution_minutes"][t.get("sla_priority") or t.get("base_priority") or t["priority"]]
    done_at = t.get("first_response_at") if which == "first_response" else t.get("resolved_at")
    if done_at:
        used = _active_minutes(t, parse(done_at))
        return {"state": "BREACHED" if used > window else "WITHIN_SLA", "used_min": round(used, 1), "window_min": window, "met": used <= window, "done": True}
    if t["status"] in PAUSE_STATES:
        return {"state": "PAUSED", "used_min": round(_active_minutes(t, now), 1), "window_min": window, "met": None, "done": False}
    used = _active_minutes(t, now)
    state = "BREACHED" if used > window else "AT_RISK" if used >= policy.get("at_risk_fraction", 0.75) * window else "WITHIN_SLA"
    return {"state": state, "used_min": round(used, 1), "window_min": window, "met": None, "done": False}


def overall_state(t: dict, now: datetime, policy: dict) -> str:
    if t["status"] in ("RESOLVED", "CLOSED"):
        s = sla_status(t, now, policy, "resolution")
        f = sla_status(t, now, policy, "first_response")
        return "BREACHED" if "BREACHED" in (s["state"], f["state"]) else "WITHIN_SLA"
    f = sla_status(t, now, policy, "first_response")
    r = sla_status(t, now, policy, "resolution")
    for s in ("BREACHED", "AT_RISK", "PAUSED"):
        if s in (f["state"], r["state"]):
            return s
    return "WITHIN_SLA"


def scan(tickets: list[dict], now: datetime, policy: dict) -> list[dict]:
    """Return one alert per active ticket that is AT_RISK or BREACHED for the first response or the resolution."""
    alerts = []
    for t in tickets:
        if t["status"] in ("RESOLVED", "CLOSED"):
            continue
        for which in ("first_response", "resolution"):
            s = sla_status(t, now, policy, which)
            if s["state"] in ("AT_RISK", "BREACHED") and not s["done"]:
                alerts.append({"ticket_id": t["ticket_id"], "which": which, "state": s["state"], "used_min": s["used_min"], "window_min": s["window_min"], "priority": t["priority"], "team": t["assigned_team"]})
    return alerts
