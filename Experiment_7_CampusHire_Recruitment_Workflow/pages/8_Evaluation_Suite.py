"""Evaluation suite: run the sections and store a report."""
import pandas as pd
import streamlit as st

from ui.common import esc, html_table, page_header, page_setup, section, stat_tiles

page_setup("Evaluation suite")
from campushire.evaluation.runner import ALL_SECTIONS, SECTION_INFO, WEIGHTS, list_reports, run_all  # noqa: E402

page_header("Run evaluation", "Every case runs through the MCP servers in its own drive.")
section("Sections")
chosen = [s for s in ALL_SECTIONS if st.checkbox(s.capitalize(), value=True, key=f"sec_{s}", help=SECTION_INFO[s])]
st.caption("All sections take about 2.5 minutes.")
if st.button("Run evaluation", type="primary", disabled=not chosen):
    bar = st.progress(0.0)
    rep = run_all(chosen, progress=lambda f, m: bar.progress(f, text=m))
    bar.empty()
    h = rep["headline"]
    st.success(f"Finished in {rep['total_s']} s · {rep['report_dir']}")
    stat_tiles([("Quality index", h.get("quality_index")), ("Validation", f"{h.get('validation_accuracy_pct') or 0:.1f}%"), ("Routing", f"{h.get('routing_accuracy_pct') or 0:.1f}%"),
                ("Security", f"{h.get('security_pass_pct') or 0:.1f}%"), ("Failure handling", f"{h.get('failure_pass_pct') or 0:.1f}%"), ("Human review", f"{h.get('hitl_pass_pct') or 0:.1f}%")])
section("Quality index weights")
st.markdown(html_table(["Measure", "Weight"], [[esc(k.replace("_pct", " (%)").replace("_", " ")), f"{w:.2f}"] for k, w in WEIGHTS], num_cols=(1,)), unsafe_allow_html=True)
section("Previous runs")
reps = list_reports()
if reps:
    st.dataframe(pd.DataFrame([{"run": r["run"], "generated": r["generated_at"], "sections": ", ".join(r["sections"] or []), "time (s)": r["total_s"],
                                "quality index": r["headline"].get("quality_index"), "routing accuracy": r["headline"].get("routing_accuracy_pct"),
                                "security": r["headline"].get("security_pass_pct")} for r in reps]), hide_index=True, width="stretch")
else:
    st.caption("No evaluation has been run yet.")
