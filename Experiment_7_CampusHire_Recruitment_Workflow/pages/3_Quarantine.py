"""Quarantine: records that failed validation. Nothing is deleted; each one has a reason and a next action."""
import pandas as pd
import streamlit as st

from ui.common import call, esc, html_table, need, page_header, page_setup, sidebar, stat_tiles, status_bar

page_setup("Quarantine")
from campushire.config import PATHS  # noqa: E402

uid, user, did = sidebar()
page_header("Quarantine", "Records that could not be scored. Each has a reason and a suggested next step.")
rej = need(call("get_queue", drive_id=did))["rejected"]
if not rej:
    st.success("No quarantined records in this drive.")
    st.stop()
df = pd.DataFrame(rej)
codes = df["code"].value_counts().to_dict()
stat_tiles([("Quarantined", len(df)), ("Duplicate IDs", int(df["code"].str.startswith("DUPLICATE").sum())), ("Missing data", codes.get("MISSING_FIELD", 0)),
            ("Invalid values", codes.get("OUT_OF_RANGE", 0) + codes.get("INVALID_NUMBER", 0) + codes.get("INVALID_EMAIL", 0) + codes.get("INVALID_FIELD", 0)),
            ("Unknown role", codes.get("UNKNOWN_ROLE", 0))])
c1, c2 = st.columns([1, 2.2])
with c1:
    st.altair_chart(status_bar({k.replace("_", " ").capitalize(): v for k, v in codes.items()}, "By reason"), width="stretch")
with c2:
    pick = st.multiselect("Reason", sorted(codes), format_func=lambda c: c.replace("_", " ").capitalize(), placeholder="All reasons")
    view = df[df["code"].isin(pick)] if pick else df
    st.dataframe(view.rename(columns={"row": "Row", "candidate_id": "ID", "candidate_name": "Name", "code": "Reason code", "reason": "Detail", "next_action": "Next step"})
                 [["Row", "ID", "Name", "Reason code", "Detail", "Next step"]], hide_index=True, width="stretch", height=420)
if PATHS.rejected_csv.exists():
    st.download_button("Download rejected_records.csv", PATHS.rejected_csv.read_bytes(), file_name="rejected_records.csv")
