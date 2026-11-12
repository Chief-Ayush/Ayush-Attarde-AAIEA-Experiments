"""MCP server: ingestion (inspect, load, validate, normalise, import).  Tools: inspect_source, load_source, validate_records, import_records."""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from typing_extensions import TypedDict

import recruitment_workflow as rw

from .. import rbac, store
from ..config import PATHS, ROLES_APPLIED
from ..errors import InputError
from .common import READ_ONLY, WRITES_LOCAL, risk_meta, run_blocking, safe_input_path

mcp = FastMCP("campushire-ingest", instructions="Loads candidate files (CSV or JSON), validates and normalises records, quarantines bad ones and imports good ones.",
              log_level="WARNING")


class SourceInfo(TypedDict):
    file: str
    format: str
    rows: int
    headers: list[str]
    missing_headers: list[str]
    unknown_headers: list[str]


class Loaded(TypedDict):
    file: str
    count: int
    records: list[dict[str, Any]]


class Validation(TypedDict):
    valid: list[dict[str, Any]]
    rejected: list[dict[str, Any]]
    skipped: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    counts: dict[str, int]


class Imported(TypedDict):
    drive_id: str
    imported: int
    repeats: int
    rejected: int


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def inspect_source(path: str) -> SourceInfo:
    """Check a candidate file (CSV or JSON): readable, has a header, which required columns are missing."""
    def work():
        p = safe_input_path(path)
        recs = rw.load_candidates(p)
        headers = list(recs[0].keys()) if recs else []
        if p.suffix.lower() == ".csv" and not headers:
            import csv
            with open(p, newline="", encoding="utf-8-sig") as fh:
                headers = [h.strip() for h in next(csv.reader(fh), [])]
        return {"file": p.name, "format": p.suffix.lstrip(".").lower(), "rows": len(recs), "headers": headers,
                "missing_headers": [h for h in rw.REQUIRED_FIELDS if h not in headers], "unknown_headers": [h for h in headers if h not in rw.ALL_FIELDS]}
    return await run_blocking("inspect_source", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def load_source(path: str, offset: int = 0, limit: int = 1000) -> Loaded:
    """Read raw candidate records from a CSV or JSON file (a window of `limit` rows from `offset`). Malformed files raise an input error."""
    def work():
        p = safe_input_path(path)
        recs = rw.load_candidates(p)
        win = recs[max(0, offset): max(0, offset) + max(1, limit)]
        return {"file": p.name, "count": len(recs), "records": win}
    return await run_blocking("load_source", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def validate_records(records: list[dict[str, Any]], drive_id: str = "") -> Validation:
    """Validate and normalise records. Duplicates (within the batch or already in the drive) are quarantined with a reason, never dropped silently."""
    def work():
        existing = store.existing_hashes(drive_id) if drive_id else {}
        return rw.validate_batch(records, ROLES_APPLIED, existing)
    return await run_blocking("validate_records", work)


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("MEDIUM"))
async def import_records(drive_id: str, valid: list[dict[str, Any]], rejected: list[dict[str, Any]], actor: str, trace_id: str = "") -> Imported:
    """Store validated candidates in the drive (idempotent) and write quarantined records to rejected_records.csv. Needs the 'run' permission."""
    def work():
        rbac.authorize(actor, "run", drive_id=drive_id, action="import candidates", trace_id=trace_id)
        res = store.import_records(drive_id, valid, rejected, actor, trace_id)
        return {"drive_id": drive_id, **res}
    return await run_blocking("import_records", work)


if __name__ == "__main__":
    mcp.run()
