"""NotificationAgent: drafts messages, sends the ones allowed to go and retries temporary failures (comms server)."""
from __future__ import annotations

import time

from .base import EMPTY_DISPATCH, HUMAN_STATUSES, Runner, StageError, State, agent, fail, timed


@agent("NotificationAgent")
def make(run: Runner):
    s = run.s  # noqa: F841

    def notify(state: State) -> dict:
        t0 = time.perf_counter()
        ids = [t["ticket_id"] for t in state.get("tickets") or []]
        prep = {"drafted": 0, "auto_approved": 0, "awaiting_approval": 0, "duplicates": 0, "policy_blocked": 0}
        errs = []
        for o in run.many("prepare_notifications", [{"ticket_ids": ch, "actor": state["actor"], "auto_dispatch": s.auto_dispatch, "trace_id": state["trace_id"]} for ch in run.chunks(ids)], phase=7):
            if o.ok:
                for k in prep:
                    prep[k] += o.data.get(k, 0)
            else:
                errs.append(f"prepare_notifications: {o.error}")
        disp = {"sent": 0, "dead_letter": 0, "retries": 0, "blocked": 0, "already_sent": 0, "attempts": 0, "recovered": 0}
        for o in run.many("dispatch_pending", [{"actor": state["actor"], "ticket_ids": ch, "max_attempts": s.notification_attempts, "trace_id": state["trace_id"]} for ch in run.chunks(ids)], phase=7):
            if o.ok:
                for k in disp:
                    disp[k] += o.data.get(k, 0)
            else:
                errs.append(f"dispatch_pending: {o.error}")
        return {"notify": prep, "dispatch": disp, "errors": (state.get("errors") or []) + errs, "timings": timed(state, "notify", t0), "status": "notified"}


    return notify
