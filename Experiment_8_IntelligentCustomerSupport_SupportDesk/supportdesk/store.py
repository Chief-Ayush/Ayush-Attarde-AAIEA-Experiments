"""SQLite persistence (variation 5): tickets, history, comments, SLA deadlines, notifications and an append-only, hash-chained audit log.

* One database file in WAL mode with a short-lived connection per operation, so the four MCP server processes can write safely.
* The audit table rejects UPDATE and DELETE with triggers and every row carries the hash of the previous row, so tampering that
  bypasses the triggers is still detected by `verify_audit_chain()`.
* audit_log.csv, notification_log.csv and rejected_queries.csv (files named in the manual) are append-only exports.
* The service clock can be advanced (simulated time) so SLA warnings and breaches can be demonstrated and tested.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import time
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import notifications as nt
import ticket_manager as tm
import sla_monitor as sla

from .config import PATHS, load_policy
from .errors import InputError

_CSV_LOCK = threading.Lock()
AUDIT_FIELDS = ["event_id", "timestamp", "query_id", "ticket_id", "event_type", "intent", "complexity", "priority", "knowledge_article_id", "match_score", "route",
                "reviewer_action", "ticket_status", "notification_status", "error_details", "actor", "trace_id"]
AUDIT_DATA = AUDIT_FIELDS[4:]
NOTIF_FIELDS = ["notification_id", "timestamp", "ticket_id", "event", "to_addr", "state", "attempts", "idempotency_key", "detail"]
REJECT_FIELDS = ["timestamp", "batch_id", "row", "query_id", "customer_id", "code", "reason", "next_action"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS queries (query_id TEXT PRIMARY KEY, customer_id TEXT, query_text TEXT, created_at TEXT, channel TEXT, batch_id TEXT, record_hash TEXT,
  flags TEXT, imported_at TEXT);
CREATE TABLE IF NOT EXISTS rejected (id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT, row INTEGER, query_id TEXT, customer_id TEXT, code TEXT, reason TEXT,
  next_action TEXT, record TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS tickets (ticket_id TEXT PRIMARY KEY, query_id TEXT UNIQUE, customer_id TEXT, batch_id TEXT, intent TEXT, complexity TEXT, priority TEXT,
  base_priority TEXT, sla_priority TEXT, route TEXT, status TEXT, assigned_team TEXT, assignee TEXT, article_id TEXT, match_score REAL, confidence REAL, method TEXT,
  proposed_response TEXT, final_response TEXT, resolution_summary TEXT, flags TEXT, query_text TEXT,
  created_at TEXT, updated_at TEXT, clock_start TEXT, first_response_due TEXT, resolution_due TEXT, first_response_at TEXT, resolved_at TEXT,
  sla_paused_at TEXT, paused_total_min REAL DEFAULT 0, reopen_count INTEGER DEFAULT 0, aged INTEGER DEFAULT 0, last_reviewer TEXT, review_action TEXT);
CREATE TABLE IF NOT EXISTS ticket_events (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT, at TEXT, kind TEXT, from_val TEXT, to_val TEXT, actor TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS response_versions (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT, version INTEGER, text TEXT, edited_by TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS notifications (notification_id TEXT PRIMARY KEY, ticket_id TEXT, event TEXT, recipient_type TEXT, to_addr TEXT, subject TEXT, message TEXT,
  state TEXT, needs_approval INTEGER, approved_by TEXT, idempotency_key TEXT UNIQUE, attempts INTEGER DEFAULT 0, sim_fail_first INTEGER DEFAULT 0, detail TEXT,
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS notification_attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, notification_id TEXT, attempt INTEGER, result TEXT, error TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS sla_alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT, which TEXT, state TEXT, at TEXT, UNIQUE (ticket_id, which, state));
CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, batch_id TEXT, started_at TEXT, finished_at TEXT, mode TEXT, actor TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS audit (event_id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, query_id TEXT, ticket_id TEXT, event_type TEXT, intent TEXT, complexity TEXT,
  priority TEXT, knowledge_article_id TEXT, match_score REAL, route TEXT, reviewer_action TEXT, ticket_status TEXT, notification_status TEXT, error_details TEXT,
  actor TEXT, trace_id TEXT, prev_hash TEXT, row_hash TEXT);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT, 'audit records are append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT, 'audit records are append-only'); END;
CREATE INDEX IF NOT EXISTS ix_audit_ticket ON audit (ticket_id);
CREATE INDEX IF NOT EXISTS ix_ticket_status ON tickets (status, priority);
CREATE INDEX IF NOT EXISTS ix_ticket_batch ON tickets (batch_id);
CREATE INDEX IF NOT EXISTS ix_notif_ticket ON notifications (ticket_id);
"""


def csv_safe(v):
    """Neutralise spreadsheet formulas in exported text."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") and len(s) > 1 else s


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(PATHS.db, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    for _ in range(40):                       # several server processes start together and race for the WAL switch
        try:
            con.execute("PRAGMA journal_mode=WAL")
            break
        except sqlite3.OperationalError:
            time.sleep(0.25)
    return con


@contextmanager
def tx():
    con = connect()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db() -> None:
    for attempt in range(40):
        try:
            with tx() as con:
                con.executescript(SCHEMA)
            return
        except sqlite3.OperationalError:
            if attempt == 39:
                raise
            time.sleep(0.25)


init_db()


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------------------------------------------------- clock
def clock_offset() -> float:
    with tx() as con:
        r = con.execute("SELECT v FROM meta WHERE k='clock_offset_min'").fetchone()
    return float(r["v"]) if r else 0.0


def now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=clock_offset())


def advance_clock(minutes: float) -> float:
    with tx() as con:
        con.execute("BEGIN IMMEDIATE") if not con.in_transaction else None
        r = con.execute("SELECT v FROM meta WHERE k='clock_offset_min'").fetchone()
        new = (float(r["v"]) if r else 0.0) + float(minutes)
        con.execute("INSERT INTO meta (k, v) VALUES ('clock_offset_min', ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(new),))
    return new


def reset_clock() -> None:
    with tx() as con:
        con.execute("DELETE FROM meta WHERE k='clock_offset_min'")


# ---------------------------------------------------------------------------------------------------------------------- audit
def _hash(prev: str, payload: dict) -> str:
    return hashlib.sha256((prev + json.dumps(payload, sort_keys=True, default=str)).encode()).hexdigest()[:24]


def add_audit(event_type: str, query_id: str = "", ticket_id: str = "", **kw) -> int:
    rec = {"timestamp": iso(now()), "query_id": query_id or "", "ticket_id": ticket_id or "", "event_type": event_type}
    for f in AUDIT_DATA[1:]:
        v = kw.get(f)
        rec[f] = json.dumps(v) if isinstance(v, (list, dict)) else v
    with tx() as con:
        con.execute("BEGIN IMMEDIATE") if not con.in_transaction else None
        last = con.execute("SELECT row_hash FROM audit ORDER BY event_id DESC LIMIT 1").fetchone()
        prev = last["row_hash"] if last else "GENESIS"
        rec["prev_hash"], rec["row_hash"] = prev, _hash(prev, rec)
        cur = con.execute(f"INSERT INTO audit ({','.join(rec)}) VALUES ({','.join('?' * len(rec))})", list(rec.values()))
        eid = cur.lastrowid
    _append_csv(PATHS.audit_csv, AUDIT_FIELDS, {**rec, "event_id": eid})
    return eid


def _append_csv(path, fields, rec: dict) -> None:
    with _CSV_LOCK:
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow({k: csv_safe(rec.get(k)) for k in fields})


def query_audit(ticket_id: str | None = None, query_id: str | None = None, event_type: str | None = None, limit: int = 500, after_id: int = 0) -> list[dict]:
    q, a = "SELECT * FROM audit WHERE event_id > ?", [after_id]
    for col, v in (("ticket_id", ticket_id), ("query_id", query_id), ("event_type", event_type)):
        if v:
            q += f" AND {col} = ?"
            a.append(v)
    q += " ORDER BY event_id DESC LIMIT ?"
    a.append(limit)
    with tx() as con:
        return _rows(con.execute(q, a))


def verify_audit_chain() -> dict:
    with tx() as con:
        rows = _rows(con.execute("SELECT * FROM audit ORDER BY event_id"))
    prev, bad = "GENESIS", None
    for r in rows:
        payload = {k: v for k, v in r.items() if k not in ("event_id", "prev_hash", "row_hash")}
        if r["prev_hash"] != prev or _hash(prev, payload) != r["row_hash"]:
            bad = r["event_id"]
            break
        prev = r["row_hash"]
    return {"ok": bad is None, "records": len(rows), "first_bad_event": bad}


# ---------------------------------------------------------------------------------------------------------------------- queries
def existing_query_hashes() -> dict:
    with tx() as con:
        return {r["query_id"]: r["record_hash"] for r in con.execute("SELECT query_id, record_hash FROM queries")}


def import_queries(batch_id: str, valid: list[dict], rejected: list[dict], actor: str, trace_id: str = "", channel: str = "file") -> dict:
    """Idempotent: an identical record that is already stored counts as a repeat; the same ID with different content is rejected."""
    imported = repeats = 0
    ts = iso(now())
    known = existing_query_hashes()
    with tx() as con:
        for r in valid:
            h = r["record_hash"]
            if r["query_id"] in known:
                if known[r["query_id"]] == h:
                    repeats += 1
                    continue
                rejected.append({"row": r.get("row", 0), "record": r, "code": "DUPLICATE_ID", "reason": "Query ID already exists with different content", "next_action": "Use a new query ID"})
                continue
            con.execute("INSERT INTO queries VALUES (?,?,?,?,?,?,?,?,?)", (r["query_id"], r["customer_id"], r["query_text"], r["created_at"], r.get("channel") or channel,
                                                                           batch_id, h, json.dumps(r.get("flags") or {}), ts))
            imported += 1
        for x in rejected:
            rec = x.get("record", {})
            con.execute("INSERT INTO rejected (batch_id,row,query_id,customer_id,code,reason,next_action,record,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (batch_id, x.get("row"), rec.get("query_id"), rec.get("customer_id"), x.get("code"), x.get("reason"), x.get("next_action"), json.dumps(rec, default=str), ts))
    for x in rejected:
        rec = x.get("record", {})
        _append_csv(PATHS.rejected_csv, REJECT_FIELDS, {"timestamp": ts, "batch_id": batch_id, "row": x.get("row"), "query_id": rec.get("query_id"), "customer_id": rec.get("customer_id"),
                                                      "code": x.get("code"), "reason": x.get("reason"), "next_action": x.get("next_action")})
        add_audit("query_rejected", rec.get("query_id") or "", "", error_details=f"{x.get('code')}: {x.get('reason')}", actor=actor, trace_id=trace_id)
    for r in valid:
        if r.get("flags", {}).get("injection"):
            add_audit("injection_text_flagged", r["query_id"], "", error_details="query text tries to instruct the system; treated as plain text", actor=actor, trace_id=trace_id)
    add_audit("batch_imported", "", "", error_details=f"batch={batch_id} imported={imported} repeats={repeats} rejected={len(rejected)}", actor=actor, trace_id=trace_id)
    return {"imported": imported, "repeats": repeats, "rejected": len(rejected)}


def get_query(query_id: str) -> dict | None:
    with tx() as con:
        r = con.execute("SELECT * FROM queries WHERE query_id=?", (query_id,)).fetchone()
    return dict(r) if r else None


def queries_without_ticket(batch_id: str) -> list[dict]:
    with tx() as con:
        return _rows(con.execute("SELECT q.* FROM queries q LEFT JOIN tickets t ON t.query_id=q.query_id WHERE q.batch_id=? AND t.ticket_id IS NULL ORDER BY q.rowid", (batch_id,)))


def rejected_rows(batch_id: str | None = None, limit: int = 500) -> list[dict]:
    with tx() as con:
        if batch_id:
            return _rows(con.execute("SELECT * FROM rejected WHERE batch_id=? ORDER BY id DESC LIMIT ?", (batch_id, limit)))
        return _rows(con.execute("SELECT * FROM rejected ORDER BY id DESC LIMIT ?", (limit,)))


# ---------------------------------------------------------------------------------------------------------------------- tickets
def _event(con, ticket_id: str, kind: str, frm=None, to=None, actor="", note="", at: str | None = None) -> None:
    con.execute("INSERT INTO ticket_events (ticket_id,at,kind,from_val,to_val,actor,note) VALUES (?,?,?,?,?,?,?)", (ticket_id, at or iso(now()), kind, frm, to, actor, note))


def create_ticket(t: dict, query_text: str, batch_id: str, actor: str, confidence: float = 0.0, method: str = "", flags: dict | None = None, trace_id: str = "") -> dict:
    """Insert a ticket once per query (idempotent: a repeat returns the existing ticket)."""
    with tx() as con:
        con.execute("BEGIN IMMEDIATE") if not con.in_transaction else None
        ex = con.execute("SELECT * FROM tickets WHERE query_id=?", (t["query_id"],)).fetchone()
        if ex:
            return {**dict(ex), "_existing": True}
        t = {**t, "batch_id": batch_id, "confidence": confidence, "method": method, "flags": json.dumps(flags or {}), "query_text": query_text,
             "clock_start": t["created_at"], "sla_priority": t["priority"]}
        if t["status"] in tm.PAUSE_STATES:
            t["sla_paused_at"] = t["created_at"]
        cols = list(t)
        con.execute(f"INSERT INTO tickets ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [t[c] for c in cols])
        _event(con, t["ticket_id"], "CREATED", None, t["status"], actor, f"route={t['route']}", at=t["created_at"])
        _event(con, t["ticket_id"], "ASSIGN", None, t["assigned_team"], actor, "initial routing", at=t["created_at"])
    add_audit("ticket_created", t["query_id"], t["ticket_id"], intent=t["intent"], complexity=t["complexity"], priority=t["priority"], knowledge_article_id=t["article_id"],
              match_score=t["match_score"], route=t["route"], ticket_status=t["status"], actor=actor, trace_id=trace_id)
    return t


def get_ticket(ticket_id: str) -> dict | None:
    with tx() as con:
        r = con.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
    return dict(r) if r else None


def list_tickets(status: list[str] | None = None, team: str | None = None, priority: str | None = None, batch_id: str | None = None, active_only: bool = False,
                 text: str | None = None, limit: int = 500) -> list[dict]:
    q, a = "SELECT * FROM tickets WHERE 1=1", []
    if status:
        q += f" AND status IN ({','.join('?' * len(status))})"
        a += status
    if active_only:
        q += f" AND status IN ({','.join('?' * len(tm.ACTIVE))})"
        a += sorted(tm.ACTIVE)
    for col, v in (("assigned_team", team), ("priority", priority), ("batch_id", batch_id)):
        if v:
            q += f" AND {col} = ?"
            a.append(v)
    if text:
        q += " AND (ticket_id LIKE ? OR query_text LIKE ? OR customer_id LIKE ?)"
        a += [f"%{text}%"] * 3
    q += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    a.append(limit)
    with tx() as con:
        return _rows(con.execute(q, a))


def ticket_history(ticket_id: str) -> list[dict]:
    with tx() as con:
        return _rows(con.execute("SELECT * FROM ticket_events WHERE ticket_id=? ORDER BY id", (ticket_id,)))


def response_versions(ticket_id: str) -> list[dict]:
    with tx() as con:
        return _rows(con.execute("SELECT * FROM response_versions WHERE ticket_id=? ORDER BY version", (ticket_id,)))


def _set_status(con, t: dict, new: str, at: datetime, actor: str, note: str = "") -> dict:
    """Apply a status change with SLA pause bookkeeping.  Returns the field updates."""
    upd = {"status": new, "updated_at": iso(at)}
    was_paused, will_pause = t["status"] in tm.PAUSE_STATES, new in tm.PAUSE_STATES
    if was_paused and not will_pause and t.get("sla_paused_at"):
        upd["paused_total_min"] = float(t.get("paused_total_min") or 0) + max(0.0, (at - tm.parse(t["sla_paused_at"])).total_seconds() / 60)
        upd["sla_paused_at"] = None
    elif will_pause and not was_paused:
        upd["sla_paused_at"] = iso(at)
    if new == "RESOLVED":
        upd["resolved_at"] = iso(at)
        if was_paused and "paused_total_min" not in upd and t.get("sla_paused_at"):
            upd["paused_total_min"] = float(t.get("paused_total_min") or 0) + max(0.0, (at - tm.parse(t["sla_paused_at"])).total_seconds() / 60)
            upd["sla_paused_at"] = None
    _event(con, t["ticket_id"], "STATUS", t["status"], new, actor, note, at=iso(at))
    return upd


def _update(con, ticket_id: str, upd: dict) -> None:
    con.execute(f"UPDATE tickets SET {','.join(f'{k}=?' for k in upd)} WHERE ticket_id=?", [*upd.values(), ticket_id])


def apply_review(ticket_id: str, action: str, actor: str, note: str = "", edited_response: str | None = None, team: str | None = None, trace_id: str = "",
                 at: datetime | None = None) -> dict:
    at = at or now()
    with tx() as con:
        con.execute("BEGIN IMMEDIATE") if not con.in_transaction else None
        row = con.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
        if not row:
            raise InputError(f"Unknown ticket {ticket_id}")
        t = dict(row)
        try:
            target = tm.check_action(t["status"], action)
        except ValueError as exc:
            raise InputError(str(exc)) from exc
        upd: dict = {"last_reviewer": actor, "review_action": action, "assignee": actor}
        if action == "EDIT":
            text = (edited_response or "").strip()
            if not text:
                raise InputError("EDIT needs the new response text")
            prev = t.get("final_response") or t.get("proposed_response") or ""
            n = con.execute("SELECT COUNT(*) c FROM response_versions WHERE ticket_id=?", (ticket_id,)).fetchone()["c"]
            if prev:
                con.execute("INSERT INTO response_versions (ticket_id,version,text,edited_by,at) VALUES (?,?,?,?,?)", (ticket_id, n + 1, prev, actor, iso(at)))
            upd["final_response"] = text
        elif action == "APPROVE":
            resp = (edited_response or t.get("final_response") or t.get("proposed_response") or "").strip()
            if not resp:
                raise InputError("There is no response to approve. Write one with EDIT first.")
            upd["final_response"] = resp
        elif action in ("HOLD", "REQUEST_INFORMATION") and not note.strip():
            raise InputError(f"{action} needs a note explaining why")
        elif action == "RESOLVE":
            upd["resolution_summary"] = (note or t.get("final_response") or t.get("proposed_response") or "Resolved by agent").strip()
        elif action == "ESCALATE":
            if team:
                upd["assigned_team"] = team
                _event(con, ticket_id, "ASSIGN", t["assigned_team"], team, actor, note or "escalated", at=iso(at))
        upd.update(_set_status(con, t, target, at, actor, f"{action}: {note}".strip(": ")))
        _update(con, ticket_id, upd)
        if note:
            _event(con, ticket_id, "COMMENT", None, None, actor, note, at=iso(at))
        out = {**t, **upd}
    add_audit("review_action", t["query_id"], ticket_id, intent=t["intent"], complexity=t["complexity"], priority=t["priority"], route=t["route"], reviewer_action=action,
              ticket_status=out["status"], actor=actor, trace_id=trace_id, error_details=(note[:200] if note else None))
    return out


def mark_first_response(ticket_id: str, at: datetime | None = None, actor: str = "system") -> None:
    at = at or now()
    with tx() as con:
        con.execute("UPDATE tickets SET first_response_at=?, updated_at=? WHERE ticket_id=? AND first_response_at IS NULL", (iso(at), iso(at), ticket_id))


def set_status_system(ticket_id: str, new: str, actor: str, note: str = "", at: datetime | None = None, trace_id: str = "") -> dict:
    """Status change made by the system (auto policy, dispatch, customer reply).  Still validated against the lifecycle."""
    at = at or now()
    with tx() as con:
        con.execute("BEGIN IMMEDIATE") if not con.in_transaction else None
        t = dict(con.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone())
        if t["status"] == new:
            return t
        if not tm.can_transition(t["status"], new):
            raise InputError(f"A ticket in state {t['status']} cannot move to {new}")
        upd = _set_status(con, t, new, at, actor, note)
        _update(con, ticket_id, upd)
    add_audit("status_change", t["query_id"], ticket_id, priority=t["priority"], ticket_status=new, actor=actor, trace_id=trace_id, error_details=note or None)
    return {**t, **upd}


def reopen_ticket(ticket_id: str, actor: str, reason: str, policy: dict | None = None, at: datetime | None = None, trace_id: str = "") -> dict:
    """Customer reports failure (or an agent reopens): lifecycle state REOPENED, priority raised one level (not above P2), SLA clock restarted."""
    policy = policy or load_policy()
    at = at or now()
    if not reason.strip():
        raise InputError("A reopening needs a reason")
    with tx() as con:
        con.execute("BEGIN IMMEDIATE") if not con.in_transaction else None
        t = dict(con.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone())
        if not tm.can_transition(t["status"], "REOPENED"):
            raise InputError(f"A ticket in state {t['status']} cannot be reopened")
        if t["status"] in ("RESOLVED", "CLOSED") and t.get("resolved_at") and (at - tm.parse(t["resolved_at"])).days > tm.REOPEN_WINDOW_DAYS:
            raise InputError("The reopening window has passed; create a new ticket")
        new_pri = tm.reopen_priority(t["priority"])
        upd = _set_status(con, t, "REOPENED", at, actor, reason)
        upd.update({"priority": new_pri, "sla_priority": new_pri, "reopen_count": int(t["reopen_count"] or 0) + 1, "clock_start": iso(at), "paused_total_min": 0, "sla_paused_at": None,
                    "first_response_at": None, "resolved_at": None, "assigned_team": t["assigned_team"] if new_pri != "P1" else "Incident Response",
                    "first_response_due": iso(at + timedelta(minutes=policy["sla_minutes"][new_pri])), "resolution_due": iso(at + timedelta(minutes=policy["resolution_minutes"][new_pri]))})
        if new_pri != t["priority"]:
            _event(con, ticket_id, "PRIORITY", t["priority"], new_pri, actor, "raised on reopening", at=iso(at))
        _update(con, ticket_id, upd)
        _event(con, ticket_id, "COMMENT", None, None, actor, reason, at=iso(at))
    add_audit("ticket_reopened", t["query_id"], ticket_id, priority=new_pri, ticket_status="REOPENED", actor=actor, trace_id=trace_id, error_details=reason[:200])
    return {**t, **upd}


def add_comment(ticket_id: str, actor: str, text: str) -> None:
    with tx() as con:
        _event(con, ticket_id, "COMMENT", None, None, actor, text)


def age_tickets(policy: dict, at: datetime | None = None, trace_id: str = "") -> list[dict]:
    """Priority aging (variation 6): a ticket that has used its whole first-response window without any response moves up one priority level (P1 is never auto-assigned)."""
    at = at or now()
    moved = []
    for t in list_tickets(status=["OPEN", "PENDING_APPROVAL", "IN_REVIEW", "ESCALATED", "REOPENED"], limit=5000):
        if t["first_response_at"] or t["priority"] in ("P1", "P2") or int(t["aged"] or 0) >= 2:
            continue                                                  # aging never reaches P1; P2 already has the human-attention path
        if sla.sla_status(t, at, policy, "first_response")["state"] != "BREACHED":
            continue
        new = tm.raise_priority(t["priority"])
        with tx() as con:
            _update(con, t["ticket_id"], {"priority": new, "aged": int(t["aged"] or 0) + 1, "updated_at": iso(at)})
            _event(con, t["ticket_id"], "PRIORITY", t["priority"], new, "system:aging", "waited past first-response target", at=iso(at))
        add_audit("priority_aged", t["query_id"], t["ticket_id"], priority=new, ticket_status=t["status"], actor="system:aging", trace_id=trace_id, error_details=f"{t['priority']} -> {new}")
        moved.append({"ticket_id": t["ticket_id"], "from": t["priority"], "to": new})
    return moved


def record_alerts(alerts: list[dict], at: datetime | None = None, trace_id: str = "") -> list[dict]:
    """Store each (ticket, which, state) alert once; returns only the new ones."""
    at = at or now()
    new = []
    for a in alerts:
        with tx() as con:
            cur = con.execute("INSERT OR IGNORE INTO sla_alerts (ticket_id,which,state,at) VALUES (?,?,?,?)", (a["ticket_id"], a["which"], a["state"], iso(at)))
            fresh = cur.rowcount > 0
        if fresh:
            t = get_ticket(a["ticket_id"])
            add_audit("sla_" + a["state"].lower(), t["query_id"], a["ticket_id"], priority=t["priority"], ticket_status=t["status"], actor="system:sla-monitor", trace_id=trace_id,
                      error_details=f"{a['which']} {a['used_min']}/{a['window_min']} min")
            new.append(a)
    return new


def list_alerts(limit: int = 200) -> list[dict]:
    with tx() as con:
        return _rows(con.execute("SELECT * FROM sla_alerts ORDER BY id DESC LIMIT ?", (limit,)))


# ---------------------------------------------------------------------------------------------------------------------- notifications
def create_notification(ticket: dict, event: str, actor: str, policy: dict, approved_by: str | None = None, sim_fail_first: int = 0, trace_id: str = "", **extra) -> dict:
    n = nt.generate_notification(ticket, event, policy, **extra)
    nid = "NTF-" + n["idempotency_key"][:10].upper()
    ts = iso(now())
    with tx() as con:
        con.execute("BEGIN IMMEDIATE") if not con.in_transaction else None
        ex = con.execute("SELECT * FROM notifications WHERE idempotency_key=?", (n["idempotency_key"],)).fetchone()
        if ex:
            ex = dict(ex)
            if approved_by and ex["needs_approval"] and not ex["approved_by"]:      # the same draft, now approved by an agent
                con.execute("UPDATE notifications SET approved_by=?, state=CASE WHEN state='DRAFT' THEN 'APPROVED' ELSE state END, updated_at=? WHERE notification_id=?", (approved_by, ts, ex["notification_id"]))
                ex.update(approved_by=approved_by, state="APPROVED" if ex["state"] == "DRAFT" else ex["state"])
            return {**ex, "_duplicate": True}
        state = "APPROVED" if (n["needs_approval"] and approved_by) else "DRAFT"
        con.execute("INSERT INTO notifications (notification_id,ticket_id,event,recipient_type,to_addr,subject,message,state,needs_approval,approved_by,idempotency_key,sim_fail_first,created_at,updated_at,attempts)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)", (nid, n["ticket_id"], event, n["recipient_type"], n["to_addr"], n["subject"], n["message"], state, int(n["needs_approval"]),
                                                                  approved_by, n["idempotency_key"], sim_fail_first, ts, ts))
    add_audit("notification_drafted", ticket["query_id"], ticket["ticket_id"], priority=ticket["priority"], ticket_status=ticket["status"], notification_status=state, actor=actor,
              trace_id=trace_id, error_details=event)
    return {**n, "notification_id": nid, "state": state, "approved_by": approved_by}


def get_notification(nid: str) -> dict | None:
    with tx() as con:
        r = con.execute("SELECT * FROM notifications WHERE notification_id=?", (nid,)).fetchone()
    return dict(r) if r else None


def list_notifications(ticket_id: str | None = None, state: str | None = None, limit: int = 500) -> list[dict]:
    q, a = "SELECT * FROM notifications WHERE 1=1", []
    for col, v in (("ticket_id", ticket_id), ("state", state)):
        if v:
            q += f" AND {col} = ?"
            a.append(v)
    q += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    a.append(limit)
    with tx() as con:
        return _rows(con.execute(q, a))


def notification_attempts(nid: str) -> list[dict]:
    with tx() as con:
        return _rows(con.execute("SELECT * FROM notification_attempts WHERE notification_id=? ORDER BY id", (nid,)))


def approve_notification(nid: str, actor: str) -> None:
    with tx() as con:
        con.execute("UPDATE notifications SET approved_by=?, state=CASE WHEN state='DRAFT' THEN 'APPROVED' ELSE state END, updated_at=? WHERE notification_id=?", (actor, iso(now()), nid))


def claim_notification(nid: str) -> bool:
    """Atomically take a notification for sending; a second concurrent dispatcher gets False and sends nothing."""
    with tx() as con:
        cur = con.execute("UPDATE notifications SET state='SENDING', updated_at=? WHERE notification_id=? AND state IN ('DRAFT','APPROVED')", (iso(now()), nid))
        return cur.rowcount == 1


def record_dispatch(nid: str, result: dict, actor: str, trace_id: str = "") -> dict:
    ts = iso(now())
    n = get_notification(nid)
    with tx() as con:
        for a in result["attempts"]:
            con.execute("INSERT INTO notification_attempts (notification_id,attempt,result,error,at) VALUES (?,?,?,?,?)", (nid, a["attempt"], a["result"], a["error"], ts))
        con.execute("UPDATE notifications SET state=?, attempts=attempts+?, detail=?, updated_at=? WHERE notification_id=?",
                    (result["status"], len(result["attempts"]), result.get("error") or result.get("provider_id") or "", ts, nid))
    t = get_ticket(n["ticket_id"])
    _append_csv(PATHS.notification_csv, NOTIF_FIELDS, {"notification_id": nid, "timestamp": ts, "ticket_id": n["ticket_id"], "event": n["event"], "to_addr": n["to_addr"], "state": result["status"],
                                                     "attempts": len(result["attempts"]), "idempotency_key": n["idempotency_key"], "detail": result.get("error") or result.get("provider_id") or ""})
    for a in result["attempts"]:
        if a["result"] != "SENT":
            add_audit("notification_retry" if a["result"] == "TRANSIENT_FAILURE" else "notification_failed", t["query_id"], t["ticket_id"], priority=t["priority"], ticket_status=t["status"],
                      notification_status=a["result"], actor=actor, trace_id=trace_id, error_details=f"{n['event']} attempt {a['attempt']}: {a['error']}")
    add_audit("notification_" + ("sent" if result["status"] == "SENT" else "dead_letter"), t["query_id"], t["ticket_id"], priority=t["priority"], ticket_status=t["status"],
              notification_status=result["status"], actor=actor, trace_id=trace_id, error_details=f"{n['event']} after {len(result['attempts'])} attempt(s)")
    return get_notification(nid)


# ---------------------------------------------------------------------------------------------------------------------- reports / admin
def queue_counts() -> dict:
    with tx() as con:
        rows = _rows(con.execute("SELECT priority, status, COUNT(*) n FROM tickets GROUP BY priority, status"))
    return {"rows": rows}


def purge_prefix(prefix: str) -> dict:
    """Remove test data whose batch id starts with a prefix (audit rows are never removed)."""
    with tx() as con:
        ids = [r["ticket_id"] for r in con.execute("SELECT ticket_id FROM tickets WHERE batch_id LIKE ?", (prefix + "%",))]
        qids = [r["query_id"] for r in con.execute("SELECT query_id FROM queries WHERE batch_id LIKE ?", (prefix + "%",))]
        for tid in ids:
            con.execute("DELETE FROM ticket_events WHERE ticket_id=?", (tid,))
            con.execute("DELETE FROM response_versions WHERE ticket_id=?", (tid,))
            con.execute("DELETE FROM sla_alerts WHERE ticket_id=?", (tid,))
            con.execute("DELETE FROM notification_attempts WHERE notification_id IN (SELECT notification_id FROM notifications WHERE ticket_id=?)", (tid,))
            con.execute("DELETE FROM notifications WHERE ticket_id=?", (tid,))
        con.execute("DELETE FROM tickets WHERE batch_id LIKE ?", (prefix + "%",))
        con.execute("DELETE FROM queries WHERE batch_id LIKE ?", (prefix + "%",))
        con.execute("DELETE FROM rejected WHERE batch_id LIKE ?", (prefix + "%",))
    return {"tickets": len(ids), "queries": len(qids)}
