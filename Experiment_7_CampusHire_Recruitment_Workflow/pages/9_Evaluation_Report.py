"""Evaluation report: results of a stored evaluation run."""
import altair as alt
import pandas as pd
import streamlit as st

from ui.common import SERIES, bar_chart, esc, grouped_bar, html_table, label, page_header, page_setup, section, stat_tiles, status_bar

page_setup("Evaluation report")
from campushire.evaluation.runner import list_reports, load_report  # noqa: E402

page_header("Evaluation report")
reps = list_reports()
if not reps:
    st.info("No evaluation yet.")
    st.page_link("pages/8_Evaluation_Suite.py", label="Run evaluation")
    st.stop()
run = st.selectbox("Report", [r["run"] for r in reps], format_func=lambda r: next(f"{x['run']} · quality index {x['headline'].get('quality_index')}" for x in reps if x["run"] == r))
rep = load_report(run)
h, S, T = rep["headline"], rep["summaries"], {k: pd.DataFrame(v) for k, v in rep["tables"].items()}


def pct(k, d=1):
    v = h.get(k)
    return "-" if v is None else f"{v:.{d}f}%"


section("Lab manual metrics", "Measured on the 120-record drive.")
stat_tiles([("Data validation rate", pct("data_validation_rate_pct"), f"{h.get('lab_valid_records')} of {h.get('lab_records')} records valid"), ("Workflow success rate", pct("workflow_success_rate_pct"), "scored, drafted and not failed"),
            ("Notification draft rate", pct("notification_draft_rate_pct"), "drafts per valid record"), ("Test pass rate", pct("test_pass_rate_pct"), "all test suites"),
            ("Time per valid record", f"{h.get('avg_time_per_valid_record_ms') or 0:.0f} ms", "whole workflow"), ("Human review completion", pct("human_review_completion_rate_pct"), "decided and closed")])
stat_tiles([("Quality index", h.get("quality_index"), "weighted, 0-100"), ("Score accuracy", pct("score_accuracy_pct"), "exact to 0.01"), ("Routing accuracy", pct("routing_accuracy_pct"), "incl. 75 / 74.99 / 50"),
            ("Delivery success", pct("delivery_success_rate_pct"), "after 2 retries"), ("Instruction text flagged", pct("flag_recall_pct"), "pattern based"), ("Overrides", h.get("overrides"))])
st.caption(f"Generated {rep['generated_at']} · {rep['total_s']} s · {rep['report_dir']}")

tabs = st.tabs(["Accuracy", "Functional", "Human review", "Security", "Failure handling", "Hard cases", "Performance", "Rules and fairness"])
with tabs[0]:
    if "accuracy" in T:
        a, df = S["accuracy"], T["accuracy"]
        st.caption("Validation, rejection reason, score and routing include the 72 hard-case records; the 120 reference rows alone score 100% on each.")
        stat_tiles([("Validation", pct("validation_accuracy_pct")), ("Rejection reason", pct("rejection_code_accuracy_pct")), ("Rule 1.1 routing", pct("rule_v11_routing_accuracy_pct")),
                    ("Boundary cases", pct("boundary_cases_correct_pct"), "49.99 · 50 · 74.99 · 75"), ("Repeat uploads skipped", pct("batch2_repeats_skipped_pct")),
                    ("Updates quarantined", pct("batch2_updates_quarantined_pct"))])
        c1, c2 = st.columns(2)
        with c1:
            dist = pd.DataFrame([{"status": label(k), "candidates": v} for k, v in a["routing_distribution"].items()])
            st.altair_chart(bar_chart(dist, "status", "candidates", "Routing of the 104 valid records", fmt="d", x_title=None), width="stretch")
        with c2:
            q = df[~df["expect_valid"]]["code"].value_counts().to_dict()
            st.altair_chart(status_bar({k.replace("_", " ").title(): v for k, v in q.items()}, "Quarantined by reason"), width="stretch")
        bad = df[(~df["validation_ok"]) | (~df["code_ok"]) | (df["score_ok"] == False) | (df["route_ok"] == False) | (df["flag_ok"] == False)]  # noqa: E712
        section("Where the system and the reference disagree")
        if len(bad):
            st.dataframe(bad[["row", "candidate_id", "tag", "expected_status", "status", "expected_score", "score", "should_flag", "flagged"]], hide_index=True, width="stretch")
        else:
            st.caption("No disagreements.")
        with st.expander("All 120 rows"):
            st.dataframe(df, hide_index=True, width="stretch")
for i, sec in ((1, "functional"), (2, "hitl"), (3, "security"), (4, "failure")):
    with tabs[i]:
        if sec in T:
            df, sm = T[sec], S[sec]
            c1, c2 = st.columns([1, 2])
            with c1:
                st.altair_chart(status_bar(df["result"].value_counts().to_dict(), f"{sm['passed']} of {sm['tests']} passed"), width="stretch")
                st.markdown(html_table(["Category", "Pass rate"], [[esc(k), f"{v:.0f}%"] for k, v in sm["by_category"].items()], num_cols=(1,)), unsafe_allow_html=True)
            with c2:
                st.dataframe(df[["id", "category", "test", "expected", "observed", "result", "detail"]], hide_index=True, width="stretch", height=min(38 * (len(df) + 1), 720))
with tabs[5]:
    if "hard" in T:
        df, sm = T["hard"], S["hard"]
        stat_tiles([("All hard-suite cases", f"{sm['passed']} of {sm['tests']}", f"{sm['pass_rate_pct']:.1f}%"), ("Ordinary variants", pct("hard_controls_pass_pct"), "should always pass"),
                    ("Hard cases", pct("hard_cases_pass_pct"), "known limits stay as failures"), ("Instruction text found", f"{sm['flags_found']} of {sm['flags_expected']}", f"{sm['flag_false_positives']} false alarm(s)")])
        c1, c2 = st.columns([1, 2])
        with c1:
            st.altair_chart(status_bar(df["result"].value_counts().to_dict(), f"{sm['passed']} of {sm['tests']} passed"), width="stretch")
            st.markdown(html_table(["Category", "Pass rate"], [[esc(k), f"{v:.0f}%"] for k, v in sm["by_category"].items()], num_cols=(1,)), unsafe_allow_html=True)
        with c2:
            st.dataframe(df[["id", "category", "kind", "test", "expected", "observed", "result"]], hide_index=True, width="stretch", height=720)
    else:
        st.caption("Run the evaluation with the hard cases to see this tab.")
with tabs[6]:
    if "performance" in T:
        p, df = S["performance"], T["performance"]
        stat_tiles([("Sequential", f"{p['sequential_ms'] / 1000:.1f} s", f"{p['calls_sequential']} MCP calls"), ("Batch", f"{p['batch_ms'] / 1000:.1f} s", f"{p['batch_speedup']}x faster · {p['calls_batch']} calls"),
                    ("Parallel", f"{p['parallel_ms'] / 1000:.1f} s", f"{p['parallel_speedup']}x faster"), ("Same output in every mode", "Yes" if p["outputs_identical_across_modes"] else "No"),
                    ("Connection reuse", f"{p['connection_reuse_speedup']}x", f"{p['fresh_call_ms']:.0f} -> {p['reused_call_ms']:.0f} ms per call")])
        g = df.groupby("mode", as_index=False)[["wall_ms", "score_ms", "draft_ms", "dispatch_ms"]].mean()
        long = g.melt(id_vars="mode", var_name="step", value_name="ms")
        long["step"] = long["step"].map({"wall_ms": "Whole run", "score_ms": "Scoring", "draft_ms": "Drafting", "dispatch_ms": "Review and sending"})
        st.altair_chart(grouped_bar(long[long["step"] != "Whole run"].assign(mode=lambda d: d["mode"].str.title()), "step", "mode", "ms", "Time per step (average of 3 runs)", fmt=".0f", y_title="ms"), width="stretch")
        st.dataframe(df, hide_index=True, width="stretch")
with tabs[7]:
    an = S.get("analysis")
    if an:
        c = an["compare"]
        stat_tiles([("Candidates compared", c["candidates"]), ("Same status in 1.0 and 1.1", c["unchanged"]), ("Status changes", c["changed"]), ("Stability", f"{c['stability']:.1%}")])
        if "rule_moves" in T and len(T["rule_moves"]):
            st.dataframe(T["rule_moves"], hide_index=True, width="stretch")
        section("Fairness review")
        for f in an["fairness_flags"]:
            st.warning(f)
        if "fairness" in T:
            fr = T["fairness"]
            col = fr[fr["dimension"] == "college"]
            st.altair_chart(bar_chart(col, "group", "rate", "Shortlist-review rate by college", fmt=".0%", domain=(0, 1), x_title=None), width="stretch")
        for n in an["criteria_notes"]:
            st.markdown(f"- {n}")
