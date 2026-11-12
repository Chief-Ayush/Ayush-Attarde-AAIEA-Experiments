"""Run evaluation: choose sections, run, and store a report."""
import pandas as pd
import streamlit as st

from ui.common import esc, html_table, page_header, page_setup, section, sidebar, stat_tiles

page_setup("Run evaluation")
uid, user = sidebar()
from supportdesk.evaluation.runner import ALL_SECTIONS, SECTION_INFO, WEIGHTS, list_reports, run_all  # noqa: E402

page_header("Run evaluation", "Every case runs through the MCP servers in its own isolated batch.")
section("Sections")
chosen = [s for s in ALL_SECTIONS if st.checkbox(s.capitalize(), value=True, key=f"sec_{s}", help=SECTION_INFO[s])]
st.caption("All sections together take several minutes. The report is stored and shown on the next page.")
if st.button("Run evaluation", type="primary", disabled=not chosen or "settings" not in user["perms"]):
    bar = st.progress(0.0)
    rep = run_all(chosen, progress=lambda f, m: bar.progress(f, text=m))
    bar.empty()
    h = rep["headline"]
    st.success(f"Finished in {rep['total_s']} s. Saved to {rep['report_dir']}")
    def pc(k):
        v = h.get(k)
        return "-" if v is None else f"{v:.1f}%"
    qi = h.get("quality_index")
    tiles = [("Quality index", "-" if qi is None else qi, "" if qi is not None else "needs all sections"), ("Intent", pc("intent_accuracy_pct")), ("Safe routing", pc("safe_route_pct")),
             ("Security", pc("security_pass_pct")), ("Reliability", pc("reliability_pass_pct")), ("Hard cases", pc("hard_pass_pct")), ("Functional", pc("functional_pass_pct"))]
    stat_tiles([x for x in tiles if x[1] != "-" or x[0] == "Quality index"])
if "settings" not in user["perms"]:
    st.caption("Running the evaluation needs an admin account.")
section("Quality index weights")
st.markdown(html_table(["Measure", "Weight"], [[esc(k.replace("_pct", " (%)").replace("_", " ")), f"{w:.2f}"] for k, w in WEIGHTS], num_cols=(1,)), unsafe_allow_html=True)
section("Previous runs")
reps = list_reports()
if reps:
    st.dataframe(pd.DataFrame([{"run": r["run"], "generated": r["generated_at"], "sections": ", ".join(r["sections"] or []), "time (s)": r["total_s"], "quality index": r["headline"].get("quality_index"),
                                "intent": r["headline"].get("intent_accuracy_pct"), "safe routing": r["headline"].get("safe_route_pct"), "hard cases": r["headline"].get("hard_pass_pct")} for r in reps]), hide_index=True, width="stretch")
else:
    st.caption("No evaluation has been run yet.")
