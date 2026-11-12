"""New request: a customer writes a message; the whole pipeline runs and the result is shown in plain words."""
import pandas as pd
import streamlit as st

from ui.common import call, esc, label, need, page_header, page_setup, section, sidebar, status_badge, summary_strip

page_setup("New request")
uid, user = sidebar()
from supportdesk.config import PATHS, load_settings  # noqa: E402
from supportdesk.workflow import Workflow  # noqa: E402

page_header("New request", "Write as a TaskNest customer. The message is checked, sorted, matched to an approved article and routed.")
EXAMPLES = {"": "", "Password reset": "I forgot my password and the reset email never arrives.", "Invoice charged twice": "I was charged twice for the Pro plan this month, please refund one payment.",
            "Whole team locked out": "None of our 40 people can log in since this morning, the whole workspace is down.", "Feature idea": "It would be great if tasks could repeat every other week.",
            "Data export error": "Exporting my project to CSV gives error 500 every time.", "Unclear": "it is not working"}
c1, c2 = st.columns([1, 2.4])
ex = c1.selectbox("Start from an example", list(EXAMPLES))
cust = c1.text_input("Customer ID", "C2001")
text = c2.text_area("Message", EXAMPLES[ex], height=120, placeholder="Describe the problem ...")
if st.button("Send", type="primary", disabled=not text.strip() or "run" not in user["perms"]):
    import uuid
    from supportdesk.mcp import get_hub
    qid, bid = "CHAT-" + uuid.uuid4().hex[:8].upper(), "CHAT-" + pd.Timestamp.now().strftime("%Y%m%d")
    cid = cust.strip() or "C2001"
    now = need(call("queue_summary"))["service_time"]
    PATHS.uploads.mkdir(parents=True, exist_ok=True)
    src = PATHS.uploads / f"{qid}.csv"
    pd.DataFrame([{"query_id": qid, "customer_id": cid, "query_text": text.strip(), "created_at": now}]).to_csv(src, index=False)
    with st.spinner("Checking, classifying and routing ..."):
        wf = Workflow(get_hub(), load_settings())
        wf.start(bid, str(src), uid)
    problems = [str(e) for e in (wf.state.get("errors") or [])]
    st.session_state["last_chat"] = {"query_id": qid, "batch_id": bid, "problems": problems}
    st.rerun()
sub = st.session_state.get("last_chat")
if not sub:
    st.stop()
tks = [t for t in need(call("list_tickets", batch_id=sub["batch_id"], limit=500))["tickets"] if t["query_id"] == sub["query_id"]]
if not tks:
    st.error("The message was not accepted. " + "; ".join(sub["problems"]) if sub["problems"] else "The message was not accepted (empty, too long or a duplicate).")
    st.stop()
t = tks[0]
section("Result")
route = t["route_class"]
msg = {"AUTO": "An approved answer was found and sent to the customer.", "URG": "Marked urgent and sent straight to the incident team.", "REV": "A person will review the answer before anything is sent.",
       "FEED": "Logged for the product team.", "INFO": "No reliable answer was found. The customer will be asked for more detail."}.get(route, "")
if route in ("AUTO", "FEED"):
    st.success(msg)
elif route == "URG":
    st.error(msg)
else:
    st.info(msg)
summary_strip([("Ticket", f"<code>{esc(t['ticket_id'])}</code>"), ("Priority", status_badge(t["priority"])), ("Type", esc(t["intent"])), ("Team", esc(t["assigned_team"])), ("Status", status_badge(t["status"])),
               ("SLA", status_badge(t["sla_state"]))])
g = need(call("get_ticket", ticket_id=t["ticket_id"]))
if g["ticket"]["proposed_response"] or g["ticket"]["final_response"]:
    section("Reply" + (" that was sent" if route == "AUTO" else " drafted for review"))
    st.text(g["ticket"]["final_response"] or g["ticket"]["proposed_response"])
c1, c2 = st.columns(2)
c1.page_link("views/1_Support_Desk.py", label="Open the support desk")
c2.page_link("views/3_Ticket_Lookup.py", label="Full ticket history")
