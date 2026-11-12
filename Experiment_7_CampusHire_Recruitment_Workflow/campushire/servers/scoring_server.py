"""MCP server: rules, weighted scoring, routing, rule-version comparison and fairness review."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from mcp.server.fastmcp import FastMCP
from typing_extensions import TypedDict

import recruitment_workflow as rw

from .. import rbac, store
from ..config import PATHS
from ..errors import InputError
from .common import DESTRUCTIVE, READ_ONLY, WRITES_LOCAL, risk_meta, run_blocking

mcp = FastMCP("campushire-scoring", instructions="Applies the versioned rules in rules.json. Candidate free text is never used for scoring.", log_level="WARNING")


class RulesInfo(TypedDict):
    active_version: str
    versions: list[str]
    rules: dict[str, Any]


class Scored(TypedDict):
    rule_version: str
    count: int
    results: list[dict[str, Any]]
    failed: list[dict[str, Any]]


class Routed(TypedDict):
    score: float
    status: str


class Saved(TypedDict):
    drive_id: str
    saved: int


def _cfg() -> dict:
    return rw.load_rules(PATHS.rules_json)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def get_rules(version: str = "") -> RulesInfo:
    """Return the rule set (weights and thresholds per role) of a version; the active version when none is given. Malformed rules raise an input error."""
    def work():
        cfg = _cfg()
        ver = version or cfg.get("active_version")
        if ver not in cfg["versions"]:
            raise InputError(f"Unknown rule version: {ver}")
        return {"active_version": cfg.get("active_version"), "versions": list(cfg["versions"]), "rules": {"version": ver, **cfg["versions"][ver]}}
    return await run_blocking("get_rules", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def score_candidates(candidates: list[dict[str, Any]], version: str = "") -> Scored:
    """Weighted score (0-100, two decimals), criterion-wise explanation and proposed status for each candidate. One bad record does not stop the batch."""
    def work():
        cfg = _cfg()
        out, failed = [], []
        ver = version or cfg.get("active_version")
        for c in candidates:
            try:
                r = rw.calculate_score(c, cfg, ver)
                r["proposed_status"] = rw.route_candidate(r.pop("_exact", r["score"]), r["thresholds"])      # routed on the unrounded score: 74.995 stays below 75
                r["explanation"] = rw.explain(r)
                out.append(r)
            except InputError as exc:
                failed.append({"candidate_id": c.get("candidate_id", ""), "reason": str(exc)})
        return {"rule_version": ver, "count": len(out), "results": out, "failed": failed}
    return await run_blocking("score_candidates", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def route_score(score: float, role: str, version: str = "") -> Routed:
    """Map a score to its proposed status using the thresholds of the role (75 and above, 50 to 74.99, below 50)."""
    def work():
        _, rr = rw.rules_for(_cfg(), role, version or None)
        return {"score": score, "status": rw.route_candidate(score, rr["thresholds"])}
    return await run_blocking("route_score", work)


@mcp.tool(annotations=WRITES_LOCAL, meta=risk_meta("MEDIUM"))
async def save_results(drive_id: str, results: list[dict[str, Any]], run_id: str, actor: str, trace_id: str = "") -> Saved:
    """Persist scores and proposed statuses and write one audit record per candidate. Needs the 'run' permission."""
    def work():
        rbac.authorize(actor, "run", drive_id=drive_id, action="save scoring results", trace_id=trace_id)
        return {"drive_id": drive_id, "saved": store.save_results(drive_id, results, run_id, actor, trace_id)}
    return await run_blocking("save_results", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def compare_rule_versions(drive_id: str, version_a: str, version_b: str) -> dict[str, Any]:
    """Re-score the drive's candidates under two rule versions (nothing is saved) and report who moves between statuses."""
    def work():
        cfg = _cfg()
        cands = store.candidates(drive_id)
        moves, same = [], 0
        rank = {"NOT_SHORTLISTED_REVIEW": 0, "MANUAL_REVIEW": 1, "SHORTLIST_REVIEW": 2}
        counts = {version_a: defaultdict(int), version_b: defaultdict(int)}
        for c in cands:
            ra, rb = rw.calculate_score(c, cfg, version_a), rw.calculate_score(c, cfg, version_b)
            sa, sb = rw.route_candidate(ra.get("_exact", ra["score"]), ra["thresholds"]), rw.route_candidate(rb.get("_exact", rb["score"]), rb["thresholds"])
            counts[version_a][sa] += 1
            counts[version_b][sb] += 1
            if sa == sb:
                same += 1
            else:
                moves.append({"candidate_id": c["candidate_id"], "role": c["application_role"], f"score_{version_a}": ra["score"], f"score_{version_b}": rb["score"],
                              "from": sa, "to": sb, "direction": "up" if rank[sb] > rank[sa] else "down"})
        n = len(cands)
        return {"drive_id": drive_id, "candidates": n, "unchanged": same, "changed": len(moves), "stability": round(same / n, 4) if n else 1.0,
                "distribution": {k: dict(v) for k, v in counts.items()}, "moves": moves}
    return await run_blocking("compare_rule_versions", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def fairness_review(drive_id: str) -> dict[str, Any]:
    """Compare shortlist rates across colleges and roles (four-fifths rule of thumb, groups of 3 or more) and list the criteria that may favour some groups."""
    def work():
        res = store.results(drive_id)
        out = {"drive_id": drive_id, "scored": len(res), "groups": {}, "flags": [], "criteria_notes": [
            "Technical assessment depends on test access and preparation time, which may differ between colleges.",
            "Relevant experience favours candidates with access to internships and may disadvantage first-generation applicants.",
            "Skills scores are self-reported or assessor-rated and should be audited for consistency."]}
        for dim, key in (("college", "college"), ("role", "role")):
            g = defaultdict(lambda: [0, 0])
            for r in res:
                k = r.get(key) or "(not given)"
                g[k][0] += 1
                g[k][1] += r["proposed_status"] == "SHORTLIST_REVIEW"
            rows = [{"group": k, "n": n, "shortlist_review": s, "rate": round(s / n, 4)} for k, (n, s) in sorted(g.items())]
            elig = [r for r in rows if r["n"] >= 3]
            top = max((r["rate"] for r in elig), default=0)
            for r in rows:
                r["ratio_to_highest"] = round(r["rate"] / top, 3) if top and r["n"] >= 3 else None
                if r["ratio_to_highest"] is not None and r["ratio_to_highest"] < 0.8:
                    out["flags"].append(f"{dim} '{r['group']}': shortlist-review rate {r['rate']:.0%} is {r['ratio_to_highest']:.0%} of the highest group (below 80%) - a human should check the criteria")
            out["groups"][dim] = rows
        return out
    return await run_blocking("fairness_review", work)


@mcp.tool(annotations=DESTRUCTIVE, meta=risk_meta("HIGH"))
async def set_active_version(version: str, actor: str) -> dict[str, Any]:
    """Change which rule version new scoring runs use (admin only; existing scores keep their recorded version). The change is audited."""
    def work():
        import json
        rbac.authorize(actor, "rules", action="change the active rule version")
        cfg = _cfg()
        if version not in cfg["versions"]:
            raise InputError(f"Unknown rule version: {version}")
        old = cfg.get("active_version")
        cfg["active_version"] = version
        cfg["rule_version"] = version
        rw.validate_rules(cfg)
        PATHS.rules_json.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        store.add_audit("rules_activated", "", rule_version=version, error_details=f"{old} -> {version}", actor=actor)
        return {"previous": old, "active": version}
    return await run_blocking("set_active_version", work)


@mcp.resource("rules://active", mime_type="application/json", description="Active scoring rules")
def active_rules() -> str:
    import json
    return json.dumps(_cfg(), indent=1)


if __name__ == "__main__":
    mcp.run()
