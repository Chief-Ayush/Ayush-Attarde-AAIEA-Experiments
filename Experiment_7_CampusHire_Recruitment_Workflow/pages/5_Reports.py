"""Reports: drive summary, rule version comparison and fairness review."""
import json

import altair as alt
import pandas as pd
import streamlit as st

from ui.common import SERIES, bar_chart, call, esc, grouped_bar, html_table, label, need, page_header, page_setup, section, sidebar, stat_tiles, status_badge

page_setup("Reports")
uid, user, did = sidebar()
page_header("Drive report", "Summary of the drive, checks on the rules and a fairness review.")
rep = need(call("drive_report", drive_id=did))

n = rep["notifications"]
dsr = rep["delivery_success_rate"]
stat_tiles([("Candidates", rep["candidates"], f"{rep['quarantined']} quarantined ({rep['duplicates']} duplicates)"), ("Review completion", f"{rep['review_completion']:.0%}", f"{rep['reviewed']} of {rep['scored']}"),
            ("Overrides", len(rep["overrides"])), ("Delivery success", "-" if dsr is None else f"{dsr:.0%}", f"{n.get('SENT', 0)} sent · {n.get('FAILED', 0)} failed"),
            ("SLA reminders", rep["sla_reminders"]), ("Mean score", rep["score_summary"].get("mean", "-"))])

t1, t2, t3, t4 = st.tabs(["Summary", "Overrides", "Rule versions", "Fairness"])
with t1:
    c1, c2 = st.columns(2)
    with c1:
        rows = [{"status": label(s), "stage": st_, "candidates": rep[k].get(s, 0)} for s in ["SHORTLIST_REVIEW", "MANUAL_REVIEW", "NOT_SHORTLISTED_REVIEW", "ON_HOLD", "AWAITING_INFORMATION"]
                for st_, k in (("Proposed", "proposed"), ("Final", "final")) if rep[k].get(s, 0) or k == "proposed" and s in ("SHORTLIST_REVIEW", "MANUAL_REVIEW", "NOT_SHORTLISTED_REVIEW")]
        st.altair_chart(grouped_bar(pd.DataFrame(rows), "status", "stage", "candidates", y_title="candidates", fmt="d", title="Status distribution"), width="stretch")
    with c2:
        q = need(call("get_queue", drive_id=did))["queue"]
        sc = pd.DataFrame({"score": [x["score"] for x in q], "role": [x["role"] for x in q]})
        st.altair_chart(alt.Chart(sc).mark_bar(cornerRadiusEnd=2).encode(x=alt.X("score:Q", bin=alt.Bin(step=10), title="score"), y=alt.Y("count()", title="candidates"),
                                                                       color=alt.Color("role:N", scale=alt.Scale(range=SERIES[:2]), legend=alt.Legend(title=None)),
                                                                       tooltip=["role:N", "count()"]).properties(title="Score distribution", height=250), width="stretch")
    st.markdown(html_table(["Quarantine reason", "Records"], [[esc(k.replace("_", " ").capitalize()), v] for k, v in rep["quarantine_reasons"].items()] or [["None", 0]], num_cols=(1,)), unsafe_allow_html=True)
    if rep["flagged_text"]:
        st.warning("Applications with instruction-like text (ignored by scoring, flagged for reviewers): " + ", ".join(rep["flagged_text"]))
    st.download_button("Download report (JSON)", json.dumps(rep, indent=1, default=str), file_name=f"{did}_report.json")
    if q:
        st.download_button("Download results (CSV)", pd.DataFrame([{k: x[k] for k in ("candidate_id", "candidate_name", "role", "score", "rule_version", "proposed_status", "final_status")} for x in q]).to_csv(index=False),
                           file_name=f"{did}_results.csv")
with t2:
    if rep["overrides"]:
        st.markdown(html_table(["Candidate", "Reviewer", "Role", "New status", "Reason"], [[f"<b>{esc(o['candidate_id'])}</b>", esc(o["reviewer"]), esc(o["role"].replace("_", " ")),
                                                                                         status_badge(o["to"]), esc(o["reason"])] for o in rep["overrides"]]), unsafe_allow_html=True)
    else:
        st.caption("No overrides in this drive.")
    st.markdown(html_table(["Action", "Count"], [[esc(k.replace("_", " ").capitalize()), v] for k, v in rep["actions"].items()] or [["No decisions yet", 0]], num_cols=(1,)), unsafe_allow_html=True)
with t3:
    versions = need(call("get_rules"))["versions"]
    c1, c2, c3 = st.columns([1, 1, 3])
    a = c1.selectbox("Compare", versions, index=0)
    b = c2.selectbox("with", versions, index=len(versions) - 1)
    if a != b and rep["candidates"]:
        cmp = need(call("compare_rule_versions", drive_id=did, version_a=a, version_b=b))
        stat_tiles([("Candidates", cmp["candidates"]), ("Unchanged", cmp["unchanged"]), ("Change status", cmp["changed"]), ("Stability", f"{cmp['stability']:.0%}")])
        dist = pd.DataFrame([{"rules": f"Version {v}", "status": label(s), "candidates": c} for v, d in cmp["distribution"].items() for s, c in d.items()])
        st.altair_chart(grouped_bar(dist, "status", "rules", "candidates", y_title="candidates", fmt="d"), width="stretch")
        if cmp["moves"]:
            st.dataframe(pd.DataFrame(cmp["moves"]).rename(columns={"candidate_id": "ID"}), hide_index=True, width="stretch")
with t4:
    fr = need(call("fairness_review", drive_id=did))
    if not fr["scored"]:
        st.caption("Nothing scored yet.")
    else:
        for f in fr["flags"] or []:
            st.warning(f)
        if not fr["flags"]:
            st.success("No group falls below 80% of the highest group's shortlist-review rate.")
        for dim, rows in fr["groups"].items():
            st.markdown(f"**By {dim}**")
            df = pd.DataFrame(rows)
            st.altair_chart(bar_chart(df, "group", "rate", fmt=".0%", domain=(0, 1), x_title=None, y_title="shortlist-review rate"), width="stretch")
            st.dataframe(df.rename(columns={"group": dim.title(), "n": "Candidates", "shortlist_review": "Shortlist review", "rate": "Rate", "ratio_to_highest": "Ratio to highest"}),
                         hide_index=True, width="stretch", column_config={"Rate": st.column_config.NumberColumn(format="percent")})
        st.markdown("**Criteria to watch**")
        for note in fr["criteria_notes"]:
            st.markdown(f"- {note}")
