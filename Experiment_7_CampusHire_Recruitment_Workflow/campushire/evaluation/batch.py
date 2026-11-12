"""Batch testing: run many candidate files through the real workflow, compare execution modes, keep every batch on disk."""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime

import pandas as pd

from .. import store
from ..config import PATHS, Settings
from ..workflow import run_drive
from .metrics import percentile
from .suites import ADMIN, REVIEWER, Env


def batch_item_row(path: str, mode: str, version: str, env: Env, label: str | None = None, run: int = 1) -> dict:
    did = env.drive("batch")
    s = Settings(execution_mode=mode, rule_version=version, approval_mode="auto")
    t0 = time.perf_counter()
    res = run_drive(path, REVIEWER, drive_id=did, reviewer=REVIEWER, settings=s, hub=env.hub)
    st = res["state"]
    rep = st.get("report") or {}
    k = st.get("counts") or {}
    sig = hashlib.sha256(json.dumps(sorted((r["candidate_id"], r["score"], r["proposed_status"]) for r in store.results(did))).encode()).hexdigest()[:10]
    valid = k.get("valid", 0)
    return {"file": label or path.rsplit("/", 1)[-1], "mode": mode, "rule_version": version or "active", "run": run, "status": st.get("status"),
            "records": k.get("total", 0), "valid": valid, "quarantined": k.get("rejected", 0), "duplicates": k.get("duplicates", 0), "scored": rep.get("scored", 0),
            "drafts": sum((rep.get("notifications") or {}).values()), "sent": (rep.get("notifications") or {}).get("SENT", 0),
            "failed_delivery": (rep.get("notifications") or {}).get("FAILED", 0), "not_sent": (rep.get("notifications") or {}).get("NOT_SENT", 0),
            "reviewed": rep.get("reviewed", 0), "tool_calls": res["tool_calls"], "failed_calls": len(res["failures"]),
            "wall_ms": res["total_ms"], "ms_per_valid": round(res["total_ms"] / valid, 1) if valid else None, "output_hash": sig, "drive_id": did,
            "elapsed_s": round(time.perf_counter() - t0, 3)}


def run_batch(files: list[tuple[str, str]], modes: list[str], versions: list[str], reps: int = 1, progress=None) -> pd.DataFrame:
    env = Env()
    rows, total, n = [], len(files) * len(modes) * len(versions) * reps, 0
    for label, path in files:
        for ver in versions:
            for mode in modes:
                for r in range(reps):
                    rows.append(batch_item_row(path, mode, ver, env, label, r + 1))
                    n += 1
                    if progress:
                        progress(n / total, f"{label} · {mode} · rules {ver or 'active'}")
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    return {"runs": len(df), "files": int(df["file"].nunique()), "avg_wall_ms": round(float(df["wall_ms"].mean())), "p95_wall_ms": round(percentile(df["wall_ms"], 95)),
            "records": int(df.groupby("file")["records"].first().sum()), "valid": int(df.groupby("file")["valid"].first().sum()),
            "avg_ms_per_valid": round(float(df["ms_per_valid"].mean()), 1), "failed_calls": int(df["failed_calls"].sum()),
            "completed_pct": round(100 * float((df["status"] == "completed").mean()), 1),
            "consistent_pct": round(100 * float(df.groupby(["file", "rule_version"])["output_hash"].nunique().eq(1).mean()), 1)}


def save_batch(df: pd.DataFrame, meta: dict) -> str:
    name = f"batch_{datetime.now():%Y%m%d_%H%M%S}"
    d = PATHS.batches / name
    d.mkdir(parents=True, exist_ok=True)
    df.to_csv(d / "results.csv", index=False)
    (d / "meta.json").write_text(json.dumps({"name": name, "created": datetime.now().isoformat(timespec="seconds"), **meta, "summary": summarize(df)}, indent=1, default=str),
                                 encoding="utf-8")
    return name


def list_batches() -> list[dict]:
    out = []
    for d in sorted(PATHS.batches.glob("batch_*"), reverse=True):
        try:
            out.append(json.loads((d / "meta.json").read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue
    return out


def load_batch(name: str) -> pd.DataFrame:
    df = pd.read_csv(PATHS.batches / name / "results.csv")
    return df
