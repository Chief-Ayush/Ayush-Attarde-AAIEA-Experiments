"""Run workflow: create a drive, choose a candidate file and run ingest -> validate -> score -> draft; the workflow then waits for reviewers."""
import time

import pandas as pd
import streamlit as st

from ui.common import call, esc, html_table, hub, need, page_header, page_setup, section, sidebar, stat_tiles, trace_frame

page_setup("Run workflow")
from campushire.config import PATHS, load_settings  # noqa: E402
from campushire.workflow import Workflow, run_drive  # noqa: E402

uid, user, did = sidebar(require_drive=False)
page_header("Run workflow", "Load a candidate file, screen it and prepare notification drafts.")
can_run = "run" in user["perms"]
if not can_run:
    st.warning(f"{user['name']} has a read-only role. Sign in as an HR reviewer or admin to run the workflow.")

with st.expander("New hiring drive", expanded=not did):
    c1, c2 = st.columns([3, 1])
    name = c1.text_input("Drive name", "Campus drive 2026", label_visibility="collapsed")
    if c2.button("Create drive", disabled=not can_run, width="stretch"):
        r = call("create_drive", name=name, actor=uid)
        if r.ok:
            st.session_state["drive"] = r.data["drive_id"]
            st.rerun()
        else:
            st.error(r.error)

if not did:
    st.stop()

section("Candidate file")
SAMPLES = {"Sample drive - 120 records (candidates.csv)": "candidates.csv", "Short sample - 10 records": "data/samples/candidates_10.csv",
           "Second upload - repeats and updated applications (JSON)": "data/samples/candidates_batch2.json"}
c1, c2 = st.columns([1.4, 1])
choice = c1.selectbox("Source", list(SAMPLES) + ["Upload a file"], label_visibility="collapsed")
path = SAMPLES.get(choice)
if choice == "Upload a file":
    up = c1.file_uploader("CSV or JSON", type=["csv", "json"], label_visibility="collapsed")
    if up is not None:
        p = PATHS.uploads / f"upload_{int(time.time())}_{up.name.replace('/', '_')}"
        p.write_bytes(up.getvalue())
        path = p.relative_to(PATHS.root).as_posix()
s0 = load_settings()
rules = need(call("get_rules"))
versions = rules["versions"]
with c2:
    ver = st.selectbox("Rule version", versions, index=versions.index(rules["active_version"]), help="Recorded with every score.")
    mode = st.selectbox("Execution", ["parallel", "batch", "sequential"], index=["parallel", "batch", "sequential"].index(s0.execution_mode))
auto = st.checkbox("Also review automatically with the demo policy (for testing only)", value=False)

if st.button("Run workflow", type="primary", disabled=not (can_run and path)):
    s = s0.with_(execution_mode=mode, rule_version=ver)
    with st.spinner("Running through the MCP servers ..."):
        if auto:
            res = run_drive(path, uid, drive_id=did, reviewer=uid if "review" in user["perms"] else "priya.reviewer", settings=s, hub=hub())
            st.session_state["last_run"] = {"state": res["state"], "trace": res["trace"].records, "ms": res["total_ms"], "paused": False}
        else:
            wf = Workflow(hub(), s)
            t0 = time.perf_counter()
            wf.start(did, path, uid)
            st.session_state["last_run"] = {"state": wf.state, "trace": wf.run.trace.records, "ms": (time.perf_counter() - t0) * 1000, "paused": wf.pending() is not None,
                                            "wf": wf}

lr = st.session_state.get("last_run")
if lr:
    stt = lr["state"]
    k = stt.get("counts") or {}
    section("Result")
    if stt.get("status") == "failed":
        st.error("The workflow stopped: " + "; ".join(stt.get("errors") or ["unknown error"]))
    elif lr.get("paused"):
        st.success("Screening is done. The workflow is paused until reviewers decide.")
        st.page_link("pages/2_Review_Queue.py", label="Open the review queue")
    stat_tiles([("Records", k.get("total", 0)), ("Accepted", k.get("valid", 0)), ("Quarantined", k.get("rejected", 0), f"{k.get('duplicates', 0)} duplicates"),
                ("Already imported", k.get("skipped", 0)), ("Drafts", stt.get("drafts_created", 0)), ("Time", f"{lr['ms'] / 1000:.1f} s", f"{len(lr['trace'])} MCP calls")])
    if stt.get("errors"):
        st.warning("; ".join(stt["errors"][:3]))
    tm = stt.get("timings") or {}
    if tm:
        st.markdown(html_table(["Step", "Time (ms)"], [[esc(s.replace("_", " ").capitalize()), f"{v:,.0f}"] for s, v in tm.items()], num_cols=(1,)), unsafe_allow_html=True)
    with st.expander("MCP calls of this run"):
        st.dataframe(trace_frame(lr["trace"]), hide_index=True, width="stretch")
