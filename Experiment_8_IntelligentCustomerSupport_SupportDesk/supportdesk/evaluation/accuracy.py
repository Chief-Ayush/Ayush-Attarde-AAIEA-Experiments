"""Accuracy suite: the 147-query gold set (plus 9 invalid records) goes through the whole system; every lab-manual metric is computed from the database and logs."""
from __future__ import annotations

import csv
import json
import time

import pandas as pd

import workflow_router as wr
from classifier import INTENTS

from .. import store
from ..config import PATHS, Settings
from ..workflow import auto_agent_policy
from .env import ADMIN, Env
from .gold import GOLD
from .metrics import confusion, pct, percentile, prf

# invalid records appended to the file: (offset, record, expected rejection code)
INVALID = [
    ({"query_id": "X001", "customer_id": "C9801", "query_text": "", "created_at": "2026-10-22T09:00:00"}, "EMPTY_QUERY"),
    ({"query_id": "X002", "customer_id": "C9802", "query_text": "    ", "created_at": "2026-10-22T09:00:00"}, "EMPTY_QUERY"),
    ({"query_id": "G001", "customer_id": "C9803", "query_text": "Duplicate id of the first gold query", "created_at": "2026-10-22T09:00:00"}, "DUPLICATE_ID"),
    ({"query_id": "G010", "customer_id": "C9804", "query_text": "Another duplicate id", "created_at": "2026-10-22T09:00:00"}, "DUPLICATE_ID"),
    ({"query_id": "X005", "customer_id": "C9805", "query_text": "Where is my invoice?", "created_at": "last tuesday"}, "BAD_TIMESTAMP"),
    ({"query_id": "X006", "customer_id": "jane.doe@example.com", "query_text": "Where is my invoice?", "created_at": "2026-10-22T09:00:00"}, "BAD_CUSTOMER_ID"),
    ({"query_id": "X007", "customer_id": "", "query_text": "Cannot log in", "created_at": "2026-10-22T09:00:00"}, "MISSING_FIELD"),
    ({"query_id": "X008", "customer_id": "C9808", "query_text": "a" * 2500, "created_at": "2026-10-22T09:00:00"}, "TEXT_TOO_LONG"),
    ({"query_id": "bad id!", "customer_id": "C9809", "query_text": "Where is my invoice?", "created_at": "2026-10-22T09:00:00"}, "BAD_ID"),
]


def customer_for(i: int) -> str:
    """Most customers have a normal mailbox; a few simulate flaky or bouncing mailboxes so retry and dead-letter paths are exercised."""
    if i % 23 == 5:
        return f"FLAKY{1 + i % 3}"
    if i % 41 == 7:
        return f"BOUNCE{i}"
    return f"C{9000 + i}"


def gold_records(prefix: str = "") -> list[dict]:
    return [{"query_id": f"{prefix}G{i:03d}", "customer_id": customer_for(i), "query_text": g[0], "created_at": f"2026-10-22T{9 + i // 60:02d}:{i % 60:02d}:00"} for i, g in enumerate(GOLD, start=1)]


def write_gold_files() -> None:
    PATHS.eval_data.mkdir(parents=True, exist_ok=True)
    recs = gold_records()
    with open(PATHS.eval_data / "gold_queries.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["query_id", "customer_id", "query_text", "created_at", "gold_intent", "gold_complexity", "gold_priority", "gold_article", "gold_route"])
        for r, g in zip(recs, GOLD):
            w.writerow([r["query_id"], r["customer_id"], r["query_text"], r["created_at"], g[1], g[2], g[3], g[4] or "", g[5]])


def simulate_customers(env: Env, bid: str, truth: dict) -> dict:
    """Customer replies to answers we sent: 'helped' exactly when the article we sent is the one the gold label says answers the query."""
    helped = not_helped = agent_ok = agent_not = 0
    for t in env.tickets(bid):
        if t["status"] != "AWAITING_CUSTOMER":
            continue
        notes = store.list_notifications(t["ticket_id"])
        if not any(n["event"] == "RESPONSE" and n["state"] == "SENT" for n in notes):
            continue
        ok = truth.get(t["query_id"]) == t["article_id"] and t["article_id"] is not None
        r = env.call("customer_reply", {"ticket_id": t["ticket_id"], "helped": bool(ok), "actor": ADMIN, "note": "" if ok else "the answer does not match my question"})
        if t["route"] == "PROPOSE_AUTO_RESOLUTION":
            helped += bool(ok) and r.ok
            not_helped += (not ok) and r.ok
        else:
            agent_ok += bool(ok) and r.ok
            agent_not += (not ok) and r.ok
    return {"helped": helped, "not_helped": not_helped, "agent_helped": agent_ok, "agent_not_helped": agent_not}


def audit_complete(ticket: dict, events: list[str], notes: list[dict]) -> bool:
    """A ticket's audit trail is complete when its creation is logged with the classification, every notification has a draft and an outcome, and every agent action is logged."""
    ev = set(events)
    if "ticket_created" not in ev:
        return False
    for n in notes:
        if "notification_drafted" not in ev:
            return False
        if n["state"] == "SENT" and "notification_sent" not in ev:
            return False
        if n["state"] == "DEAD_LETTER" and "notification_dead_letter" not in ev:
            return False
    if ticket["review_action"] and "review_action" not in ev:
        return False
    return True


def run_accuracy(env: Env, settings: Settings | None = None, prefix: str | None = None, tag: str = "gold") -> tuple[pd.DataFrame, dict, dict]:
    s = settings or env.s
    prefix = prefix if prefix is not None else env.tag() + "-"
    recs = gold_records(prefix)
    inv = [{**r, "query_id": (prefix + r["query_id"]) if r["query_id"].startswith("G") else r["query_id"]} for r, _ in INVALID]
    # the duplicate IDs refer to the prefixed originals
    batch_recs = recs + inv
    bid = f"EVAL-{tag}-{env.tag()}"
    from ..workflow import run_batch
    t0 = time.perf_counter()
    res = run_batch(env.write(batch_recs), ADMIN, batch_id=bid, settings=s, hub=env.hub)
    total_ms = res["total_ms"]
    truth = {r["query_id"]: g[4] for r, g in zip(recs, GOLD)}
    cust = simulate_customers(env, bid, truth)
    tickets = {t["query_id"]: t for t in env.tickets(bid)}
    st = res["state"]
    rows = []
    for r, g in zip(recs, GOLD):
        t = tickets.get(r["query_id"])
        rc = wr.route_class(t["route"]) if t else None
        rows.append({"query_id": r["query_id"], "text": r["query_text"], "gold_intent": g[1], "intent": t["intent"] if t else None, "gold_complexity": g[2], "complexity": t["complexity"] if t else None,
                     "gold_priority": g[3], "priority": t["base_priority"] if t else None, "gold_article": g[4], "article": t["article_id"] if t else None, "match_score": t["match_score"] if t else None,
                     "gold_route": g[5], "route_class": rc, "route": t["route"] if t else None, "status": t["status"] if t else None, "ticket_id": t["ticket_id"] if t else None})
    df = pd.DataFrame(rows)
    df["intent_ok"] = df.intent == df.gold_intent
    df["complexity_ok"] = df.complexity == df.gold_complexity
    df["priority_ok"] = df.priority == df.gold_priority
    df["route_ok"] = df.route_class == df.gold_route
    df["article_ok"] = df.apply(lambda r: (r.article == r.gold_article) if r.gold_article else True, axis=1)
    df["abstain_ok"] = df.apply(lambda r: (not r.article or (r.match_score or 0) < 40 or r.route_class in ("REV", "URG", "INFO")) if not r.gold_article else True, axis=1)

    out = {"batch_id": bid, "prefix": prefix, "n_gold": len(df), "invalid_records": len(inv)}
    out["intent_accuracy_pct"] = pct(int(df.intent_ok.sum()), len(df))
    out["complexity_accuracy_pct"] = pct(int(df.complexity_ok.sum()), len(df))
    out["priority_accuracy_pct"] = pct(int(df.priority_ok.sum()), len(df))
    out["route_accuracy_pct"] = pct(int(df.route_ok.sum()), len(df))
    f = prf(df.gold_intent.tolist(), df.intent.fillna("-").tolist(), INTENTS)
    out["intent_macro_f1_pct"], out["intent_classes"] = f["macro_f1"], f["classes"]
    out["intent_confusion"] = confusion(df.gold_intent.tolist(), df.intent.fillna("-").tolist(), INTENTS)
    out["priority_macro_f1_pct"] = prf(df.gold_priority.tolist(), df.priority.fillna("-").tolist(), ["P1", "P2", "P3", "P4"])["macro_f1"]
    ans = df[df.gold_article.notna() & (df.gold_article != "")]
    out["retrieval_top1_pct"] = pct(int(ans.article_ok.sum()), len(ans))
    un = df[df.gold_article.isna() | (df.gold_article == "")]
    out["safe_abstention_pct"] = pct(int(un.abstain_ok.sum()), len(un))
    caution = {"AUTO": 0, "FEED": 1, "INFO": 1, "REV": 2, "URG": 3}
    df["route_safe"] = df.apply(lambda r: r.route_ok or (r.route_class in ("REV", "URG") and caution.get(r.route_class, 0) >= caution[r.gold_route] and r.gold_route != "INFO"), axis=1)
    out["safe_route_pct"] = pct(int(df.route_safe.sum()), len(df))
    gold_auto = df[df.gold_route == "AUTO"]
    out["automation_rate_pct"] = pct(int((gold_auto.route_class == "AUTO").sum()), len(gold_auto))
    out["unsafe_automation"] = int(((df.route_class == "AUTO") & (df.gold_route != "AUTO")).sum())
    p1 = df[df.gold_priority == "P1"]
    out["p1_recall_pct"] = pct(int((p1.route_class == "URG").sum()), len(p1))
    out["urgent_precision_pct"] = pct(int(((df.route_class == "URG") & (df.gold_priority == "P1")).sum()), int((df.route_class == "URG").sum()))
    # ---- lab-manual metrics
    valid_n = len(df)
    esc = df[df.route_class.isin(["URG", "REV"])]
    out["escalation_rate_pct"] = pct(len(esc), valid_n)
    out["gold_escalation_rate_pct"] = pct(int(df.gold_route.isin(["URG", "REV"]).sum()), valid_n)
    out["escalation_decision_accuracy_pct"] = pct(int((df.route_class.isin(["URG", "REV"]) == df.gold_route.isin(["URG", "REV"])).sum()), valid_n)
    elig = df[df.gold_route == "AUTO"]
    out["eligible_queries"] = len(elig)
    out["resolution_rate_pct"] = pct(int((elig.status == "RESOLVED").sum()), len(elig))
    out["auto_resolved_pct"] = pct(int(((elig.status == "RESOLVED") & (elig.route_class == "AUTO")).sum()), len(elig))
    sent_auto = df[(df.route_class == "AUTO") & df.status.isin(["AWAITING_CUSTOMER", "RESOLVED", "IN_REVIEW", "REOPENED"])]
    wrong = sent_auto[sent_auto.article != sent_auto.gold_article]
    out["auto_answers_sent"] = len(sent_auto)
    out["false_auto_resolution_pct"] = pct(len(wrong), len(sent_auto))
    out["auto_answer_precision_pct"] = pct(len(sent_auto) - len(wrong), len(sent_auto))
    out["customer_confirmed"], out["customer_reopened"] = cust["helped"], cust["not_helped"]
    out["agent_answers_helped"], out["agent_answers_not_helped"] = cust["agent_helped"], cust["agent_not_helped"]
    out["reopened_rate_pct"] = pct(cust["not_helped"], cust["helped"] + cust["not_helped"])
    # validation
    rej = st.get("rejected") or []
    exp_codes = [c for _, c in INVALID]
    got_codes = [x["code"] for x in rej]
    exp_total = len(recs) + len(INVALID)
    correct_valid = sum(1 for r in recs if r["query_id"] in tickets)
    correct_invalid = sum(1 for (r, c) in zip(inv, exp_codes) if any(x["code"] == c for x in rej))
    out["validation_accuracy_pct"] = pct(correct_valid + min(correct_invalid, len(rej)), exp_total)
    out["rejection_code_accuracy_pct"] = pct(sum(min(exp_codes.count(c), got_codes.count(c)) for c in set(exp_codes)), len(exp_codes))
    out["ticket_creation_rate_pct"] = pct(len(tickets), st["counts"]["valid"])
    # notifications
    nots = []
    for t in tickets.values():
        nots += store.list_notifications(t["ticket_id"])
    authorised = [n for n in nots if not (n["needs_approval"] and not n["approved_by"])]
    delivered = [n for n in authorised if n["state"] == "SENT"]
    attempts = {n["notification_id"]: store.notification_attempts(n["notification_id"]) for n in nots if int(n["attempts"] or 0)}
    transient = [a for v in attempts.values() for a in v if a["result"] == "TRANSIENT_FAILURE"]
    recovered = [n for n in delivered if int(n["attempts"] or 0) > 1]
    failing_notes = [n for n in authorised if any(a["result"] == "TRANSIENT_FAILURE" for a in attempts.get(n["notification_id"], []))]
    out.update(notifications_total=len(nots), notifications_authorised=len(authorised), notifications_delivered=len(delivered), notifications_dead_letter=sum(n["state"] == "DEAD_LETTER" for n in nots),
               notification_success_pct=pct(len(delivered), len(authorised)), notifications_retried=len([n for n in nots if int(n["attempts"] or 0) > 1]), retry_recovered=len(recovered),
               retry_recovery_rate_pct=pct(len(recovered), len(failing_notes)), transient_failures=len(transient), never_authorised=len(nots) - len(authorised))
    # audit completeness
    ok = 0
    for t in tickets.values():
        ev = [a["event_type"] for a in store.query_audit(ticket_id=t["ticket_id"], limit=500)]
        full = store.get_ticket(t["ticket_id"])
        ok += audit_complete(full, ev, store.list_notifications(t["ticket_id"]))
    out["audit_completeness_pct"] = pct(ok, len(tickets))
    out["audit_chain_ok"] = store.verify_audit_chain()["ok"]
    # human review completion: tickets that needed an agent and whose agent action was recorded and delivered (no dead-lettered reply)
    need = df[df.route_class.isin(["URG", "REV", "FEED"])]
    done = 0
    for tid in need.ticket_id.dropna():
        full = store.get_ticket(tid)
        n_dead = any(n["state"] == "DEAD_LETTER" and n["event"] in ("RESPONSE", "INFO_REQUESTED", "RESOLVED") for n in store.list_notifications(tid))
        done += bool(full["last_reviewer"]) and not n_dead and full["review_action"] not in ("HOLD",)
    out["human_review_needed"] = len(need)
    out["human_review_completion_pct"] = pct(done, len(need))
    out["still_open_after_review"] = len(need) - done
    # workflow success: ticket created, notification delivered or intentionally withheld
    okw = 0
    for t in tickets.values():
        ns = [n for n in store.list_notifications(t["ticket_id"]) if not (n["needs_approval"] and not n["approved_by"])]
        okw += all(n["state"] == "SENT" for n in ns)
    out["workflow_success_pct"] = pct(okw, len(tickets))
    out["avg_automated_ms_per_query"] = round(total_ms / valid_n, 1)
    out["total_ms"] = total_ms
    out["tool_calls"] = res["tool_calls"]
    out["status_counts"] = df.status.value_counts().to_dict()
    out["route_counts"] = df.route_class.value_counts().to_dict()
    return df, out, res
