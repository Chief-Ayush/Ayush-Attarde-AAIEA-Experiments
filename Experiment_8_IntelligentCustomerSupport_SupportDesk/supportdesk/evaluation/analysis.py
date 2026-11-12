"""Comparison experiments (lab variations): classifier engines, retrieval methods, workflow configurations (ablation), priority queues vs first-in-first-out
with a staffed-queue simulation, and sequential / batch / parallel performance."""
from __future__ import annotations

import random
import statistics
import time

import pandas as pd

import classifier
import resolver
import workflow_router as wr

from ..config import PATHS, Settings, load_policy
from .accuracy import run_accuracy
from .env import Env
from .gold import GOLD
from .metrics import confusion, pct, percentile, prf


# ---------------------------------------------------------------------------------------------------------------------- classifier and retrieval comparisons
def compare_classifiers() -> pd.DataFrame:
    rows = []
    texts = [g[0] for g in GOLD]
    engines = {"Manual baseline rules": lambda t: classifier.classify_baseline(t), "Keyword rules (ordered, with critical-first)": lambda t: classifier.classify_query(t, "rules"),
               "TF-IDF + logistic regression": lambda t: classifier.classify_query(t, "learned")}
    for name, fn in engines.items():
        lat, preds = [], []
        for t in texts:
            t0 = time.perf_counter()
            r = fn(t)
            lat.append((time.perf_counter() - t0) * 1000)
            preds.append(r)
        yi = [g[1] for g in GOLD]
        pi = [p["intent"] for p in preds]
        f = prf(yi, pi, classifier.INTENTS)
        pr = [sum(f["classes"][c]["precision"] for c in f["classes"]) / len(f["classes"]), sum(f["classes"][c]["recall"] for c in f["classes"]) / len(f["classes"])]
        rows.append({"engine": name, "intent_accuracy_pct": pct(sum(a == b for a, b in zip(yi, pi)), len(yi)), "macro_precision_pct": round(pr[0], 1), "macro_recall_pct": round(pr[1], 1), "macro_f1_pct": f["macro_f1"],
                     "complexity_accuracy_pct": pct(sum(g[2] == p["complexity"] for g, p in zip(GOLD, preds)), len(GOLD)), "priority_accuracy_pct": pct(sum(g[3] == p["priority"] for g, p in zip(GOLD, preds)), len(GOLD)),
                     "p1_recall_pct": pct(sum(1 for g, p in zip(GOLD, preds) if g[3] == "P1" and p["priority"] == "P1"), sum(1 for g in GOLD if g[3] == "P1")),
                     "median_ms": round(percentile(lat, 50), 3), "p95_ms": round(percentile(lat, 95), 3)})
    return pd.DataFrame(rows)


def compare_retrieval() -> pd.DataFrame:
    kb = resolver.load_knowledge_base()
    rows = []
    ans = [g for g in GOLD if g[4]]
    una = [g for g in GOLD if not g[4]]
    for name, method, use_intent in (("Keyword match (manual formula)", "keyword", False), ("TF-IDF cosine", "semantic", False), ("TF-IDF cosine + intent-aware ranking", "semantic", True)):
        top1 = rr = 0
        lat = []
        for g in ans:
            intent = classifier.classify_query(g[0])["intent"] if use_intent else None
            t0 = time.perf_counter()
            a, s, cand = resolver.retrieve_article(g[0], kb, method, top_k=5, intent=intent)
            lat.append((time.perf_counter() - t0) * 1000)
            ids = [c["article_id"] for c in cand]
            top1 += bool(ids) and ids[0] == g[4]
            rr += 1 / (ids.index(g[4]) + 1) if g[4] in ids else 0
        found_wrong = sum(1 for g in una if resolver.retrieve_article(g[0], kb, method, intent=classifier.classify_query(g[0])["intent"] if use_intent else None)[0] is not None)
        rows.append({"method": name, "top1_pct": pct(top1, len(ans)), "mrr": round(rr / len(ans), 3), "no_answer_returned_pct": pct(len(una) - found_wrong, len(una)), "median_ms": round(percentile(lat, 50), 3)})
    return pd.DataFrame(rows)


def ablation(env: Env, progress=None) -> pd.DataFrame:
    """Same gold set through the whole workflow with different settings."""
    configs = [("Rules + keyword match, no guard (closest to the manual)", Settings(classifier="rules", retrieval="keyword", auto_guard=False)),
               ("Rules + semantic search, no guard", Settings(classifier="rules", retrieval="semantic", auto_guard=False)),
               ("Learned classifier + keyword match", Settings(classifier="learned", retrieval="keyword", auto_guard=True)),
               ("Learned + semantic search, no guard", Settings(classifier="learned", retrieval="semantic", auto_guard=False)),
               ("Learned + semantic + guard (system default)", Settings())]
    rows = []
    for i, (name, s) in enumerate(configs):
        if progress:
            progress(i / len(configs), name)
        _, o, _ = run_accuracy(env, settings=s, tag="abl")
        rows.append({"configuration": name, "intent_accuracy_pct": o["intent_accuracy_pct"], "priority_accuracy_pct": o["priority_accuracy_pct"], "safe_route_pct": o["safe_route_pct"],
                     "retrieval_top1_pct": o["retrieval_top1_pct"], "automation_rate_pct": o["automation_rate_pct"], "auto_answer_precision_pct": o["auto_answer_precision_pct"],
                     "false_auto_resolution_pct": o["false_auto_resolution_pct"], "resolution_rate_pct": o["resolution_rate_pct"], "p1_recall_pct": o["p1_recall_pct"], "total_ms": o["total_ms"]})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------------------------------------------------- staffed-queue simulation (first-response SLA)
STAFF = {"Access Support": 2, "Billing Support": 2, "Technical Support": 3, "Product Feedback": 1, "Tier 1 Support": 2, "Incident Response": 2}
SERVICE_MEDIAN = {"P1": 4.0, "P2": 9.0, "P3": 8.0, "P4": 5.0}          # minutes an agent needs to write the first response
SHIFT_MIN = 420
BUSY_WINDOW = 100     # a busy period: the same tickets arrive within this many minutes (a launch day or an outage morning)


def simulate_sla(tickets: list[dict], scheduling: str, seed: int, aging: bool = True, policy: dict | None = None, staff: dict | None = None) -> dict:
    """Discrete-event simulation of agents answering tickets. tickets: dicts with priority (base), team, route_class, complexity.
    Auto-answered tickets get an immediate response; the others wait for an agent of their team.  Arrivals are Poisson over a busy window."""
    policy = policy or load_policy()
    staff = staff or STAFF
    rnd = random.Random(seed)
    n = len(tickets)
    t, arrivals = 0.0, []
    for _ in range(n):
        t += rnd.expovariate(n / BUSY_WINDOW)
        arrivals.append(t)
    items = []
    for a, tk in zip(arrivals, rnd.sample(tickets, n)):
        mult = 1.6 if tk["complexity"] == "High" else 1.0
        items.append({**tk, "arrive": a, "service": rnd.lognormvariate(0, 0.5) * SERVICE_MEDIAN[tk["priority"]] * mult})
    done = {}
    by_team: dict[str, list] = {}
    for it in items:
        if it["route_class"] == "AUTO":
            done[id(it)] = 0.1
        else:
            by_team.setdefault(it["team"], []).append(it)
    for team, its in by_team.items():
        free = [0.0] * staff.get(team, 1)
        pending = sorted(its, key=lambda x: x["arrive"])
        waiting: list = []
        idx = 0
        while idx < len(pending) or waiting:
            k = min(range(len(free)), key=lambda j: free[j])
            now_ = free[k]
            if not waiting and idx < len(pending) and pending[idx]["arrive"] > now_:
                now_ = pending[idx]["arrive"]
            while idx < len(pending) and pending[idx]["arrive"] <= now_:
                waiting.append(pending[idx])
                idx += 1
            if not waiting:
                continue

            def key(x):
                if scheduling == "fifo":
                    return x["arrive"]
                rank = int(x["priority"][1])
                if aging:
                    win = policy["sla_minutes"][x["priority"]]
                    rank = max(2, rank - int((now_ - x["arrive"]) // win)) if rank > 2 else rank
                return (rank, x["arrive"])
            nxt = min(waiting, key=key)
            waiting.remove(nxt)
            start = max(now_, nxt["arrive"])
            end = start + nxt["service"]
            free[k] = end
            done[id(nxt)] = end - nxt["arrive"]
    resp = []
    for it in items:
        r = done[id(it)]
        resp.append({"priority": it["priority"], "team": it["team"], "route_class": it["route_class"], "response_min": r, "met": r <= policy["sla_minutes"][it["priority"]]})
    df = pd.DataFrame(resp)
    util = {tm: round(sum(i["service"] for i in its) / (staff.get(tm, 1) * BUSY_WINDOW) * 100, 1) for tm, its in by_team.items()}
    return {"df": df, "compliance_pct": pct(int(df.met.sum()), len(df)), "by_priority": {p: pct(int(g.met.sum()), len(g)) for p, g in df.groupby("priority")}, "mean_response_min": round(df.response_min.mean(), 1),
            "p95_response_min": round(percentile(df.response_min.tolist(), 95), 1), "utilisation_pct": util}


def sla_comparison(tickets: list[dict], seeds: int = 20) -> tuple[pd.DataFrame, dict]:
    rows, detail = [], {}
    for mode, aging in (("fifo", False), ("priority", False), ("priority", True)):
        comp, byp, mean_r, p95 = [], {p: [] for p in ("P1", "P2", "P3", "P4")}, [], []
        for sd in range(1, seeds + 1):
            r = simulate_sla(tickets, mode, sd, aging)
            comp.append(r["compliance_pct"])
            mean_r.append(r["mean_response_min"])
            p95.append(r["p95_response_min"])
            for p, v in r["by_priority"].items():
                byp[p].append(v)
            util = r["utilisation_pct"]
        name = "First-in-first-out" if mode == "fifo" else ("Priority queues + aging" if aging else "Priority queues")
        rows.append({"scheduling": name, "sla_compliance_pct": round(statistics.mean(comp), 1), "sd": round(statistics.pstdev(comp), 1),
                     **{f"{p}_pct": round(statistics.mean(v), 1) for p, v in byp.items() if v}, "mean_response_min": round(statistics.mean(mean_r), 1), "p95_response_min": round(statistics.mean(p95), 1)})
        detail[name] = util
    return pd.DataFrame(rows), detail


# ---------------------------------------------------------------------------------------------------------------------- performance
def performance(env: Env, runs: int = 3) -> tuple[pd.DataFrame, dict]:
    rows = []
    for mode in ("sequential", "batch", "parallel"):
        ts, outs = [], []
        for _ in range(runs):
            _, o, res = run_accuracy(env, settings=Settings(execution_mode=mode), tag="perf")
            ts.append(o["total_ms"])
            outs.append((o["intent_accuracy_pct"], o["route_accuracy_pct"], o["resolution_rate_pct"]))
        rows.append({"mode": mode, "median_total_ms": round(statistics.median(ts), 0), "min_ms": round(min(ts), 0), "max_ms": round(max(ts), 0), "ms_per_query": round(statistics.median(ts) / len(GOLD), 1),
                     "intent_accuracy_pct": outs[0][0], "route_accuracy_pct": outs[0][1], "identical_across_runs": len(set(outs)) == 1})
    df = pd.DataFrame(rows)
    seq = float(df[df["mode"] == "sequential"].median_total_ms.iloc[0])
    sm = {"batch_speedup": round(seq / float(df[df["mode"] == "batch"].median_total_ms.iloc[0]), 2), "parallel_speedup": round(seq / float(df[df["mode"] == "parallel"].median_total_ms.iloc[0]), 2),
          "outputs_identical_across_modes": len({(r.intent_accuracy_pct, r.route_accuracy_pct) for r in df.itertuples()}) == 1,
          "outputs_identical_across_runs": bool(df.identical_across_runs.all())}
    return df, sm
