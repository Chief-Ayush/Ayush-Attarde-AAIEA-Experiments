"""Notifications: what was sent, retries, and the dead-letter queue."""
import pandas as pd
import streamlit as st

from ui.common import call, esc, html_table, label, need, page_header, page_setup, section, sidebar, stat_tiles, status_badge

page_setup("Notifications")
uid, user = sidebar()
page_header("Notifications", "Messages go out through a simulated gateway. Temporary failures are retried with growing delays; permanent ones wait in the dead-letter queue.")
s = need(call("notification_stats"))
stat_tiles([("Messages", s["total"]), ("Delivered", s["delivered"]), ("Retried", s["retried"], "needed more than one try"), ("Recovered after retry", s["recovered"]), ("Dead letter", s["dead_letter"]),
            ("Waiting for approval", s["never_authorised"])])
state = st.selectbox("Show", ["all", "SENT", "DRAFT", "DEAD_LETTER"], label_visibility="collapsed")
rows = need(call("list_notifications", state="" if state == "all" else state, limit=200))["notifications"]
rows = [n for n in rows if not str(n["ticket_id"]).startswith("TKT-") or True]
df = pd.DataFrame([{"Message": n["notification_id"], "Ticket": n["ticket_id"], "Event": label(n["event"]), "To": n["to_addr"], "State": label(n["state"]), "Tries": n["attempts"], "Approved by": n["approved_by"] or "-"} for n in rows])
sel = st.dataframe(df, hide_index=True, width="stretch", height=min(380, 38 + 35 * max(1, len(df))), on_select="rerun", selection_mode="single-row")
if sel.selection.rows:
    n = rows[sel.selection.rows[0]]
    st.markdown(f"**{esc(n['subject'])}** &nbsp; {status_badge(n['state'])}", unsafe_allow_html=True)
    st.text(n["message"])
    if n.get("attempt_log"):
        st.dataframe(pd.DataFrame(n["attempt_log"]).drop(columns=["id"], errors="ignore"), hide_index=True, width="stretch")
    if n["state"] == "DEAD_LETTER":
        addr = st.text_input("Corrected address (optional)", key="fix_addr")
        if st.button("Try again", disabled="reassign" not in user["perms"]):
            r = call("requeue_dead_letter", notification_id=n["notification_id"], actor=uid, to_addr=addr)
            if r.ok:
                call("dispatch_pending", actor=uid, ticket_ids=[n["ticket_id"]])
                st.rerun()
            st.error(str(r.error))
        if "reassign" not in user["perms"]:
            st.caption("Only leads and admins can requeue.")
if st.button("Send everything that is approved", disabled="dispatch" not in user["perms"]):
    r = need(call("dispatch_pending", actor=uid))
    st.success(f"Sent {r['sent']}, dead-lettered {r['dead_letter']}, retried {r['retries']}, waiting for approval {r['blocked']}.")
