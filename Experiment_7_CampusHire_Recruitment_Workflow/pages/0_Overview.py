"""Overview of the selected hiring drive."""
import pandas as pd
import streamlit as st

from ui.common import (STATUS_LABEL, bar_chart, call, esc, grouped_bar, html_table, label, need, page_header, page_setup, section, sidebar, stat_tiles,
                       status_badge, status_bar)

page_setup("Overview")
uid, user, did = sidebar()

page_header("Campus hiring drive", "Automated screening with a person at every decision.")
rep = need(call("drive_report", drive_id=did))
d = rep["drive"]
q = need(call("get_queue", drive_id=did))["queue"]
n_notes = rep["notifications"]
waiting = rep["scored"] - rep["reviewed"]
stat_tiles([("Candidates", rep["candidates"], f"{rep['quarantined']} quarantined"), ("Awaiting decision", waiting), ("Decided", rep["reviewed"], f"{rep['review_completion']:.0%} complete"),
            ("Messages sent", n_notes.get("SENT", 0), f"{n_notes.get('FAILED', 0)} failed · {n_notes.get('NOT_SENT', 0)} held back"),
            ("Rule version", d["rule_version"])])

c1, c2 = st.columns([1.3, 1])
with c1:
    section("Proposed and final status")
    rows = []
    for s in ["SHORTLIST_REVIEW", "MANUAL_REVIEW", "NOT_SHORTLISTED_REVIEW"]:
        rows.append({"status": label(s), "stage": "Proposed", "candidates": rep["proposed"].get(s, 0)})
        rows.append({"status": label(s), "stage": "Final (after review)", "candidates": rep["final"].get(s, 0)})
    st.altair_chart(grouped_bar(pd.DataFrame(rows), "status", "stage", "candidates", y_title="candidates", fmt="d"), width="stretch")
with c2:
    section("Messages")
    names = {"SENT": "Sent", "FAILED": "Failed", "NOT_SENT": "Held back", "DRAFT": "Draft", "APPROVED": "Approved"}
    st.altair_chart(status_bar({names.get(k, k): v for k, v in n_notes.items()} or {"None yet": 0}, cmap={"Sent": "#0ca30c", "Failed": "#d03b3b", "Held back": "#8a909a", "Draft": "#2a78d6", "Approved": "#1baf7a"}), width="stretch")

section("Next steps")
c1, c2, c3, c4 = st.columns(4)
c1.page_link("pages/1_Run_Workflow.py", label="Run workflow")
c2.page_link("pages/2_Review_Queue.py", label=f"Review queue ({waiting} waiting)")
c3.page_link("pages/3_Quarantine.py", label=f"Quarantine ({rep['quarantined']})")
c4.page_link("pages/5_Reports.py", label="Drive report")

section("Recent activity")
a = need(call("query_audit", drive_id=did, limit=8))["records"]
st.markdown(html_table(["Time", "Event", "Candidate", "Detail", "By"], [[esc(r["timestamp"][5:19].replace("T", " ")), f"<b>{esc(r['event'].replace('_', ' '))}</b>", esc(r["candidate_id"] or "-"),
                                                                       esc((r["error_details"] or r["final_status"] or r["proposed_status"] or "")[:90]), esc(r["actor"] or "-")]
                                                                      for r in a]), unsafe_allow_html=True)
