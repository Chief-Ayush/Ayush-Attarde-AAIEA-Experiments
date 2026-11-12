"""SLA monitor: deadlines, warnings, breaches, priority aging and the scheduling comparison."""
import altair as alt
import pandas as pd
import streamlit as st

from ui.common import grouped_bar, SERIES, STATUS, call, esc, fmt_min, html_table, label, need, page_header, page_setup, section, sidebar, stat_tiles, status_badge

page_setup("SLA monitor")
uid, user = sidebar()
page_header("SLA monitor", "Every open ticket has a first-reply and a resolution deadline. Warnings fire at 75% of the time used.")
q = need(call("queue_summary"))
c1, c2, c3, c4, c5 = st.columns([1, 1, 1, 1, 1.3])
can_clock = "clock" in user["perms"]
for col, mins, name in ((c1, 30, "+30 min"), (c2, 120, "+2 hours"), (c3, 1440, "+1 day")):
    if col.button(name, disabled=not can_clock, help="Moves the simulated service clock forward to show warnings and breaches."):
        need(call("advance_clock", minutes=mins, actor=uid))
        st.rerun()
if c4.button("Reset clock", disabled=not can_clock):
    need(call("reset_clock", actor=uid))
    st.rerun()
if c5.button("Check SLAs now", type="primary", disabled="run" not in user["perms"]):
    r = need(call("sla_scan", actor=uid))
    if r["new_alerts"]:                                       # the notices to the support lead are sent straight away
        call("dispatch_pending", actor=uid, ticket_ids=sorted({a["ticket_id"] for a in r["new_alerts"]}))
    st.session_state["scan"] = r
    st.rerun()
if not can_clock:
    st.caption("Moving the clock needs a lead or admin account.")
st.caption(f"Service time {q['service_time'][:16].replace('T', ' ')}")
scan = st.session_state.get("scan")
if scan:
    if scan["aged"]:
        st.warning(f"{len(scan['aged'])} ticket(s) raised in priority after waiting too long: " + ", ".join(f"{a['ticket_id']} {a['from']} to {a['to']}" if isinstance(a, dict) and 'from' in a else str(a) for a in scan["aged"][:5]))
    if scan["new_alerts"]:
        st.info(f"{len(scan['new_alerts'])} new alert(s) recorded and the support lead was notified.")
    else:
        st.caption("No new alerts since the last check.")
tk = [t for t in need(call("list_tickets", active_only=True, limit=1000))["tickets"] if not t["batch_id"].startswith("EVAL-")]
states = pd.Series([t["sla_state"] for t in tk]).value_counts().to_dict() if tk else {}
stat_tiles([("Open", len(tk)), ("Within SLA", states.get("WITHIN_SLA", 0)), ("At risk", states.get("AT_RISK", 0)), ("Breached", states.get("BREACHED", 0)), ("Paused", states.get("PAUSED", 0))])
if tk:
    df = pd.DataFrame([{"ticket": t["ticket_id"], "priority": t["priority"], "used": min(250, 100 * t["first_response_used_min"] / max(1, t["first_response_window_min"])), "state": label(t["sla_state"]), "text": (t["query_text"] or "")[:70]}
                       for t in tk if not t["first_response_at"]])
    section("Time used before the first reply", "100% is the deadline. Bars past the line have missed it.")
    if len(df):
        df = df.sort_values("used", ascending=False).head(25)
        base = alt.Chart(df).encode(y=alt.Y("ticket:N", sort=None, title=None, axis=alt.Axis(labelOverlap=False, labelLimit=140)), x=alt.X("used:Q", title="% of first-reply target used", scale=alt.Scale(domain=[0, max(130, df.used.max() + 10)])),
                                    color=alt.Color("state:N", scale=alt.Scale(domain=["Within SLA", "At risk", "Breached", "Paused"], range=[STATUS["good"], STATUS["warning"], STATUS["critical"], "#8a909a"]), legend=alt.Legend(title=None)),
                                    tooltip=["ticket", "priority", "text", alt.Tooltip("used:Q", format=".0f")])
        rule = alt.Chart(pd.DataFrame({"x": [100]})).mark_rule(color="#1c1f24", strokeDash=[4, 3]).encode(x="x:Q")
        st.altair_chart((base.mark_bar(cornerRadiusEnd=3, size=12) + rule).properties(height=alt.Step(20)), width="stretch")
    else:
        st.caption("Every open ticket has had its first reply.")
    st.dataframe(pd.DataFrame([{"Ticket": t["ticket_id"], "Priority": t["priority"] + (" (raised)" if t["aged"] else ""), "Team": t["assigned_team"], "Status": label(t["status"]), "SLA": label(t["sla_state"]),
                                "First reply": f"{fmt_min(t['first_response_used_min'])} of {fmt_min(t['first_response_window_min'])}"} for t in tk]), hide_index=True, width="stretch", height=300)
section("Scheduling comparison", "Same tickets, same agents, three ways of choosing the next ticket (latest evaluation).")
from supportdesk.evaluation.runner import load_report  # noqa: E402
rep = load_report()
sch = (rep or {}).get("tables", {}).get("scheduling")
if sch:
    sd = pd.DataFrame(sch)
    c1, c2 = st.columns([1.1, 1])
    c1.dataframe(sd.rename(columns={"scheduling": "Scheduling", "sla_compliance_pct": "On time %", "sd": "Spread", "mean_response_min": "Mean reply (min)", "p95_response_min": "Slowest 5% (min)"}), hide_index=True, width="stretch")
    long = sd.melt(id_vars="scheduling", value_vars=[c for c in ("P1_pct", "P2_pct") if c in sd], var_name="priority", value_name="on_time")
    long["priority"] = long["priority"].str.replace("_pct", "")
    c2.altair_chart(grouped_bar(long, "priority", "scheduling", "on_time", "Replies on time (%)", y_title="on time (%)"), width="stretch")
else:
    st.caption("Run the evaluation to see the comparison.")
