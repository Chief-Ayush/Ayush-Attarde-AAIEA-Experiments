"""Knowledge retrieval and response scoring (lab step 7 and variation 3).

  keyword  - the manual's formula: 100 x matched keywords / keywords considered, over approved articles only;
  semantic - TF-IDF cosine similarity between the query and each approved article (title, keywords, sample questions),
             mapped to a 0-100 match score with a scale fitted on the training queries (never on the gold set).
Unapproved articles are never indexed, so they can never be returned.  The score is a heuristic match score, not a probability.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KB_FILE = ROOT / "knowledge_base.json"
CALIBRATION_FILE = ROOT / "storage" / "retrieval_calibration.json"
MIN_COSINE = 0.12          # below this a query has no usable overlap with any article


class KnowledgeBaseError(Exception):
    """Raised for a missing or malformed knowledge base; nothing is generated from a bad file (TC19)."""


def load_knowledge_base(path: str | Path = KB_FILE) -> list[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise KnowledgeBaseError(f"knowledge base not found: {Path(path).name}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise KnowledgeBaseError(f"knowledge base is not valid JSON ({exc.__class__.__name__})") from exc
    if not isinstance(data, list):
        raise KnowledgeBaseError("knowledge base must be a JSON list of articles")
    problems = []
    for i, a in enumerate(data):
        if not isinstance(a, dict) or not all(k in a for k in ("article_id", "intent", "keywords", "response", "approved")):
            problems.append(i)
        elif not isinstance(a["keywords"], list) or not isinstance(a["response"], str):
            problems.append(i)
    if problems:
        raise KnowledgeBaseError(f"{len(problems)} malformed article(s) at position(s) {problems[:5]}")
    return data


def approved(kb: list[dict]) -> list[dict]:
    return [a for a in kb if a.get("approved") is True]


def _doc(a: dict) -> str:
    return " ".join([a.get("title", "")] + a["keywords"] * 2 + a.get("sample_questions", []))


def keyword_scores(text: str, kb: list[dict]) -> list[tuple[float, dict]]:
    low = text.lower()
    out = []
    for a in approved(kb):
        kws = a.get("keywords", [])
        matched = [k for k in kws if k.lower() in low]
        if matched:
            out.append((len(matched) / len(kws) * 100, a))
    return sorted(out, key=lambda x: -x[0])


_IDX: dict = {}


def _index(kb: list[dict]):
    key = tuple((a["article_id"], a.get("version")) for a in approved(kb))
    if _IDX.get("key") != key:
        from sklearn.feature_extraction.text import TfidfVectorizer
        docs = approved(kb)
        vec = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, stop_words="english", token_pattern=r"(?u)\b[\w'-]{2,}\b")
        m = vec.fit_transform([_doc(a) for a in docs])
        _IDX.update(key=key, vec=vec, mat=m, docs=docs)
    return _IDX["vec"], _IDX["mat"], _IDX["docs"]


def cosine_ranking(text: str, kb: list[dict]) -> list[tuple[float, dict]]:
    vec, mat, docs = _index(kb)
    q = vec.transform([text])
    sims = (mat @ q.T).toarray().ravel()
    return sorted(zip(sims.tolist(), docs), key=lambda x: -x[0])


def cosine_full() -> float:
    try:
        return float(json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))["cosine_full"])
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return 0.5


def fit_scale(kb: list[dict], rows: list[dict], quantile: float = 0.35) -> float:
    """Pick the cosine that maps to a score of 100 so that 70 sits at the given quantile of correct-article cosines (training queries only)."""
    import numpy as np
    sims = []
    for r in rows:
        if r.get("article"):
            for s, a in cosine_ranking(r["text"], kb):
                if a["article_id"] == r["article"]:
                    sims.append(s)
    full = float(np.quantile(sims, quantile)) / 0.70
    CALIBRATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    CALIBRATION_FILE.write_text(json.dumps({"cosine_full": round(full, 4), "quantile": quantile, "n": len(sims)}), encoding="utf-8")
    return full


INTENT_BONUS = 0.10        # chosen on the training queries: an article whose intent matches the predicted intent ranks slightly higher


def retrieve_article(query_text: str, knowledge_base: list[dict], method: str = "semantic", top_k: int = 3, intent: str | None = None, detail: dict | None = None) -> tuple[dict | None, float, list[dict]]:
    """Return (best article or None, score 0-100, ranked candidates).  No approved match -> (None, 0, [])."""
    if method == "keyword":
        ranked = [(s, a) for s, a in keyword_scores(query_text, knowledge_base)]
    else:
        full = cosine_full()
        raw = [(s, a) for s, a in cosine_ranking(query_text, knowledge_base) if s >= MIN_COSINE]
        raw = sorted([(s + (INTENT_BONUS if intent and a["intent"] == intent else 0.0), a) for s, a in raw], key=lambda x: -x[0])
        ranked = [(min(100.0, 100.0 * s / full), a) for s, a in raw]
    cand = [{"article_id": a["article_id"], "title": a["title"], "score": round(s, 1)} for s, a in ranked[:top_k]]
    if not ranked:
        return None, 0.0, []
    if detail is not None:                                   # evidence used by the auto-answer guard
        top = ranked[0][1]
        detail.update(intent_match=bool(intent and top["intent"] == intent), keyword_hits=keyword_evidence(query_text, top),
                      margin=round(((raw[0][0] - raw[1][0]) if method != "keyword" and len(raw) > 1 else 1.0), 4) if method != "keyword" else 1.0)
    return ranked[0][1], round(ranked[0][0], 2), cand


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _stems(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z0-9']+", text.lower())}


def keyword_evidence(text: str, article: dict) -> int:
    """How many of the article's keyword phrases occur in the query (word-stem match, so 'locked' supports 'account locked')."""
    q = _stems(text)
    return sum(1 for k in article.get("keywords", []) if {_stem(w) for w in re.findall(r"[a-z0-9']+", k.lower())} <= q)


def draft_response(article: dict | None, customer_ref: str = "") -> str:
    """Only approved article text is used; with no article there is no invented answer."""
    if not article or not article.get("approved"):
        return ""
    return article["response"]
