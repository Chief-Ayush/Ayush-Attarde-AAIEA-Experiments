"""MCP server: classification, knowledge search and routing.  Tools: classify_queries, search_knowledge, decide_routes, list_articles, check_knowledge_base."""
from __future__ import annotations

import time
from typing import Any

from mcp.server.fastmcp import FastMCP

import classifier
import resolver
import workflow_router as wr

from ...config import PATHS, load_policy
from ...errors import InputError
from .common import READ_ONLY, risk_meta, run_blocking, safe_input_path

mcp = FastMCP("supportdesk-knowledge", instructions="Classifies queries, searches the approved knowledge base and proposes a route. It never changes tickets.", log_level="WARNING")


try:                                       # load the model and index once at start-up so the first call is fast
    classifier.get_model()
    resolver.cosine_ranking("warm up", resolver.load_knowledge_base(PATHS.kb_json))
except Exception:                          # noqa: BLE001 - a bad knowledge base is reported by the tools, not at start-up
    pass


def _kb():
    try:
        return resolver.load_knowledge_base(PATHS.kb_json)
    except resolver.KnowledgeBaseError as exc:
        raise InputError(str(exc)) from exc


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def classify_queries(queries: list[dict[str, Any]], method: str = "learned") -> dict[str, Any]:
    """Classify each query by intent, complexity and priority. Critical outage and security rules run first. method: learned | rules."""
    def work():
        if method not in ("learned", "rules"):
            raise InputError("method must be 'learned' or 'rules'")
        out = []
        t0 = time.perf_counter()
        for q in queries:
            t = time.perf_counter()
            c = classifier.classify_query(str(q.get("query_text", "")), method)
            out.append({"query_id": q.get("query_id"), **c, "ms": round((time.perf_counter() - t) * 1000, 2)})
        return {"results": out, "count": len(out), "total_ms": round((time.perf_counter() - t0) * 1000, 1)}
    return await run_blocking("classify_queries", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def search_knowledge(items: list[dict[str, Any]], method: str = "semantic") -> dict[str, Any]:
    """Search approved articles for each query (items: query_id, query_text, optional intent). method: semantic | keyword. Unapproved articles are never returned."""
    def work():
        if method not in ("semantic", "keyword"):
            raise InputError("method must be 'semantic' or 'keyword'")
        kb = _kb()
        out = []
        for it in items:
            det: dict = {}
            art, score, cand = resolver.retrieve_article(str(it.get("query_text", "")), kb, method, intent=it.get("intent"), detail=det)
            out.append({"query_id": it.get("query_id"), "article_id": art["article_id"] if art else None, "title": art["title"] if art else None, "match_score": score,
                        "response": resolver.draft_response(art), "candidates": cand, "evidence": det})
        return {"results": out, "count": len(out)}
    return await run_blocking("search_knowledge", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def decide_routes(items: list[dict[str, Any]], guard: bool = True) -> dict[str, Any]:
    """Choose a route for each query from its classification, match score and risk rules (items: query_id, classification, match_score, article_found, evidence). guard: require the auto-answer evidence checks."""
    def work():
        policy = load_policy()
        out = []
        for it in items:
            c = it["classification"]
            r = wr.route_query(c, float(it.get("match_score") or 0), bool(it.get("article_found")), policy, (it.get("evidence") or {}) if guard else None)
            out.append({"query_id": it.get("query_id"), "route": r, "route_class": wr.route_class(r), "team": wr.assign_team(c, r)})
        return {"results": out, "count": len(out)}
    return await run_blocking("decide_routes", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def list_articles(include_unapproved: bool = False) -> dict[str, Any]:
    """List knowledge-base articles (approved only by default) with owner, version and last review date."""
    def work():
        kb = _kb()
        arts = [{k: a.get(k) for k in ("article_id", "title", "intent", "keywords", "approved", "version", "owner", "last_reviewed")} for a in kb if include_unapproved or a.get("approved")]
        return {"articles": arts, "count": len(arts), "total": len(kb)}
    return await run_blocking("list_articles", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def check_knowledge_base(path: str = "") -> dict[str, Any]:
    """Validate a knowledge-base file. A malformed file raises an input error and no response is ever generated from it."""
    def work():
        try:
            kb = resolver.load_knowledge_base(safe_input_path(path) if path else PATHS.kb_json)
        except resolver.KnowledgeBaseError as exc:
            raise InputError(str(exc)) from exc
        return {"ok": True, "articles": len(kb), "approved": len(resolver.approved(kb))}
    return await run_blocking("check_knowledge_base", work)


if __name__ == "__main__":
    mcp.run()
