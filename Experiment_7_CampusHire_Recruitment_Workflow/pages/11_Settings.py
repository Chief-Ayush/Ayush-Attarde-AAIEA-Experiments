"""Settings: users and roles, execution defaults, notification and SLA rules, rule version."""
import json

import pandas as pd
import streamlit as st

from ui.common import badge, call, esc, html_table, page_header, page_setup, section, sidebar

page_setup("Settings")
from campushire.config import PATHS, PERMISSIONS, load_settings, save_settings  # noqa: E402

uid, user, did = sidebar(require_drive=False)
page_header("Settings")

section("People and roles", "Roles come from users.json and are checked by the servers on every call.")
from campushire.config import load_users  # noqa: E402
users = load_users()
st.markdown(html_table(["User ID", "Name", "Role", "Allowed"], [[f"<code>{esc(k)}</code>", esc(v["name"]), badge(v["role"].replace("_", " "), "info"),
                                                                esc(", ".join(sorted(PERMISSIONS[v["role"]])))] for k, v in users.items()]), unsafe_allow_html=True)

section("Workflow defaults")
s = load_settings()
with st.form("defaults"):
    c1, c2, c3 = st.columns(3)
    mode = c1.selectbox("Execution", ["parallel", "batch", "sequential"], index=["parallel", "batch", "sequential"].index(s.execution_mode))
    chunk = c2.number_input("Records per MCP call", 1, 100, s.chunk_size)
    par = c3.number_input("Parallel calls", 1, 16, s.max_parallel)
    c1, c2, c3 = st.columns(3)
    retry = c1.selectbox("Retry policy", ["controlled", "naive", "none"], index=["controlled", "naive", "none"].index(s.retry_policy))
    nret = c2.number_input("Message retries after a delivery failure", 0, 5, s.notification_retries)
    sla = c3.number_input("SLA for a reviewer decision (hours)", 1, 500, s.sla_hours)
    if st.form_submit_button("Save", type="primary"):
        save_settings(s.with_(execution_mode=mode, chunk_size=int(chunk), max_parallel=int(par), retry_policy=retry, notification_retries=int(nret), sla_hours=int(sla)))
        st.toast("Saved")

section("Scoring rules", "Only job-related criteria are accepted. Candidate text is never read by the scoring step.")
rules = call("get_rules")
if rules.ok:
    R = rules.data
    st.markdown(f"Active version **{esc(R['active_version'])}** · versions {', '.join(R['versions'])}")
    ver = st.selectbox("Show version", R["versions"], index=R["versions"].index(R["active_version"]))
    rv = call("get_rules", version=ver).data["rules"]
    rows = [[esc(role), esc(", ".join(f"{k.replace('_score', '').replace('_', ' ')} {w:g}%" for k, w in b["criteria"].items())),
             f"{b['thresholds']['shortlist_review']:g} / {b['thresholds']['manual_review']:g}"] for role, b in rv["roles"].items()]
    st.markdown(html_table(["Role", "Weights", "Shortlist / manual review from"], rows), unsafe_allow_html=True)
    st.caption(rv.get("description", ""))
    if st.button(f"Make version {ver} the active version", disabled="rules" not in user["perms"] or ver == R["active_version"]):
        r = call("set_active_version", version=ver, actor=uid)
        st.toast("Active version changed" if r.ok else r.error)
        if r.ok:
            st.rerun()
    if "rules" not in user["perms"]:
        st.caption("Only an admin can change the active version.")
else:
    st.error(rules.error)
