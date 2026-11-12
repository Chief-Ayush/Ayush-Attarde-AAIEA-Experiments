"""Settings: people and roles, workflow behaviour, SLA rules and the knowledge base."""
import json

import pandas as pd
import streamlit as st

from ui.common import badge, call, esc, html_table, need, page_header, page_setup, section, sidebar

page_setup("Settings")
uid, user = sidebar()
from supportdesk.config import PERMISSIONS, load_policy, load_settings, load_users, save_settings  # noqa: E402

page_header("Settings")
section("People and roles", "Roles are checked by the servers on every call, not only in this page.")
users = load_users()
st.markdown(html_table(["User", "Name", "Role", "Team", "Allowed"], [[f"<code>{esc(k)}</code>", esc(v["name"]), badge(v["role"], "info"), esc(v.get("team") or "all"), esc(", ".join(sorted(PERMISSIONS[v["role"]])))] for k, v in users.items()]), unsafe_allow_html=True)
section("Workflow")
s = load_settings()
can = "settings" in user["perms"]
with st.form("wf"):
    c1, c2, c3 = st.columns(3)
    cl = c1.selectbox("Classifier", ["learned", "rules"], index=["learned", "rules"].index(s.classifier))
    rt = c2.selectbox("Search", ["semantic", "keyword"], index=["semantic", "keyword"].index(s.retrieval))
    mode = c3.selectbox("Execution", ["parallel", "batch", "sequential"], index=["parallel", "batch", "sequential"].index(s.execution_mode))
    c1, c2, c3 = st.columns(3)
    guard = c1.toggle("Check evidence before automatic answers", value=s.auto_guard)
    auto = c2.toggle("Send safe answers automatically", value=s.auto_dispatch)
    aging = c3.toggle("Raise priority of waiting tickets", value=s.aging)
    c1, c2, c3 = st.columns(3)
    sched = c1.selectbox("Next ticket", ["priority", "fifo"], index=["priority", "fifo"].index(s.scheduling))
    attempts = c2.number_input("Delivery attempts", 1, 6, s.notification_attempts)
    retry = c3.selectbox("Tool retry policy", ["controlled", "naive", "none"], index=["controlled", "naive", "none"].index(s.retry_policy))
    if st.form_submit_button("Save", type="primary", disabled=not can):
        save_settings(s.with_(classifier=cl, retrieval=rt, execution_mode=mode, auto_guard=guard, auto_dispatch=auto, aging=aging, scheduling=sched, notification_attempts=int(attempts), retry_policy=retry))
        st.toast("Saved")
if not can:
    st.caption("Only an admin can change settings.")
section("Service levels", "From config.json. Time to first reply and to resolution, in minutes.")
pol = load_policy()
st.markdown(html_table(["Priority", "First reply", "Resolution"], [[p, pol["sla_minutes"][p], pol["resolution_minutes"][p]] for p in ("P1", "P2", "P3", "P4")], num_cols=(1, 2)), unsafe_allow_html=True)
section("Knowledge base")
kb = need(call("list_articles", include_unapproved=True))
st.dataframe(pd.DataFrame(kb["articles"]).assign(keywords=lambda d: d["keywords"].map(lambda k: ", ".join(k) if isinstance(k, list) else k)), hide_index=True, width="stretch", height=300)
st.caption("Articles that are not approved are never used to answer a customer.")
