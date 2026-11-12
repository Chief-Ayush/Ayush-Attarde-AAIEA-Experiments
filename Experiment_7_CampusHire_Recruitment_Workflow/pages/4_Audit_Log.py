"""Audit log: append-only record of every action, with tamper check."""
import pandas as pd
import streamlit as st

from ui.common import badge, call, esc, html_table, need, page_header, page_setup, section, sidebar, stat_tiles

page_setup("Audit log")
from campushire.config import PATHS  # noqa: E402

uid, user, did = sidebar()
page_header("Audit log", "Every action is recorded once and cannot be changed or deleted.")
chain = need(call("verify_audit"))
c = st.columns([1, 1, 1, 2])
all_rows = need(call("query_audit", drive_id=did, limit=2000))["records"]
stat_tiles([("Records in this drive", len(all_rows)), ("Records in total", chain["rows"]),
            ("Integrity check", "Intact" if chain["ok"] else f"Changed at row {chain['first_bad_id']}"), ("Denied attempts", sum(1 for r in all_rows if r["event"] == "access_denied"))])

c1, c2, c3 = st.columns([1.2, 1.4, 1.2])
cand = c1.text_input("Candidate ID", placeholder="All candidates").strip().upper()
events = sorted({r["event"] for r in all_rows})
ev = c2.multiselect("Event", events, format_func=lambda e: e.replace("_", " "), placeholder="All events")
limit = c3.selectbox("Show", [100, 300, 1000], index=0)
rows = [r for r in all_rows if (not cand or r["candidate_id"] == cand) and (not ev or r["event"] in ev)][:limit]
df = pd.DataFrame(rows).fillna("")
if len(df):
    show = df[["id", "timestamp", "candidate_id", "event", "validation_status", "score", "rule_version", "proposed_status", "reviewer_action", "final_status",
               "notification_status", "error_details", "actor", "trace_id"]].rename(columns={"id": "#", "candidate_id": "Candidate", "validation_status": "Validation",
                                                                                              "rule_version": "Rules", "proposed_status": "Proposed", "reviewer_action": "Action",
                                                                                              "final_status": "Final", "notification_status": "Message", "error_details": "Detail"})
    st.dataframe(show, hide_index=True, width="stretch", height=430)
else:
    st.caption("No matching records.")

d1, d2, _ = st.columns([1, 1, 3])
if d1.button("Update audit_log.csv", disabled="view" not in user["perms"]):
    r = call("export_audit", actor=uid)
    st.toast(f"{r.data['appended']} new line(s) appended" if r.ok else r.error)
if PATHS.audit_csv.exists():
    d2.download_button("Download audit_log.csv", PATHS.audit_csv.read_bytes(), file_name="audit_log.csv")

if cand:
    section(f"Candidate {cand}")
    t = need(call("candidate_trail", drive_id=did, candidate_id=cand))
    st.markdown(html_table(["Time", "Event", "Detail"], [[esc(a["timestamp"][5:19].replace("T", " ")), f"<b>{esc(a['event'].replace('_', ' '))}</b>",
                                                          esc(" · ".join(x for x in [a["proposed_status"], a["reviewer_action"], a["final_status"], a["notification_status"], a["error_details"]] if x))]
                                                         for a in t["audit"]]), unsafe_allow_html=True)
