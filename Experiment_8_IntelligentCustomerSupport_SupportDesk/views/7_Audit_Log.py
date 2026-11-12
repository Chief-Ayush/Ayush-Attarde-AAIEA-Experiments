"""Audit log: append-only, hash-chained record of every action."""
import pandas as pd
import streamlit as st

from ui.common import badge, call, need, page_header, page_setup, section, sidebar, stat_tiles

page_setup("Audit log")
uid, user = sidebar()
page_header("Audit log", "Every classification, decision, message and change is recorded and cannot be edited or deleted.")
v = need(call("verify_audit"))
stat_tiles([("Records", v.get("records", v.get("count", "-"))), ("Chain check", "Intact" if v["ok"] else "BROKEN"), ("Tamper triggers", "Active")])
c1, c2, c3 = st.columns([1.2, 1.2, 1])
tid = c1.text_input("Ticket", placeholder="TKT-...", label_visibility="collapsed")
ev = c2.text_input("Event type", placeholder="review_approve, message_sent ...", label_visibility="collapsed")
lim = c3.selectbox("Rows", [100, 300, 1000], label_visibility="collapsed")
d = need(call("list_audit", ticket_id=tid.strip(), event_type=ev.strip(), limit=lim))
st.dataframe(pd.DataFrame(d["events"]), hide_index=True, width="stretch", height=480)
st.download_button("Download audit_log.csv", pd.DataFrame(d["events"]).to_csv(index=False), "audit_log.csv", "text/csv")
