"""TaskNest SupportDesk - Streamlit entry point.  Run:  streamlit run app.py"""
import os

import streamlit as st


@st.cache_resource(show_spinner="Setting up the demo desk (first start only, about 15 seconds) ...")
def _first_start():
    """Fill an empty desk with a realistic working day (supportdesk/demo.py). Runs once; set SUPPORTDESK_NO_DEMO=1 to skip."""
    if os.environ.get("SUPPORTDESK_NO_DEMO"):
        return None
    from supportdesk.demo import ensure_demo
    try:
        return ensure_demo()
    except Exception as e:                                       # the app still opens; the Overview offers a button to try again
        return {"error": str(e)}


_first_start()

PAGES = [("views/0_Overview.py", "Overview"), ("views/1_Support_Desk.py", "Support desk"), ("views/2_New_Request.py", "New request"), ("views/3_Ticket_Lookup.py", "Ticket lookup"),
         ("views/4_Run_Batch.py", "Run a batch"), ("views/5_SLA_Monitor.py", "SLA monitor"), ("views/6_Notifications.py", "Notifications"), ("views/7_Audit_Log.py", "Audit log"),
         ("views/8_Test_Values.py", "Test values"), ("views/9_MCP_Inspector.py", "MCP inspector"), ("views/10_Traces_and_Logs.py", "Traces and logs"),
         ("views/11_Run_Evaluation.py", "Run evaluation"), ("views/12_Evaluation_Report.py", "Evaluation report"), ("views/13_Settings.py", "Settings")]
st.navigation([st.Page(p, title=t, default=(i == 0), url_path="" if i == 0 else None) for i, (p, t) in enumerate(PAGES)]).run()
