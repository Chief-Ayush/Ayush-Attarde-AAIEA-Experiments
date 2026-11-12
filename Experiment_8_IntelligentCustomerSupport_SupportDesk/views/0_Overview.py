"""Overview: what is waiting, how the SLA clocks stand and where the help desk is slow."""
import altair as alt
import pandas as pd
import streamlit as st

from ui.common import SERIES, STATUS, bar_chart, call, esc, fmt_min, label, need, page_header, page_setup, section, sidebar, stat_tiles, status_badge, html_table

page_setup("Overview")
uid, user = sidebar()
page_header("TaskNest SupportDesk", "Customer questions are sorted, answered from approved articles when it is safe, and handed to a person when it is not.")
def load_sample(label_: str, key: str):
    if st.button(label_, key=key, disabled="run" not in user["perms"]):
        from supportdesk.demo import reload
        with st.spinner("Loading sample data, about 15 seconds ..."):
            try:
                reload()
            except Exception as e:
                st.error(f"Sample data could not be loaded: {e}")
                st.stop()
        st.rerun()


q = need(call("queue_summary"))
if not q["total"]:
    st.info("No tickets yet. Load the sample day to see every page working, or send a message on the New request page.")
    load_sample("Load sample data", "load_demo")
    st.page_link("views/2_New_Request.py", label="New request")
    st.stop()
bp = q["by_priority"]
stat_tiles([("Open tickets", q["active"], f"of {q['total']} in total"), ("Urgent (P1)", bp["P1"]["count"]), ("At risk", sum(v["at_risk"] for v in bp.values()), "75% of target used"),
            ("Breached", sum(v["breached"] for v in bp.values()), "target missed"), ("Resolved", q["by_status"].get("RESOLVED", 0)), ("Waiting for customer", q["by_status"].get("AWAITING_CUSTOMER", 0))])
c1, c2 = st.columns(2)
with c1:
    df = pd.DataFrame([{"priority": p, "state": s, "tickets": v[k]} for p, v in bp.items() for s, k in (("Breached", "breached"), ("At risk", "at_risk"))] +
                      [{"priority": p, "state": "On track", "tickets": v["count"] - v["breached"] - v["at_risk"]} for p, v in bp.items()])
    dom = ["On track", "At risk", "Breached"]
    st.altair_chart(alt.Chart(df).mark_bar(cornerRadiusEnd=3).encode(y=alt.Y("priority:N", title=None), x=alt.X("tickets:Q", title="open tickets", axis=alt.Axis(tickMinStep=1, format="d")),
                    color=alt.Color("state:N", scale=alt.Scale(domain=dom, range=[STATUS["good"], STATUS["warning"], STATUS["critical"]]), legend=alt.Legend(title=None)),
                    order=alt.Order("state:N", sort="descending"), tooltip=["priority", "state", "tickets"]).properties(title="Priority queues", height=alt.Step(30)), width="stretch")
with c2:
    teams = pd.DataFrame({"team": list(q["by_team"]), "open": list(q["by_team"].values())})
    st.altair_chart(bar_chart(teams, "team", "open", "Open tickets by team", fmt="d", height=alt.Step(26)), width="stretch")
section("Longest waiting for a first reply")
w = q["longest_waits"]
if w:
    st.markdown(html_table(["Ticket", "Priority", "Waiting", "Target", "State", "Message"], [[f"<code>{esc(t['ticket_id'])}</code>", status_badge(t["priority"]), fmt_min(t["first_response_used_min"]),
                 fmt_min(t["first_response_window_min"]), status_badge(t["sla_state"]), esc((t["query_text"] or "")[:90])] for t in w]), unsafe_allow_html=True)
else:
    st.caption("Everything open has had a first reply.")
section("Sample data")
st.caption("A working day of 56 customer messages: answered, waiting, urgent, late and undeliverable ones. Reloading replaces it with a fresh copy.")
load_sample("Reload sample data", "reload_demo")
section("Where to go")
c1, c2, c3 = st.columns(3)
c1.page_link("views/1_Support_Desk.py", label="Work the queue")
c2.page_link("views/2_New_Request.py", label="Send a customer message")
c3.page_link("views/5_SLA_Monitor.py", label="Check SLA clocks")
