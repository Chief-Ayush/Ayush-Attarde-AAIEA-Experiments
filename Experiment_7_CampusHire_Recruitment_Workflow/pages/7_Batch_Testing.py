"""Batch testing: run several candidate files through the real workflow, compare execution modes and rule versions, keep every batch."""
import time

import altair as alt
import pandas as pd
import streamlit as st

from ui.common import SERIES, esc, grouped_bar, html_table, page_header, page_setup, section, stat_tiles

page_setup("Batch testing")
from campushire.config import PATHS  # noqa: E402
from campushire.evaluation import batch as B  # noqa: E402

page_header("Batch testing", "Run many candidate files through the full workflow and compare how they behave.")

SAMPLES = {"Sample drive (120 records)": "candidates.csv", "Short sample (10 records)": "data/samples/candidates_10.csv", "Second upload (JSON)": "data/samples/candidates_batch2.json"}
c1, c2 = st.columns([1.6, 1])
picked = c1.multiselect("Files", list(SAMPLES), default=list(SAMPLES)[:2])
ups = c1.file_uploader("Add your own files", type=["csv", "json"], accept_multiple_files=True)
modes = c2.multiselect("Execution modes", ["sequential", "batch", "parallel"], default=["sequential", "parallel"])
versions = c2.multiselect("Rule versions", ["1.0", "1.1"], default=["1.0"])
reps = c2.number_input("Repetitions", 1, 5, 1)
files = [(n, SAMPLES[n]) for n in picked]
for u in ups:
    p = PATHS.uploads / f"batch_{int(time.time())}_{u.name}"
    p.write_bytes(u.getvalue())
    files.append((u.name, p.relative_to(PATHS.root).as_posix()))
total = len(files) * len(modes) * len(versions) * int(reps)
st.caption(f"{total} run(s)")
if st.button("Run batch", type="primary", disabled=not (files and modes and versions)):
    bar = st.progress(0.0)
    df = B.run_batch(files, modes, versions, int(reps), progress=lambda f, m: bar.progress(f, text=m))
    bar.empty()
    st.session_state["batch_open"] = B.save_batch(df, {"files": [f[0] for f in files], "modes": modes, "versions": versions, "reps": int(reps)})

batches = B.list_batches()
if not batches:
    st.stop()
names = [b["name"] for b in batches]
cur = st.session_state.get("batch_open") if st.session_state.get("batch_open") in names else names[0]
section("Results")
sel = st.selectbox("Batch", names, index=names.index(cur), format_func=lambda n: next(f"{b['name']} · {b['summary'].get('runs')} runs" for b in batches if b["name"] == n))
df = B.load_batch(sel)
sm = next(b for b in batches if b["name"] == sel)["summary"]
stat_tiles([("Runs", sm["runs"]), ("Completed", f"{sm['completed_pct']:.0f}%"), ("Average run", f"{sm['avg_wall_ms'] / 1000:.1f} s", f"slowest 5% {sm['p95_wall_ms'] / 1000:.1f} s"),
            ("Per valid record", f"{sm['avg_ms_per_valid']:.0f} ms"), ("Same output on repeat", f"{sm['consistent_pct']:.0f}%"), ("Failed MCP calls", sm["failed_calls"])])
c1, c2 = st.columns(2)
with c1:
    g = df.groupby(["file", "mode"], as_index=False)["wall_ms"].mean()
    g["seconds"] = g["wall_ms"] / 1000
    st.altair_chart(grouped_bar(g, "file", "mode", "seconds", "Run time by execution mode", fmt=".2f", y_title="seconds"), width="stretch")
with c2:
    g2 = df.groupby(["file", "mode"], as_index=False)["tool_calls"].mean()
    st.altair_chart(grouped_bar(g2, "file", "mode", "tool_calls", "MCP calls per run", fmt=".0f", y_title="calls"), width="stretch")
cols = ["file", "mode", "rule_version", "run", "status", "records", "valid", "quarantined", "duplicates", "scored", "drafts", "sent", "failed_delivery", "not_sent", "tool_calls",
        "failed_calls", "wall_ms", "ms_per_valid", "output_hash"]
st.dataframe(df[cols], hide_index=True, width="stretch")
st.download_button("Download results (CSV)", df.to_csv(index=False), file_name=f"{sel}.csv")
