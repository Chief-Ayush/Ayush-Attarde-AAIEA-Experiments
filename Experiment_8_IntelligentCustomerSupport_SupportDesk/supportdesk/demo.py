"""Demo data: the first time the app starts it is filled with a realistic working day, so every page has something to show.

A batch of customer messages (data/samples/demo_queries.csv) goes through the real workflow. Agents answer most tickets, a few stay open
(including urgent ones), some customers confirm or reject the answer, and the service clock is moved forward so the SLA monitor shows
tickets that are at risk or late.  Runs once; the 'demo_seeded' marker in the database stops it from running again.
"""
from __future__ import annotations

from . import store
from .config import PATHS, load_settings
from .mcp import get_hub
from .workflow import HUMAN_STATUSES, Workflow, auto_agent_policy

ACTOR = "riya.agent"
BATCH = "DEMO-DAY1"
SOURCE = "data/samples/demo_queries.csv"
PER_TEAM = 3           # human-queue tickets per team left undecided (urgent ones are always kept)
AGES = {"P1": (20, 7, 25), "P2": (30, 13, 36), "P3": (80, 37, 190), "P4": (90, 41, 120)}      # waiting time of open tickets in minutes: base, step, spread


def _marker(set_: bool = False) -> bool:
    store.init_db()
    with store.connect() as con:
        if set_:
            con.execute("INSERT INTO meta (k, v) VALUES ('demo_seeded', '1') ON CONFLICT(k) DO UPDATE SET v='1'")
            con.commit()
            return True
        return con.execute("SELECT 1 FROM meta WHERE k='demo_seeded'").fetchone() is not None


def needs_demo() -> bool:
    return not _marker()


def clear() -> None:
    """Remove the sample batch and its marker so it can be created again (other tickets are left alone)."""
    store.purge_prefix(BATCH)
    store.init_db()
    with store.connect() as con:
        con.execute("DELETE FROM meta WHERE k='demo_seeded'")
        con.commit()


def reload() -> dict:
    clear()
    return seed()


def seed() -> dict:
    clear() if store.list_tickets(batch_id=BATCH, limit=1) else None      # an earlier run that stopped half way is replaced
    hub = get_hub()
    s = load_settings().with_(execution_mode="parallel")
    wf = Workflow(hub, s)
    wf.start(BATCH, str(PATHS.root / SOURCE), ACTOR)
    call = wf.run.call
    queue = [t for t in call("list_tickets", {"batch_id": BATCH, "status": HUMAN_STATUSES, "limit": 5000}).data["tickets"] if not t["first_response_at"]]
    queue.sort(key=lambda t: (t["priority"], t["ticket_id"]))
    keep = {t["ticket_id"] for t in queue if t["priority"] == "P1"}                       # urgent ones stay with the incident team
    per_team: dict[str, int] = {}
    for t in queue:                                                                        # every team keeps a little work in its queue
        if per_team.get(t["assigned_team"], 0) < PER_TEAM and t["ticket_id"] not in keep:
            per_team[t["assigned_team"]] = per_team.get(t["assigned_team"], 0) + 1
            keep.add(t["ticket_id"])
    wf.resume([auto_agent_policy(t) for t in queue if t["ticket_id"] not in keep])
    # customers answer some of the messages that were sent
    waiting = [t for t in call("list_tickets", {"batch_id": BATCH, "status": ["AWAITING_CUSTOMER"], "limit": 500}).data["tickets"]]
    for i, t in enumerate(waiting[:24]):
        call("customer_reply", {"ticket_id": t["ticket_id"], "helped": i % 9 != 4, "actor": ACTOR, "note": ""}, must=False)
    _age_open_tickets()
    call("sla_scan", {"actor": "system:sla-monitor", "apply_aging": True}, must=False)
    call("dispatch_pending", {"actor": "sana.lead"}, must=False)                      # the notices to the support lead go out
    _marker(True)
    return {"batch": BATCH, "tickets": len(store.list_tickets(batch_id=BATCH, limit=5000)), "kept_open": len(keep)}


def _age_open_tickets() -> None:
    """Make the tickets that are still waiting for a first reply look like a working morning: they have been waiting for different lengths of time, so the
    SLA monitor shows tickets that are on track, at risk and late (their timestamps are moved back; the service clock itself is not touched)."""
    from datetime import datetime, timedelta

    open_ = [t for t in store.list_tickets(batch_id=BATCH, active_only=True, limit=500) if not t["first_response_at"] and t["status"] != "AWAITING_CUSTOMER"]
    with store.tx() as con:
        for i, t in enumerate(sorted(open_, key=lambda x: x["ticket_id"])):
            base, step, spread = AGES.get(t["priority"], AGES["P3"])
            age = base + (i * step) % spread
            row = dict(con.execute("SELECT created_at, clock_start, first_response_due, resolution_due FROM tickets WHERE ticket_id=?", (t["ticket_id"],)).fetchone())
            cur = (store.now() - datetime.fromisoformat(row["clock_start"])).total_seconds() / 60
            shift = timedelta(minutes=max(0.0, age - cur))
            new = {k: store.iso(datetime.fromisoformat(v) - shift) for k, v in row.items() if v}
            con.execute("UPDATE tickets SET created_at=?, clock_start=?, first_response_due=?, resolution_due=? WHERE ticket_id=?",
                        (new["created_at"], new["clock_start"], new["first_response_due"], new["resolution_due"], t["ticket_id"]))


def ensure_demo() -> dict | None:
    return seed() if needs_demo() else None
