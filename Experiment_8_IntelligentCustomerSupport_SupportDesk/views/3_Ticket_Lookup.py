"""Ticket lookup: complete history of one ticket."""
import pandas as pd
import streamlit as st

from ui.common import call, esc, fmt_min, html_table, label, need, page_header, page_setup, section, sidebar, status_badge, summary_strip

page_setup("Ticket lookup")
uid, user = sidebar()
page_header("Ticket lookup", "Search by ticket number, customer or words in the message.")
c1, c2 = st.columns([2, 1])
term = c1.text_input("Search", st.session_state.get("sel", ""), placeholder="TKT-..., C1001, password ...", label_visibility="collapsed")
status = c2.selectbox("Status", ["any", "OPEN", "IN_REVIEW", "AWAITING_CUSTOMER", "RESOLVED", "ESCALATED", "ON_HOLD", "REOPENED"], label_visibility="collapsed")
d = need(call("list_tickets", text=term, status=None if status == "any" else [status], limit=60))["tickets"]
if not d:
    st.info("No tickets match.")
    st.stop()
df = pd.DataFrame([{"Ticket": t["ticket_id"], "Priority": t["priority"], "Customer": t["customer_id"], "Message": (t["query_text"] or "")[:80], "Status": label(t["status"]), "SLA": label(t["sla_state"])} for t in d])
sel = st.dataframe(df, hide_index=True, width="stretch", height=min(300, 38 + 35 * len(df)), on_select="rerun", selection_mode="single-row")
tid = df.iloc[sel.selection.rows[0]]["Ticket"] if sel.selection.rows else d[0]["ticket_id"]
g = need(call("get_ticket", ticket_id=tid))
t = g["ticket"]
st.markdown(f"### {esc(tid)} &nbsp; {status_badge(t['priority'])} {status_badge(t['status'])} {status_badge(t['sla_state'])}", unsafe_allow_html=True)
summary_strip([("Customer", esc(t["customer_id"])), ("Type", esc(t["intent"])), ("Team", esc(t["assigned_team"])), ("Assignee", esc(t["assignee"] or "-")),
               ("First reply", f"{fmt_min(g['first_response']['used_min'])} / {fmt_min(g['first_response']['window_min'])} " + status_badge(g["first_response"]["state"])),
               ("Resolution", f"{fmt_min(g['resolution']['used_min'])} / {fmt_min(g['resolution']['window_min'])} " + status_badge(g["resolution"]["state"]))])
st.info(t["query_text"])
t1, t2, t3, t4 = st.tabs(["History", "Replies", "Messages sent", "Audit trail"])
with t1:
    hist = pd.DataFrame(g["history"]).drop(columns=["id", "ticket_id"], errors="ignore")
    if "at" in hist:
        hist["at"] = hist["at"].astype(str).str[:16].str.replace("T", " ")
    st.dataframe(hist.fillna(""), hide_index=True, width="stretch")
with t2:
    if g["versions"]:
        for v in g["versions"]:
            st.caption(f"Version {v['version']} by {v['edited_by']} at {v['at'][:16].replace('T', ' ')}")
            st.text(v["text"])
    else:
        st.caption("No reply drafted.")
with t3:
    if g["notifications"]:
        st.markdown(html_table(["Event", "To", "State", "Attempts", "Subject"], [[esc(label(n["event"])), esc(n["to_addr"]), status_badge(n["state"]), n["attempts"], esc(n["subject"])] for n in g["notifications"]], num_cols=(3,)), unsafe_allow_html=True)
    else:
        st.caption("Nothing sent.")
with t4:
    st.dataframe(pd.DataFrame(g["audit"]), hide_index=True, width="stretch")
