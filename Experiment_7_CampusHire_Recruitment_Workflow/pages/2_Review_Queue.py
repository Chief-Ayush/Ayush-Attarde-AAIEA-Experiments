"""Review queue: the human decision step - APPROVE, EDIT, HOLD or REQUEST_INFO, message approval and dispatch."""
import pandas as pd
import streamlit as st

from ui.common import (SERIES, badge, bar_chart, call, esc, hub, html_table, label, need, page_header, page_setup, section, sidebar, stat_tiles, status_badge)

page_setup("Review queue")
from campushire.config import REVIEW_ACTIONS, load_settings  # noqa: E402
from campushire.workflow import Runner, finalize_drive  # noqa: E402

uid, user, did = sidebar()
page_header("Review queue", "Nothing is sent until a reviewer has decided and approved the message.")
can_review, can_send = "review" in user["perms"], "dispatch" in user["perms"]
if not can_review:
    st.info(f"{user['name']} can view the queue but not record decisions.")

data = need(call("get_queue", drive_id=did))
q = data["queue"]
if not q:
    st.info("Nothing to review yet. Run the workflow first.")
    st.page_link("pages/1_Run_Workflow.py", label="Run workflow")
    st.stop()
drafts = {d["candidate_id"]: d for d in need(call("list_drafts", drive_id=did))["drafts"]}

decided = sum(1 for x in q if x["reviewed"])
sent = sum(1 for d in drafts.values() if d["state"] == "SENT")
stat_tiles([("In queue", len(q)), ("Decided", decided, f"{decided / len(q):.0%}"), ("Approved messages", sum(1 for d in drafts.values() if d["state"] == "APPROVED"), "ready to send"),
            ("Sent", sent), ("Failed", sum(1 for d in drafts.values() if d["state"] == "FAILED"))])

# ------------------------------------------------------------------------------------------------ filters
c1, c2, c3, c4 = st.columns([1.3, 1.3, 1, 1.4])
f_status = c1.multiselect("Proposed status", ["SHORTLIST_REVIEW", "MANUAL_REVIEW", "NOT_SHORTLISTED_REVIEW"], format_func=label, placeholder="All statuses")
f_role = c2.multiselect("Role", sorted({x["role"] for x in q}), placeholder="All roles")
pending_only = c3.toggle("Undecided only", value=True)
text = c4.text_input("Search", placeholder="Name or ID", label_visibility="collapsed")
rows = [x for x in q if (not f_status or x["proposed_status"] in f_status) and (not f_role or x["role"] in f_role) and (not pending_only or not x["reviewed"])
        and (not text or text.lower() in (x["candidate_name"] + x["candidate_id"]).lower())]
df = pd.DataFrame([{"ID": x["candidate_id"], "Name": x["candidate_name"], "Role": x["role"], "Score": x["score"], "Proposed": label(x["proposed_status"]),
                    "Decision": label(x["final_status"]) if x["final_status"] else "-", "Message": drafts.get(x["candidate_id"], {}).get("state", "-").replace("_", " ").title(),
                    "Flag": "Check text" if x["flags"] else ""} for x in rows])
st.caption(f"{len(df)} of {len(q)} candidates")
ev = st.dataframe(df, hide_index=True, width="stretch", height=min(40 + 35 * max(len(df), 1), 340), on_select="rerun", selection_mode="single-row",
                  column_config={"Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.2f")})
sel = ev.selection.rows[0] if ev.selection.rows else None

# ------------------------------------------------------------------------------------------------ bulk + dispatch
b1, b2, b3 = st.columns([1.6, 1.6, 2])
with b1:
    clean = [x for x in rows if not x["reviewed"] and not x["flags"]]
    if st.button(f"Approve {len(clean)} unflagged proposals and their messages", disabled=not (can_review and clean), help="Candidates with flagged text are skipped."):
        ok = 0
        for x in clean:
            r = call("record_review", drive_id=did, candidate_id=x["candidate_id"], action="APPROVE", reviewer=uid)
            if r.ok and drafts.get(x["candidate_id"]):
                ok += call("approve_draft", draft_id=drafts[x["candidate_id"]]["draft_id"], actor=uid).ok
        st.toast(f"{ok} decisions and messages approved")
        st.rerun()
with b2:
    if st.button("Send approved messages", type="primary", disabled=not can_send, help="Sends only messages a reviewer approved; held candidates are skipped."):
        with st.spinner("Sending ..."):
            fin = finalize_drive(Runner(hub(), load_settings()), did, uid, [])
        st.session_state["fin"] = fin
        st.rerun()
fin = st.session_state.pop("fin", None)
if fin:
    st.success(f"Sent {fin['sent']} · failed {fin['failed']} · held back {fin['withheld']}" + (f" · refused {fin['refused']}" if fin["refused"] else ""))
    if fin["errors"]:
        st.warning("; ".join(fin["errors"][:3]))
with b3:
    with st.popover("SLA reminders"):
        hours = st.number_input("Pretend this many hours have passed", 0, 500, 0, step=12, help="Moves the clock forward so reminders can be tried without waiting.")
        if st.button("Check reminders", disabled="run" not in user["perms"]):
            r = call("sla_check", drive_id=did, actor=uid, as_of_offset_hours=float(hours))
            if r.ok:
                st.write(f"{r.data['overdue']} waiting longer than {r.data['sla_hours']} h · {r.data['new_reminders']} new reminder(s)")
                if r.data["items"]:
                    st.dataframe(pd.DataFrame(r.data["items"]), hide_index=True, width="stretch")
            else:
                st.error(r.error)

# ------------------------------------------------------------------------------------------------ detail
if sel is None:
    st.stop()
cid = df.iloc[sel]["ID"]
item = next(x for x in q if x["candidate_id"] == cid)
dr = drafts.get(cid)
section(f"{item['candidate_name']} · {cid}")
c1, c2 = st.columns([1, 1.3])
with c1:
    st.markdown(html_table(["", ""], [["Score", f"<b>{item['score']:.2f}</b> / 100"], ["Proposed", status_badge(item["proposed_status"])],
                                      ["Decision", status_badge(item["final_status"])], ["Role", esc(item["role"])], ["College", esc(item["college"] or "-")],
                                      ["E-mail", esc(item["email"])], ["Rule version", esc(item["rule_version"])]]), unsafe_allow_html=True)
    for f in item["flags"]:
        st.warning(f["detail"])
with c2:
    bd = pd.DataFrame(item["breakdown"])
    st.markdown(html_table(["Criterion", "Input", "Weight", "Contribution"], [[esc(p["label"]), f"{p['input']:g}", f"{p['weight']:g}%", f"<b>{p['contribution']:.2f}</b> / {p['max']:g}"] for p in item["breakdown"]],
                           num_cols=(1, 2, 3)), unsafe_allow_html=True)
    if item["review"]:
        v = item["review"]
        st.caption(f"Last decision: {v['action']} by {v['reviewer']} ({v['reviewer_role'].replace('_', ' ')}) at {v['decided_at'][:16].replace('T', ' ')}"
                   + (f" · override reason: {v['override_reason']}" if v["override_reason"] else "") + (f" · note: {v['note']}" if v["note"] else ""))

t1, t2 = st.tabs(["Decision", "Message"])
with t1:
    with st.form(f"decide_{cid}"):
        a1, a2 = st.columns([1, 1.3])
        action = a1.radio("Action", REVIEW_ACTIONS, format_func=lambda a: {"APPROVE": "Approve proposal", "EDIT": "Change status", "HOLD": "Put on hold", "REQUEST_INFO": "Request information"}[a],
                          disabled=not can_review)
        new_status = a2.selectbox("New status (change status only)", ["SHORTLIST_REVIEW", "MANUAL_REVIEW", "NOT_SHORTLISTED_REVIEW"], format_func=label,
                                  index=["SHORTLIST_REVIEW", "MANUAL_REVIEW", "NOT_SHORTLISTED_REVIEW"].index(item["proposed_status"]))
        override = a2.text_input("Override reason", placeholder="Required when the status is changed")
        note = a2.text_input("Note", placeholder="Required for hold and request information")
        approve_msg = st.checkbox("Approve the message for sending", value=action == "APPROVE")
        go = st.form_submit_button("Save decision", type="primary", disabled=not can_review)
    if go:
        r = call("record_review", drive_id=did, candidate_id=cid, action=action, reviewer=uid, new_status=new_status if action == "EDIT" else "", note=note, override_reason=override)
        if not r.ok:
            st.error(r.error.replace("Error executing tool record_review: ", ""))
        else:
            final = r.data["final_status"]
            want = "REQUEST_INFO" if final == "AWAITING_INFORMATION" else final
            msgs = []
            if final in ("ON_HOLD", "REQUIRES_REVIEW"):
                if dr:
                    call("withhold_draft", draft_id=dr["draft_id"], reason=f"candidate is {final}", actor=uid)
                msgs.append("message held back")
            else:
                if not dr or dr["template"] != want:
                    call("generate_drafts", drive_id=did, items=[{"candidate_id": cid, "status": want}], actor=uid)
                    dr = next((d for d in need(call("list_drafts", drive_id=did))["drafts"] if d["candidate_id"] == cid), None)
                if approve_msg and dr:
                    ra = call("approve_draft", draft_id=dr["draft_id"], actor=uid)
                    msgs.append("message approved" if ra.ok else "message not approved: " + ra.error.split("] ")[-1])
            st.toast(f"Saved: {label(final)}" + (" · " + ", ".join(msgs) if msgs else ""))
            st.rerun()
with t2:
    if not dr:
        st.caption("No message drafted.")
    else:
        st.markdown(f"{badge(dr['state'].replace('_', ' ').title(), {'SENT': 'good', 'APPROVED': 'good', 'FAILED': 'bad', 'DRAFT': 'info'}.get(dr['state'], 'plain'))} "
                    f"<span class='cq-muted'>to {esc(dr['to'])}" + (f" · {dr['attempts']} attempt(s)" if dr["attempts"] else "") + (f" · {esc(dr['detail'])}" if dr["detail"] else "") + "</span>",
                    unsafe_allow_html=True)
        editable = can_review and dr["state"] not in ("SENT",)
        subj = st.text_input("Subject", dr["subject"], disabled=not editable, key=f"s_{cid}")
        body = st.text_area("Message", dr["message"], height=230, disabled=not editable, key=f"m_{cid}")
        e1, e2, _ = st.columns([1, 1, 3])
        if e1.button("Save edit", disabled=not editable or (subj == dr["subject"] and body == dr["message"]), key=f"se_{cid}"):
            r = call("edit_draft", draft_id=dr["draft_id"], subject=subj, message=body, actor=uid)
            st.toast("Message saved, approve it again" if r.ok else r.error.split("] ")[-1])
            if r.ok:
                st.rerun()
            else:
                st.error(r.error.split("] ")[-1])
        if e2.button("Approve message", disabled=not (can_review and dr["state"] == "DRAFT"), key=f"ap_{cid}"):
            r = call("approve_draft", draft_id=dr["draft_id"], actor=uid)
            if r.ok:
                st.rerun()
            st.error(r.error.split("] ")[-1])
