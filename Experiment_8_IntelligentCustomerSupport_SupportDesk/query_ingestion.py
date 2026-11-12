"""Query ingestion and validation (lab steps 2-3): CSV or JSON in, valid and rejected records out, nothing dropped silently."""
from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

import classifier

REQUIRED_FIELDS = ["query_id", "customer_id", "query_text", "created_at"]
OPTIONAL_FIELDS = ["channel"]
MAX_TEXT = 2000


class IngestError(ValueError):
    """The file as a whole cannot be used (unsupported format, unreadable, malformed, missing columns)."""


def load_queries(path: str | Path) -> list[dict]:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix not in (".csv", ".json"):
        raise IngestError(f"Unsupported file format '{suffix or 'none'}': use .csv or .json")
    try:
        if suffix == ".csv":
            with open(p, newline="", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                if not reader.fieldnames:
                    raise IngestError("The CSV file has no header row")
                names = [h.strip() for h in reader.fieldnames if h]
                missing = [f for f in REQUIRED_FIELDS if f not in names]
                if missing:
                    raise IngestError("Missing required CSV columns: " + ", ".join(missing))
                return [{(k or "").strip(): v for k, v in row.items()} for row in reader]
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict) and isinstance(data.get("queries"), list):
            data = data["queries"]
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise IngestError("JSON must be a list of query objects")
        return data
    except FileNotFoundError as exc:
        raise IngestError(f"File not found: {p.name}") from exc
    except (json.JSONDecodeError, csv.Error, UnicodeDecodeError) as exc:
        raise IngestError(f"Malformed {suffix[1:].upper()} file ({exc.__class__.__name__}: {str(exc)[:80]})") from exc


def record_hash(r: dict) -> str:
    return hashlib.sha256(json.dumps([r["query_id"], r["customer_id"], r["query_text"], r["created_at"]], ensure_ascii=False).encode()).hexdigest()[:16]


def _clean(v) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(v if v is not None else "")).strip()


def validate_batch(records: list[dict], existing_hashes: dict | None = None) -> dict:
    """Return {valid, rejected, counts}.  Rules: required fields, non-empty text, unique query_id, trimmed text, sane timestamp and customer id."""
    seen: dict[str, str] = {}
    existing_hashes = existing_hashes or {}
    valid, rejected = [], []

    def reject(i, rec, code, reason, nxt):
        rejected.append({"row": i, "record": {k: (v if isinstance(v, (str, int, float, type(None))) else str(v)) for k, v in rec.items()}, "code": code, "reason": reason, "next_action": nxt})

    for i, raw in enumerate(records, start=2):
        rec = {k: _clean(v) for k, v in raw.items() if k}
        missing = [f for f in REQUIRED_FIELDS if not rec.get(f)]
        if missing:
            code = "EMPTY_QUERY" if missing == ["query_text"] else "MISSING_FIELD"
            reject(i, rec, code, "Missing or empty: " + ", ".join(missing), "Correct the record and resubmit")
            continue
        if rec["query_id"] in seen:
            reject(i, rec, "DUPLICATE_ID", f"Duplicate query ID within the file (first seen on row {seen[rec['query_id']]})", "Use a unique query ID")
            continue
        seen[rec["query_id"]] = str(i)
        if not re.fullmatch(r"[A-Za-z0-9_-]{2,32}", rec["query_id"]):
            reject(i, rec, "BAD_ID", "query_id may contain letters, digits, '-' and '_' only", "Correct the ID")
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]{2,32}", rec["customer_id"]):
            reject(i, rec, "BAD_CUSTOMER_ID", "customer_id must be a plain reference (no names or email addresses)", "Replace with a customer reference")
            continue
        try:
            datetime.fromisoformat(rec["created_at"].replace("Z", "+00:00"))
        except ValueError:
            reject(i, rec, "BAD_TIMESTAMP", "created_at is not an ISO date-time", "Use e.g. 2026-10-22T09:15:00")
            continue
        if len(rec["query_text"]) > MAX_TEXT:
            reject(i, rec, "TEXT_TOO_LONG", f"query_text exceeds {MAX_TEXT} characters", "Shorten the text")
            continue
        scan = classifier.scan_text(rec["query_text"])
        out = {"row": i, "query_id": rec["query_id"], "customer_id": rec["customer_id"], "query_text": rec["query_text"], "created_at": rec["created_at"],
               "channel": rec.get("channel") or "file", "flags": {k: v for k, v in scan.items() if v}}
        out["record_hash"] = record_hash(out)
        out["query_text"] = classifier.redact(out["query_text"])      # personal data (emails, card numbers, secrets) is masked before anything is stored
        valid.append(out)
    return {"valid": valid, "rejected": rejected, "counts": {"total": len(records), "valid": len(valid), "rejected": len(rejected)}}
