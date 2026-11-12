"""Run a batch: the LangGraph workflow, pausing for a human review before anything risky is sent."""
import pandas as pd
import streamlit as st

from ui.common import call, esc, html_table, label, need, page_header, page_setup, section, sidebar, stat_tiles, status_badge

page_setup("Run a batch")
uid, user = sidebar()
from supportdesk.config import PATHS, load_settings  # noqa: E402
from supportdesk.workflow import Workflow, auto_agent_policy, HUMAN_STATUSES  # noqa: E402

page_header("Run a batch", "Load a file of customer queries, let the system sort them, then decide what the agents do at the review pause.")
samples = sorted(p.name for p in (PATHS.root / "data" / "samples").glob("*.csv"))
c1, c2 = st.columns([1.3, 1])
choice = c1.selectbox("Source file", ["customer_queries.csv"] + [f"data/samples/{s}" for s in samples if s.endswith(".csv")])
up = c2.file_uploader("or upload a CSV", type=["csv"])
src = str(PATHS.root / choice)
if up is not None:
    PATHS.uploads.mkdir(parents=True, exist_ok=True)
    p = PATHS.uploads / ("up_" + up.name.replace("/", "_"))
    p.write_bytes(up.getvalue())
    src = str(p)
if st.button("Start the workflow", type="primary", disabled="run" not in user["perms"]):
    from supportdesk.mcp import get_hub
    bid = "BATCH-" + pd.Timestamp.now().strftime("%m%d-%H%M%S")
    wf = Workflow(get_hub(), load_settings())
    with st.spinner("Loading, checking, classifying and routing ..."):
        pend = wf.start(bid, src, uid)
    st.session_state["wf"], st.session_state["wf_batch"], st.session_state["wf_done"] = wf, bid, None
    st.rerun()
wf, bid = st.session_state.get("wf"), st.session_state.get("wf_batch")
if not wf:
    st.stop()
done = st.session_state.get("wf_done")
st.caption(f"Batch {bid}")
pend = None if done else wf.pending()
if pend is None and not done:
    done = wf.state
if pend is not None:
    section("Paused for the agents", "Tickets that need a person are listed with a suggested action. Change any of them, then continue.")
    q = [t for t in need(call("list_tickets", batch_id=bid, status=HUMAN_STATUSES, limit=1000))["tickets"] if not t["first_response_at"]]
    if not q:
        st.info("Nothing in this file needs a person. Press continue to finish the batch (rows already processed in an earlier batch are skipped).")
    sug = [auto_agent_policy(t) for t in q]
    df = pd.DataFrame([{"Ticket": t["ticket_id"], "Priority": t["priority"], "Message": (t["query_text"] or "")[:80], "Type": t["intent"], "Suggested": s["action"], "Agent": s["actor"]} for t, s in zip(q, sug)])
    ed = st.data_editor(df, hide_index=True, width="stretch", disabled=["Ticket", "Priority", "Message", "Type", "Agent"], height=min(440, 40 + 35 * len(df)),
                        column_config={"Suggested": st.column_config.SelectboxColumn("Action", options=["APPROVE", "REQUEST_INFORMATION", "HOLD", "RESOLVE", "ESCALATE"], required=True)})
    if len(df):
        st.caption(f"{len(df)} tickets are waiting. Leave a row as suggested or change its action.")
    if st.button("Continue the workflow", type="primary"):
        decs = [{"ticket_id": r["Ticket"], "actor": s["actor"], "action": r["Suggested"], "note": s["note"], **({"team": "Incident Response"} if r["Suggested"] == "ESCALATE" else {})} for (_, r), s in zip(ed.iterrows(), sug)]
        with st.spinner("Applying decisions and sending messages ..."):
            st.session_state["wf_done"] = wf.resume(decs)
        st.rerun()
    st.stop()
rep = (done or {}).get("report") or {}
if (done or {}).get("status") == "failed" or (not (done or {}).get("valid") and (done or {}).get("errors")):
    st.error("The workflow stopped: " + "; ".join(str(e).replace("inspect_source: Error executing tool inspect_source: ", "").replace(" [INPUT]", "") for e in (done.get("errors") or ["no usable records"])[:2]))
    st.caption("Nothing was created. Check the file and start again.")
    st.stop()
section("Finished")
tk = need(call("list_tickets", batch_id=bid, limit=2000))["tickets"]
by = pd.Series([label(t["status"]) for t in tk]).value_counts()
stat_tiles([("Tickets", len(tk)), ("Resolved", int((pd.Series([t["status"] for t in tk]) == "RESOLVED").sum())), ("Waiting for customer", int((pd.Series([t["status"] for t in tk]) == "AWAITING_CUSTOMER").sum())),
            ("Urgent", sum(t["priority"] == "P1" for t in tk)), ("Rejected input", (done or {}).get("counts", {}).get("rejected", 0) if isinstance((done or {}).get("counts"), dict) else 0)])
st.dataframe(pd.DataFrame([{"Ticket": t["ticket_id"], "Priority": t["priority"], "Type": t["intent"], "Route": label(t["route"]), "Status": label(t["status"]), "Message": (t["query_text"] or "")[:80]} for t in tk]), hide_index=True, width="stretch")
errs = (done or {}).get("errors") or []
if errs:
    st.warning("; ".join(str(e) for e in errs[:3]))
