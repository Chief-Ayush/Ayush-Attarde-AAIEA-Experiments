"""Evaluation report: headline metrics, comparisons, hard cases and every table from the latest run."""
import altair as alt
import pandas as pd
import streamlit as st

from ui.common import SERIES, STATUS, bar_chart, confusion_chart, esc, grouped_bar, html_table, page_header, page_setup, section, sidebar, stat_tiles, status_bar

page_setup("Evaluation report")
uid, user = sidebar()
from supportdesk.evaluation.runner import list_reports, load_report  # noqa: E402

page_header("Evaluation report", "Measured results from the stored runs. Known failures are shown, not hidden.")
reps = list_reports()
if not reps:
    st.info("No report yet. Run the evaluation first.")
    st.page_link("views/11_Run_Evaluation.py", label="Run evaluation")
    st.stop()
run = st.selectbox("Run", [r["run"] for r in reps], format_func=lambda r: f"{r} ({next(x['generated_at'] for x in reps if x['run'] == r)})", label_visibility="collapsed")
rep = load_report(run)
h, T, S = rep["headline"], rep["tables"], rep["summaries"]


def p(k, d=1):
    v = h.get(k)
    return "-" if v is None else f"{v:.{d}f}%"


stat_tiles([("Quality index", "-" if h.get("quality_index") is None else h["quality_index"], "weighted overall" if h.get("quality_index") is not None else "needs all sections"), ("Intent", p("intent_accuracy_pct")), ("Priority", p("priority_accuracy_pct")), ("Answer retrieval", p("retrieval_top1_pct")),
            ("Safe routing", p("safe_route_pct")), ("Hard cases", p("hard_pass_pct"))])
tab = st.tabs(["Summary", "Classifier and retrieval", "Workflow ablation", "Scheduling", "Hard cases", "Test suites", "Performance"])
with tab[0]:
    rows = [("Intent accuracy", "intent_accuracy_pct"), ("Intent macro-F1", "intent_macro_f1_pct"), ("Priority accuracy", "priority_accuracy_pct"), ("Complexity accuracy", "complexity_accuracy_pct"),
            ("Answer retrieval top-1", "retrieval_top1_pct"), ("Exact route accuracy", "route_accuracy_pct"), ("Safe routing", "safe_route_pct"), ("P1 recall", "p1_recall_pct"),
            ("Automation rate", "automation_rate_pct"), ("Automatic answer precision", "auto_answer_precision_pct"), ("False auto-resolution", "false_auto_resolution_pct"),
            ("Resolution rate", "resolution_rate_pct"), ("Escalation decision accuracy", "escalation_decision_accuracy_pct"), ("Notification success", "notification_success_pct"),
            ("Retry recovery", "retry_recovery_rate_pct"), ("Audit completeness", "audit_completeness_pct"), ("Workflow success", "workflow_success_pct"), ("Human review completion", "human_review_completion_pct"),
            ("Functional tests", "functional_pass_pct"), ("Agent workflow tests", "agent_pass_pct"), ("Security tests", "security_pass_pct"), ("Reliability tests", "reliability_pass_pct")]
    st.markdown(html_table(["Measure", "Value"], [[esc(a), p(b)] for a, b in rows if h.get(b) is not None], num_cols=(1,)), unsafe_allow_html=True)
    if rep.get("intent_confusion"):
        cls = rep["intent_classes"] and list(rep["intent_classes"]) or []
        cm = rep["intent_confusion"]
        classes = list(cm) if isinstance(cm, dict) else []
        if classes:
            st.altair_chart(confusion_chart(cm, classes, "Intent: labelled vs predicted"), width="stretch")
with tab[1]:
    if "classifiers" in T:
        df = pd.DataFrame(T["classifiers"])
        st.dataframe(df, hide_index=True, width="stretch")
    if "retrieval" in T:
        section("Retrieval methods")
        st.dataframe(pd.DataFrame(T["retrieval"]), hide_index=True, width="stretch")
with tab[2]:
    if "ablation" in T:
        df = pd.DataFrame(T["ablation"])
        st.dataframe(df, hide_index=True, width="stretch")
        long = df.melt(id_vars="configuration", value_vars=["intent_accuracy_pct", "automation_rate_pct", "auto_answer_precision_pct", "safe_route_pct"], var_name="metric", value_name="value")
        long["metric"] = long["metric"].str.replace("_pct", "").str.replace("_", " ")
        st.altair_chart(grouped_bar(long, "configuration", "metric", "value", "Configurations compared"), width="stretch")
with tab[3]:
    if "scheduling" in T:
        st.dataframe(pd.DataFrame(T["scheduling"]), hide_index=True, width="stretch")
        a = S.get("analysis", {})
        st.caption(f"Busy period of {a.get('busy_window_min')} minutes; team utilisation: " + ", ".join(f"{k} {v:.0f}%" for k, v in (a.get("utilisation_pct") or {}).items()))
with tab[4]:
    if "hard" in T:
        hd = S["hard"]
        stat_tiles([("Tier 1", f"{hd['tier1_pass_pct']:.1f}%", "fixed after the first run"), ("Tier 2", f"{hd['tier2_pass_pct']:.1f}%", "unseen cases"), ("Controls", f"{hd['control_pass_pct']:.1f}%"), ("Failed", hd["failed"], f"of {hd['tests']}")])
        df = pd.DataFrame(T["hard"])
        flt = st.radio("Show", ["failed only", "all"], horizontal=True)
        st.dataframe(df if flt == "all" else df[df.result == "FAIL"], hide_index=True, width="stretch")
with tab[5]:
    for k in ("functional", "agent", "security", "reliability"):
        if k in T:
            df = pd.DataFrame(T[k])
            st.markdown(f"**{k.capitalize()}**: {S[k]['passed']} of {S[k]['tests']} passed")
            st.dataframe(df, hide_index=True, width="stretch", height=240)
with tab[6]:
    if "performance" in T:
        st.dataframe(pd.DataFrame(T["performance"]), hide_index=True, width="stretch")
        st.caption(f"Batch speed-up {S['performance']['batch_speedup']}x, parallel {S['performance']['parallel_speedup']}x. Results identical across modes: {S['performance']['outputs_identical_across_modes']}.")
