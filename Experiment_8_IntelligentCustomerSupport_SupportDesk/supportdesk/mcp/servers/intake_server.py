"""MCP server: intake (inspect, load, validate, import, chat message).  Tools: inspect_source, load_source, validate_queries, import_queries, submit_message."""
from __future__ import annotations

import uuid
from typing import Any

from mcp.server.fastmcp import FastMCP

import query_ingestion as qi

from ... import rbac, store
from ...errors import InputError
from .common import READ_ONLY, WRITES_LOCAL, risk_meta, run_blocking, safe_input_path

mcp = FastMCP("supportdesk-intake", instructions="Loads customer queries (CSV or JSON or a chat message), validates them, quarantines bad records and stores good ones.", log_level="WARNING")


def _load(path: str) -> list[dict]:
    try:
        return qi.load_queries(safe_input_path(path))
    except qi.IngestError as exc:
        raise InputError(str(exc)) from exc


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def inspect_source(path: str) -> dict[str, Any]:
    """Check a query file (CSV or JSON): readable, has the required columns, how many rows."""
    def work():
        p = safe_input_path(path)
        recs = _load(path)
        heads = sorted({k for r in recs for k in r}) if recs else []
        if not recs and p.suffix.lower() == ".csv":                      # a header-only file is valid: read the header itself
            import csv
            with open(p, newline="", encoding="utf-8-sig") as fh:
                heads = [h.strip() for h in next(csv.reader(fh), []) if h]
        return {"file": p.name, "format": p.suffix.lstrip(".").lower(), "rows": len(recs), "headers": heads, "missing_headers": [h for h in qi.REQUIRED_FIELDS if h not in heads]}
    return await run_blocking("inspect_source", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def load_source(path: str, offset: int = 0, limit: int = 1000) -> dict[str, Any]:
    """Read raw query records from a CSV or JSON file (a window of `limit` rows from `offset`). Unsupported or malformed files raise an input error."""
    def work():
        recs = _load(path)
        return {"file": safe_input_path(path).name, "count": len(recs), "records": recs[max(0, offset): max(0, offset) + max(1, limit)]}
    return await run_blocking("load_source", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def validate_queries(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate and normalise queries. Invalid records are returned with a reason code, never dropped silently; personal data is masked."""
    def work():
        return qi.validate_batch(records, store.existing_query_hashes())
    return await run_blocking("validate_queries", work)


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("MEDIUM"))
async def import_queries(batch_id: str, valid: list[dict[str, Any]], rejected: list[dict[str, Any]], actor: str, channel: str = "file", trace_id: str = "") -> dict[str, Any]:
    """Store validated queries (idempotent) and write quarantined records to rejected_queries.csv. Needs the 'run' permission."""
    def work():
        rbac.authorize(actor, "run", action="import queries", trace_id=trace_id)
        return {"batch_id": batch_id, **store.import_queries(batch_id, valid, rejected, actor, trace_id, channel)}
    return await run_blocking("import_queries", work)


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("MEDIUM"))
async def submit_message(customer_id: str, text: str, actor: str, channel: str = "chat", trace_id: str = "") -> dict[str, Any]:
    """Simulated chat/web-form channel: one message becomes one validated query (new query ID, current service time)."""
    def work():
        rbac.authorize(actor, "run", action="submit a message", trace_id=trace_id)
        qid = "CHAT-" + uuid.uuid4().hex[:8].upper()
        rec = {"query_id": qid, "customer_id": customer_id, "query_text": text, "created_at": store.iso(store.now()), "channel": channel}
        v = qi.validate_batch([rec], store.existing_query_hashes())
        batch = "CHAT-" + store.now().strftime("%Y%m%d")
        res = store.import_queries(batch, v["valid"], v["rejected"], actor, trace_id, channel)
        return {"query_id": qid, "batch_id": batch, "accepted": bool(v["valid"]), "problems": [f"{x['code']}: {x['reason']}" for x in v["rejected"]], **res}
    return await run_blocking("submit_message", work)


if __name__ == "__main__":
    mcp.run()
