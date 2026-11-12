"""CampusHire - Streamlit entry point.  Run:  streamlit run app.py"""
import streamlit as st

PAGES = [("pages/0_Overview.py", "Overview"), ("pages/1_Run_Workflow.py", "Run workflow"), ("pages/2_Review_Queue.py", "Review queue"), ("pages/3_Quarantine.py", "Quarantine"),
         ("pages/4_Audit_Log.py", "Audit log"), ("pages/5_Reports.py", "Reports"), ("pages/6_MCP_Inspector.py", "MCP inspector"), ("pages/7_Batch_Testing.py", "Batch testing"), ("pages/12_Test_Values.py", "Test values"),
         ("pages/8_Evaluation_Suite.py", "Evaluation suite"), ("pages/9_Evaluation_Report.py", "Evaluation report"), ("pages/10_Traces_and_Logs.py", "Traces and logs"),
         ("pages/11_Settings.py", "Settings")]
st.navigation([st.Page(p, title=t, default=(i == 0), url_path="" if i == 0 else None) for i, (p, t) in enumerate(PAGES)]).run()
