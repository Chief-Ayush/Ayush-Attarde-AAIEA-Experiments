"""Support desk: the agent's working page - queue, draft review and actions."""
import pandas as pd
import streamlit as st

from ui.common import badge, call, esc, fmt_min, html_table, label, need, page_header, page_setup, section, sidebar, status_badge, summary_strip

page_setup("Support desk")
uid, user = sidebar()
from supportdesk.config import PRIORITIES  # noqa: E402
import workflow_router as _wr
TEAMS = sorted(set(_wr.TEAM_BY_INTENT.values()) | {"Incident Response"})  # noqa: E402

page_header("Support desk", "Your queue, highest priority first.")
can_review = "review" in user["perms"]
if st.session_state.get("flash"):
    st.success(st.session_state.pop("flash"))
c1, c2, c3, c4 = st.columns([1, 1.8, 1.1, 1])
scope = c1.selectbox("Show", ["My team" if user["role"] == "agent" else "All teams", "All teams"] if user["role"] != "agent" else ["My team"], label_visibility="collapsed")
pri = c2.multiselect("Priority", PRIORITIES, default=PRIORITIES, label_visibility="collapsed", placeholder="Priority")
view = c3.selectbox("Tickets", ["Needs a first reply", "All open", "Waiting for customer", "Resolved"], label_visibility="collapsed")
sched = c4.selectbox("Order", ["Priority", "First in first out"], label_visibility="collapsed")
status_map = {"Needs a first reply": ["OPEN", "PENDING_APPROVAL", "IN_REVIEW", "ESCALATED", "REOPENED"], "All open": None, "Waiting for customer": ["AWAITING_CUSTOMER"], "Resolved": ["RESOLVED", "CLOSED"]}
team = user["team"] if (user["role"] == "agent") else ""
d = need(call("list_tickets", status=status_map[view], team=team, active_only=view == "All open", limit=400))
rows = [t for t in d["tickets"] if t["priority"] in pri and not t["batch_id"].startswith("EVAL-")]
if view == "Needs a first reply":
    rows = [t for t in rows if not t["first_response_at"]]
rows.sort(key=(lambda t: t["created_at"]) if sched.startswith("First") else (lambda t: (t["priority"], t["first_response_due"])))
if not rows:
    st.info("Nothing here. Switch the view or send a new request.")
    st.stop()
if st.button("Take next ticket", type="primary", disabled=not can_review):
    n = need(call("next_ticket", actor=uid, scheduling="fifo" if sched.startswith("First") else "priority"))
    if n["ticket"]:
        st.session_state["sel"] = n["ticket"]["ticket_id"]
    else:
        st.toast("Queue is empty")
df = pd.DataFrame([{"Ticket": t["ticket_id"], "Priority": t["priority"], "Customer message": (t["query_text"] or "")[:95], "Type": t["intent"], "Route": label(t["route"]), "Status": label(t["status"]), "SLA": label(t["sla_state"]), "Used": f"{fmt_min(t['first_response_used_min'])} of {fmt_min(t['first_response_window_min'])}"} for t in rows])
sel = st.dataframe(df, hide_index=True, width="stretch", height=min(420, 38 + 35 * len(df)), on_select="rerun", selection_mode="single-row", key="queue_tbl",
                   column_config={"Ticket": st.column_config.TextColumn(width=112), "Priority": st.column_config.TextColumn(width=62), "Customer message": st.column_config.TextColumn(width=236),
                                  "Type": st.column_config.TextColumn(width=104), "Route": st.column_config.TextColumn(width=118), "Status": st.column_config.TextColumn(width=78), "SLA": st.column_config.TextColumn(width=70),
                                  "Used": st.column_config.TextColumn(width=104)})
if sel.selection.rows:
    st.session_state["sel"] = df.iloc[sel.selection.rows[0]]["Ticket"]
tid = st.session_state.get("sel")
if not tid:
    st.caption("Select a ticket to read it, or take the next one.")
    st.stop()
g = need(call("get_ticket", ticket_id=tid))
t, fr = g["ticket"], g["first_response"]
st.divider()
st.markdown(f"### {esc(t['ticket_id'])} &nbsp; {status_badge(t['priority'])} {status_badge(t['sla_state'])} {status_badge(t['status'])}", unsafe_allow_html=True)
summary_strip([("Customer", esc(t["customer_id"])), ("Type", esc(t["intent"])), ("Complexity", esc(t["complexity"])), ("Team", esc(t["assigned_team"])),
               ("First reply", f"{fmt_min(fr['used_min'])} of {fmt_min(fr['window_min'])}"), ("Reopened", t["reopen_count"] or 0)])
cl, cr = st.columns([1, 1.15])
with cl:
    section("Customer message")
    st.info(t["query_text"])
    import json as _j
    try:
        fl = _j.loads(t["flags"]) if t.get("flags") else {}
    except ValueError:
        fl = {}
    for k, v in (fl or {}).items():
        if v:
            st.warning(f"Flag: {k.replace('_', ' ')}" + (f" ({v})" if v is not True else ""))
    section("Why it landed here")
    st.markdown(html_table(["", ""], [["Route", esc(label(t["route"]))], ["Matched article", esc(f"{t['article_id']} ({t['match_score']:.0f}/100)") if t["article_id"] else "none"],
                                      ["Priority", esc(t["priority"] + (f" (raised from {t['base_priority']})" if t["base_priority"] != t["priority"] else ""))]]), unsafe_allow_html=True)
with cr:
    section("Reply to send")
    closed = t["status"] in ("RESOLVED", "CLOSED")
    draft = t["final_response"] or t["proposed_response"] or ""
    txt = st.text_area("Reply", draft, height=190, label_visibility="collapsed", disabled=closed or not can_review, key=f"draft_{tid}")
    note = st.text_input("Note for the record", key=f"note_{tid}", disabled=closed or not can_review)
    b = st.columns([1.5, 1.4, 0.8, 1])
    off = closed or not can_review

    import ticket_manager as _tm
    DEFAULT_NOTE = {"REQUEST_INFORMATION": "Asked the customer for more details", "HOLD": "Put on hold by the agent", "RESOLVE": "Resolved by the agent", "ESCALATE": "Escalated by the agent"}

    def ok(action):                      # an action is offered only when the ticket's current state allows it
        return _tm.can_transition(t["status"], _tm.ACTION_TARGET[action])

    def act(action, **kw):
        r = call("review_ticket", ticket_id=tid, action=action, actor=uid, note=note.strip() or DEFAULT_NOTE.get(action, ""), **kw)
        if not r.ok:
            st.error(str(r.error).replace("Error executing tool ", ""))
            return
        sent = None
        if action in ("APPROVE", "REQUEST_INFORMATION", "RESOLVE"):
            d = call("dispatch_pending", actor=uid, ticket_ids=[tid])
            sent = d.data if d.ok else None
        msg = f"{tid}: " + {"APPROVE": "approved", "REQUEST_INFORMATION": "details requested from the customer", "HOLD": "put on hold", "RESOLVE": "resolved", "ESCALATE": "escalated"}.get(action, action.lower())
        if sent is not None:
            msg += " and message sent" if sent.get("sent") else (" but the message could not be delivered (see Notifications)" if sent.get("dead_letter") else " (no message was sent)")
        st.session_state["flash"] = msg
        st.rerun()
    if b[0].button("Approve and send", type="primary", disabled=off or not ok("APPROVE") or not txt.strip(), help=None if ok("APPROVE") else "This ticket is not waiting for an approval."):
        act("APPROVE", edited_response=txt if txt.strip() != draft.strip() else "")
    if b[1].button("Ask for details", disabled=off or not ok("REQUEST_INFORMATION")):
        act("REQUEST_INFORMATION")
    if b[2].button("Hold", disabled=off or not ok("HOLD")):
        act("HOLD")
    if b[3].button("Resolve", disabled=off or not ok("RESOLVE")):
        act("RESOLVE")
    e1, e2 = st.columns([1.4, 1])
    esc_team = e1.selectbox("Escalate to", [x for x in TEAMS if x != t["assigned_team"]], label_visibility="collapsed", disabled=off or not ok("ESCALATE"))
    if e2.button("Escalate", disabled=off or not ok("ESCALATE")):
        act("ESCALATE", team=esc_team)
    if not can_review:
        st.caption("Your role can read tickets but not act on them.")
    if t["status"] == "AWAITING_CUSTOMER":
        section("Customer answer", "Simulates the customer replying to the message that was sent.")
        k1, k2 = st.columns(2)
        if k1.button("It helped"):
            call("customer_reply", ticket_id=tid, helped=True, actor=uid)
            st.rerun()
        if k2.button("It did not help"):
            call("customer_reply", ticket_id=tid, helped=False, actor=uid)
            st.rerun()
section("Comments")
for h in [x for x in g["history"] if x.get("kind") == "COMMENT"][-4:]:
    st.caption(f"{h.get('actor')}: {h.get('note')}")
ctext = st.text_input("Add an internal comment", key=f"cm_{tid}")
if st.button("Add comment", disabled=not ctext.strip() or not can_review):
    r = call("add_comment", ticket_id=tid, text=ctext, actor=uid)
    if r.ok:
        st.rerun()
    st.error(r.error)
st.page_link("views/3_Ticket_Lookup.py", label="Full history of this ticket")
