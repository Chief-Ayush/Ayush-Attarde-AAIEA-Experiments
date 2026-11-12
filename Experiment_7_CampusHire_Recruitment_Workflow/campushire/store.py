"""SQLite persistence (variation 3) with an append-only, hash-chained audit log (lab step 10).

* One database file, WAL mode, a short-lived connection per operation, so the four MCP server processes can write safely.
* The audit table rejects UPDATE and DELETE with triggers and every row carries the hash of the previous row, so tampering that
  bypasses the triggers is still detected by `verify_audit_chain()`.
* audit_log.csv and rejected_records.csv (the files named in the manual) are append-only exports of the same records.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from recruitment_workflow import csv_safe

from .config import PATHS
from .errors import InputError

_CSV_LOCK = threading.Lock()
AUDIT_FIELDS = ["timestamp", "drive_id", "candidate_id", "event", "validation_status", "score", "score_breakdown", "rule_version", "proposed_status",
                "reviewer_action", "final_status", "notification_status", "error_details", "actor", "trace_id"]
REJECT_FIELDS = ["timestamp", "drive_id", "row", "candidate_id", "candidate_name", "code", "reason", "next_action"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS drives (drive_id TEXT PRIMARY KEY, name TEXT, created_at TEXT, created_by TEXT, rule_version TEXT);
CREATE TABLE IF NOT EXISTS candidates (drive_id TEXT, candidate_id TEXT, record_hash TEXT, candidate_name TEXT, email TEXT, role TEXT, college TEXT,
  skills REAL, experience REAL, assessment REAL, cover_note TEXT, flags TEXT, imported_at TEXT, PRIMARY KEY (drive_id, candidate_id));
CREATE TABLE IF NOT EXISTS rejected (id INTEGER PRIMARY KEY AUTOINCREMENT, drive_id TEXT, row INTEGER, candidate_id TEXT, candidate_name TEXT, code TEXT,
  reason TEXT, next_action TEXT, record TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS results (drive_id TEXT, candidate_id TEXT, score REAL, breakdown TEXT, rule_version TEXT, proposed_status TEXT,
  processed_at TEXT, run_id TEXT, PRIMARY KEY (drive_id, candidate_id));
CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, drive_id TEXT, candidate_id TEXT, action TEXT, reviewer TEXT, reviewer_role TEXT,
  final_status TEXT, note TEXT, override_reason TEXT, decided_at TEXT);
CREATE TABLE IF NOT EXISTS notifications (draft_id TEXT PRIMARY KEY, drive_id TEXT, candidate_id TEXT, to_addr TEXT, subject TEXT, message TEXT, template TEXT,
  state TEXT, edited_by TEXT, approved_by TEXT, attempts INTEGER DEFAULT 0, detail TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, drive_id TEXT, candidate_id TEXT, waiting_hours REAL, created_at TEXT);
CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, drive_id TEXT, started_at TEXT, finished_at TEXT, mode TEXT, rule_version TEXT, actor TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, drive_id TEXT, candidate_id TEXT, event TEXT, validation_status TEXT,
  score REAL, score_breakdown TEXT, rule_version TEXT, proposed_status TEXT, reviewer_action TEXT, final_status TEXT, notification_status TEXT,
  error_details TEXT, actor TEXT, trace_id TEXT, prev_hash TEXT, row_hash TEXT);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT, 'audit records are append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT, 'audit records are append-only'); END;
CREATE INDEX IF NOT EXISTS ix_audit_cand ON audit (drive_id, candidate_id);
"""


def now_iso(offset_hours: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=offset_hours)).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(PATHS.db, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=8000")
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
    with tx() as con:
        con.executescript(SCHEMA)


init_db()


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------------------------------------------------- audit
def _hash(prev: str, payload: dict) -> str:
    return hashlib.sha256((prev + json.dumps(payload, sort_keys=True, default=str)).encode()).hexdigest()[:24]


def add_audit(event: str, drive_id: str = "", candidate_id: str = "", **kw) -> int:
    rec = {"timestamp": now_iso(), "drive_id": drive_id or "", "candidate_id": candidate_id or "", "event": event}
    for f in AUDIT_FIELDS[4:]:
        v = kw.get(f)
        rec[f] = json.dumps(v) if isinstance(v, (list, dict)) else v
    with tx() as con:
        con.execute("BEGIN IMMEDIATE") if not con.in_transaction else None
        last = con.execute("SELECT row_hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
        prev = last["row_hash"] if last else "GENESIS"
        rec["prev_hash"], rec["row_hash"] = prev, _hash(prev, rec)
        cols = ",".join(rec)
        cur = con.execute(f"INSERT INTO audit ({cols}) VALUES ({','.join('?' * len(rec))})", list(rec.values()))
        return cur.lastrowid


def query_audit(drive_id: str | None = None, candidate_id: str | None = None, event: str | None = None, limit: int = 500, after_id: int = 0) -> list[dict]:
    q, a = "SELECT * FROM audit WHERE id > ?", [after_id]
    for col, v in (("drive_id", drive_id), ("candidate_id", candidate_id), ("event", event)):
        if v:
            q += f" AND {col} = ?"
            a.append(v)
    q += " ORDER BY id DESC LIMIT ?"
    a.append(limit)
    with tx() as con:
        return _rows(con.execute(q, a))


def verify_audit_chain() -> dict:
    """Recompute the hash chain.  Returns {ok, rows, first_bad_id}."""
    with tx() as con:
        rows = _rows(con.execute("SELECT * FROM audit ORDER BY id"))
    prev = "GENESIS"
    for r in rows:
        payload = {k: r[k] for k in ["timestamp", "drive_id", "candidate_id", "event"] + AUDIT_FIELDS[4:]}
        if r["prev_hash"] != prev or r["row_hash"] != _hash(prev, {**payload, "prev_hash": prev}) and r["row_hash"] != _hash(prev, payload):
            return {"ok": False, "rows": len(rows), "first_bad_id": r["id"]}
        prev = r["row_hash"]
    return {"ok": True, "rows": len(rows), "first_bad_id": None}


def export_audit_csv() -> dict:
    """Append the audit rows that are not yet in audit_log.csv (never rewrites existing lines)."""
    with _CSV_LOCK:
        done = 0
        if PATHS.audit_csv.exists():
            with open(PATHS.audit_csv, "r", newline="", encoding="utf-8") as fh:
                done = max(0, sum(1 for _ in csv.reader(fh)) - 1)
        with tx() as con:
            rows = _rows(con.execute("SELECT * FROM audit ORDER BY id LIMIT -1 OFFSET ?", (done,)))
        new = not PATHS.audit_csv.exists() or PATHS.audit_csv.stat().st_size == 0
        with open(PATHS.audit_csv, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=AUDIT_FIELDS)
            if new:
                w.writeheader()
            for r in rows:
                w.writerow({k: csv_safe(r[k]) for k in AUDIT_FIELDS})
        return {"appended": len(rows), "total": done + len(rows)}


# ---------------------------------------------------------------------------------------------------------------------- drives
def create_drive(name: str, created_by: str, rule_version: str, drive_id: str | None = None) -> dict:
    did = drive_id or "D" + datetime.now().strftime("%y%m%d") + "-" + uuid.uuid4().hex[:4].upper()
    with tx() as con:
        if con.execute("SELECT 1 FROM drives WHERE drive_id=?", (did,)).fetchone():
            raise InputError(f"Drive {did} already exists")
        con.execute("INSERT INTO drives VALUES (?,?,?,?,?)", (did, name, now_iso(), created_by, rule_version))
    add_audit("drive_created", did, actor=created_by, rule_version=rule_version, error_details=None)
    return get_drive(did)


def get_drive(drive_id: str) -> dict:
    with tx() as con:
        r = con.execute("SELECT * FROM drives WHERE drive_id=?", (drive_id,)).fetchone()
    if not r:
        raise InputError(f"Unknown drive: {drive_id}")
    return dict(r)


def list_drives() -> list[dict]:
    with tx() as con:
        rows = _rows(con.execute("""SELECT d.*, (SELECT COUNT(*) FROM candidates c WHERE c.drive_id=d.drive_id) AS candidates,
                                    (SELECT COUNT(*) FROM rejected r WHERE r.drive_id=d.drive_id) AS rejected FROM drives d ORDER BY created_at DESC"""))
    return rows


# ---------------------------------------------------------------------------------------------------------------------- candidates
def existing_hashes(drive_id: str) -> dict[str, str]:
    with tx() as con:
        return {r["candidate_id"]: r["record_hash"] for r in con.execute("SELECT candidate_id, record_hash FROM candidates WHERE drive_id=?", (drive_id,))}


def import_records(drive_id: str, valid: list[dict], rejected: list[dict], actor: str, trace_id: str = "") -> dict:
    """Idempotent import: a candidate_id that is already in the drive is never inserted twice."""
    get_drive(drive_id)
    new = repeat = 0
    ts = now_iso()
    with tx() as con:
        for c in valid:
            cur = con.execute("INSERT OR IGNORE INTO candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (drive_id, c["candidate_id"], c["record_hash"], c["candidate_name"], c["email"], c["application_role"], c.get("college", ""),
                               float(c["skills_score"]), float(c["experience_score"]), float(c["assessment_score"]), c.get("cover_note", ""),
                               json.dumps(c.get("flags", [])), ts))
            new += cur.rowcount
            repeat += 1 - cur.rowcount
        for r in rejected:
            con.execute("INSERT INTO rejected (drive_id,row,candidate_id,candidate_name,code,reason,next_action,record,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (drive_id, r["row"], r["candidate_id"], r["candidate_name"], r["code"], r["reason"], r["next_action"], json.dumps(r.get("raw", {})), ts))
    with _CSV_LOCK:
        newfile = not PATHS.rejected_csv.exists() or PATHS.rejected_csv.stat().st_size == 0
        with open(PATHS.rejected_csv, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=REJECT_FIELDS)
            if newfile:
                w.writeheader()
            for r in rejected:
                w.writerow({"timestamp": ts, "drive_id": drive_id, "row": r["row"], "candidate_id": csv_safe(r["candidate_id"]),
                            "candidate_name": csv_safe(r["candidate_name"]), "code": r["code"], "reason": csv_safe(r["reason"]), "next_action": r["next_action"]})
    for c in valid:
        add_audit("candidate_ingested", drive_id, c["candidate_id"], validation_status="valid", actor=actor, trace_id=trace_id,
                  error_details=json.dumps(c["flags"]) if c.get("flags") else None)
    for r in rejected:
        add_audit("candidate_quarantined", drive_id, r["candidate_id"], validation_status="invalid", actor=actor, trace_id=trace_id,
                  error_details=f"{r['code']}: {r['reason']}")
    return {"imported": new, "repeats": repeat, "rejected": len(rejected)}


def candidates(drive_id: str, candidate_ids: list[str] | None = None) -> list[dict]:
    q, a = "SELECT * FROM candidates WHERE drive_id=?", [drive_id]
    if candidate_ids:
        q += f" AND candidate_id IN ({','.join('?' * len(candidate_ids))})"
        a += list(candidate_ids)
    with tx() as con:
        rows = _rows(con.execute(q + " ORDER BY candidate_id", a))
    for r in rows:
        r["flags"] = json.loads(r["flags"] or "[]")
        r.update(candidate_name=r["candidate_name"], application_role=r["role"], skills_score=r["skills"], experience_score=r["experience"], assessment_score=r["assessment"])
    return rows


def rejected(drive_id: str) -> list[dict]:
    with tx() as con:
        return _rows(con.execute("SELECT id,row,candidate_id,candidate_name,code,reason,next_action,created_at FROM rejected WHERE drive_id=? ORDER BY id", (drive_id,)))


# ---------------------------------------------------------------------------------------------------------------------- results
def save_results(drive_id: str, results: list[dict], run_id: str, actor: str, trace_id: str = "") -> int:
    ts = now_iso()
    with tx() as con:
        for r in results:
            con.execute("INSERT OR REPLACE INTO results VALUES (?,?,?,?,?,?,?,?)", (drive_id, r["candidate_id"], r["score"], json.dumps(r["breakdown"]),
                                                                                   r["rule_version"], r["proposed_status"], ts, run_id))
    for r in results:
        add_audit("candidate_scored", drive_id, r["candidate_id"], validation_status="valid", score=r["score"], score_breakdown=r["breakdown"],
                  rule_version=r["rule_version"], proposed_status=r["proposed_status"], actor=actor, trace_id=trace_id)
    return len(results)


def results(drive_id: str) -> list[dict]:
    with tx() as con:
        rows = _rows(con.execute("""SELECT r.*, c.candidate_name, c.email, c.role, c.college, c.flags FROM results r JOIN candidates c
                                    ON c.drive_id=r.drive_id AND c.candidate_id=r.candidate_id WHERE r.drive_id=? ORDER BY r.score DESC, r.candidate_id""", (drive_id,)))
    for r in rows:
        r["breakdown"] = json.loads(r["breakdown"])
        r["flags"] = json.loads(r["flags"] or "[]")
    return rows


# ---------------------------------------------------------------------------------------------------------------------- reviews
def latest_reviews(drive_id: str) -> dict[str, dict]:
    with tx() as con:
        rows = _rows(con.execute("SELECT * FROM reviews WHERE id IN (SELECT MAX(id) FROM reviews WHERE drive_id=? GROUP BY candidate_id)", (drive_id,)))
    return {r["candidate_id"]: r for r in rows}


def add_review(drive_id: str, candidate_id: str, action: str, reviewer: str, role: str, final_status: str, note: str, override_reason: str) -> int:
    with tx() as con:
        return con.execute("INSERT INTO reviews (drive_id,candidate_id,action,reviewer,reviewer_role,final_status,note,override_reason,decided_at) VALUES (?,?,?,?,?,?,?,?,?)",
                           (drive_id, candidate_id, action, reviewer, role, final_status, note, override_reason, now_iso())).lastrowid


def review_history(drive_id: str, candidate_id: str | None = None) -> list[dict]:
    q, a = "SELECT * FROM reviews WHERE drive_id=?", [drive_id]
    if candidate_id:
        q += " AND candidate_id=?"
        a.append(candidate_id)
    with tx() as con:
        return _rows(con.execute(q + " ORDER BY id", a))


# ---------------------------------------------------------------------------------------------------------------------- notifications
def save_draft(drive_id: str, d: dict) -> str:
    """One live draft per candidate: an existing draft that was not sent is replaced, a sent one is kept (no duplicate processing)."""
    with tx() as con:
        cur = con.execute("SELECT draft_id, state FROM notifications WHERE drive_id=? AND candidate_id=? ORDER BY updated_at DESC", (drive_id, d["candidate_id"])).fetchone()
        if cur and cur["state"] == "SENT":
            return cur["draft_id"]
        did = cur["draft_id"] if cur else f"N-{drive_id}-{d['candidate_id']}"
        con.execute("INSERT OR REPLACE INTO notifications VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (did, drive_id, d["candidate_id"], d["to"], d["subject"], d["message"], d["template"], "DRAFT", None, None, 0, "", now_iso()))
        return did


def get_draft(draft_id: str) -> dict:
    with tx() as con:
        r = con.execute("SELECT * FROM notifications WHERE draft_id=?", (draft_id,)).fetchone()
    if not r:
        raise InputError(f"Unknown draft: {draft_id}")
    return dict(r)


def drafts(drive_id: str, state: str | None = None) -> list[dict]:
    q, a = "SELECT * FROM notifications WHERE drive_id=?", [drive_id]
    if state:
        q += " AND state=?"
        a.append(state)
    with tx() as con:
        return _rows(con.execute(q + " ORDER BY candidate_id", a))


def update_draft(draft_id: str, **fields) -> None:
    allowed = {"subject", "message", "state", "edited_by", "approved_by", "attempts", "detail", "to_addr"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    with tx() as con:
        con.execute(f"UPDATE notifications SET {', '.join(k + '=?' for k in sets)}, updated_at=? WHERE draft_id=?", [*sets.values(), now_iso(), draft_id])


# ---------------------------------------------------------------------------------------------------------------------- reports
def queue(drive_id: str) -> list[dict]:
    """Everything the human review screen needs, one row per scored candidate."""
    res, rev, dr = results(drive_id), latest_reviews(drive_id), {d["candidate_id"]: d for d in drafts(drive_id)}
    out = []
    for r in res:
        v = rev.get(r["candidate_id"])
        d = dr.get(r["candidate_id"])
        out.append({**r, "review": v, "final_status": v["final_status"] if v else None, "reviewer_action": v["action"] if v else None,
                    "draft_id": d["draft_id"] if d else None, "notification_state": d["state"] if d else "NONE",
                    "reviewed": v is not None})
    return out


def reset_drive(drive_id: str, actor: str) -> None:
    """Admin only: clear a drive's working data.  The audit log is never touched."""
    with tx() as con:
        for t in ("candidates", "rejected", "results", "reviews", "notifications", "reminders"):
            con.execute(f"DELETE FROM {t} WHERE drive_id=?", (drive_id,))
    add_audit("drive_reset", drive_id, actor=actor)
