"""Traces and logs: mcp_traces.jsonl and execution.log."""
import json

import altair as alt
import pandas as pd
import streamlit as st

from ui.common import SERIES, page_header, page_setup, section, stat_tiles, status_bar, trace_frame

page_setup("Traces and logs")
from campushire.config import PATHS  # noqa: E402

page_header("Traces and logs", "Every MCP call: trace id, task id, time, status and error category.")


@st.cache_data(ttl=5)
def load_traces(mtime: float) -> pd.DataFrame:
    rows = []
    with open(PATHS.trace_file, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return pd.DataFrame(rows)


if not PATHS.trace_file.exists():
    st.info("No traces yet.")
    st.stop()
df = load_traces(PATHS.trace_file.stat().st_mtime)
stat_tiles([("Calls", len(df)), ("Workflows", df["trace_id"].nunique()), ("Failed", int((df["status"] != "success").sum())), ("Retried", int((df["attempt"] > 1).sum())),
            ("Median call", f"{df['execution_time_ms'].median():.0f} ms"), ("File size", f"{PATHS.trace_file.stat().st_size / 1024:.0f} KB")])
c1, c2 = st.columns([1, 1.4])
with c1:
    errs = df[df["status"] != "success"]["error_category"].fillna("?").value_counts().to_dict()
    if errs:
        st.altair_chart(status_bar(errs, "Failed calls by category", {"INPUT": "#eda100", "SECURITY": "#d03b3b", "TOOL": "#ec835a", "TIMEOUT": "#2a78d6", "EXECUTION": "#6250d6"}), width="stretch")
with c2:
    per = df.groupby("tool")["execution_time_ms"].median().reset_index().sort_values("execution_time_ms", ascending=False).head(14)
    st.altair_chart(alt.Chart(per).mark_bar(color=SERIES[0], cornerRadiusEnd=3).encode(y=alt.Y("tool:N", sort="-x", title=None, axis=alt.Axis(labelLimit=200)),
                                                                                       x=alt.X("execution_time_ms:Q", title="median time (ms)"), tooltip=["tool", alt.Tooltip("execution_time_ms:Q", format=".1f")])
                    .properties(title="Slowest tools", height=alt.Step(16)), width="stretch")
section("mcp_traces.jsonl")
c1, c2, c3 = st.columns([1.4, 1, 1])
tid = c1.selectbox("Trace", ["all"] + list(dict.fromkeys(df["trace_id"][::-1]))[:200])
tool = c2.selectbox("Tool", ["all"] + sorted(df["tool"].unique()))
status = c3.selectbox("Status", ["all"] + sorted(df["status"].unique()))
view = df
for col, val in (("trace_id", tid), ("tool", tool), ("status", status)):
    if val != "all":
        view = view[view[col] == val]
st.dataframe(trace_frame(view.tail(500).to_dict("records")[::-1]), hide_index=True, width="stretch")
section("execution.log")
if PATHS.execution_log.exists():
    st.code("\n".join(PATHS.execution_log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]), language=None)
