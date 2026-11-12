"""MCP inspector: sessions, tools/list, schemas, resources and a console for calling tools."""
import json

import pandas as pd
import streamlit as st

from ui.common import badge, esc, html_table, hub, page_header, page_setup, section, sidebar, summary_strip

page_setup("MCP inspector")
uid, user = sidebar()
from supportdesk.config import SERVERS, Settings  # noqa: E402
from supportdesk.tracing import Trace  # noqa: E402

EXAMPLE = {"inspect_source": {"path": "customer_queries.csv"}, "load_source": {"path": "customer_queries.csv", "limit": 2},
           "validate_queries": {"records": [{"query_id": "X1", "customer_id": "C1", "query_text": "How do I reset my password?", "created_at": "2026-10-22T09:00:00"}]},
           "classify_queries": {"queries": [{"query_id": "X1", "query_text": "I was charged twice this month"}], "method": "learned"},
           "search_knowledge": {"items": [{"query_id": "X1", "query_text": "How do I reset my password?"}], "method": "semantic"},
           "list_articles": {}, "list_tickets": {"active_only": True, "limit": 5}, "queue_summary": {}, "verify_audit": {}, "list_audit": {"limit": 5}, "notification_stats": {}}
READ_TOOLS = None

h = hub()
page_header("MCP inspector", "Servers, tools, schemas and live calls.")

section("Servers")
rows = []
for s in h.server_status():
    c = h.conns[s["server"]]
    rows.append([f"<b>{esc(s['server'])}</b>", esc(s.get("server_name", "-")), esc(s.get("server_protocol", "-")), esc(", ".join(c.capabilities) or "-"), s["tools"], s["connects"],
                 f"{s['connect_ms']:.0f}", badge("alive" if s["alive"] else "down")])
st.markdown(html_table(["Server", "Name", "Protocol", "Capabilities", "Tools", "Connects", "Start (ms)", "Session"], rows, num_cols=(4, 5, 6)), unsafe_allow_html=True)
c1, c2, _ = st.columns([1.2, 1, 3])
which = c1.selectbox("Server", list(SERVERS), label_visibility="collapsed")
if c2.button("Restart server"):
    h.run(h._reconnect(which, force=True))
    st.rerun()
summary_strip([("Calls this session", h.stats["calls"]), ("Reconnects", h.stats["reconnects"]), ("Fresh connections", h.stats["fresh_connections"])])

section("Tools")
tools = h.tools()
df = pd.DataFrame([{"tool": t.name, "server": t.server, "risk": t.risk, "read only": t.annotations.get("readOnlyHint"), "destructive": t.annotations.get("destructiveHint"),
                    "inputs": ", ".join(t.input_schema.get("properties", {})), "required": ", ".join(t.input_schema.get("required", [])), "typed output": bool(t.output_schema)}
                   for t in tools.values()])
st.dataframe(df.sort_values(["server", "tool"]), hide_index=True, width="stretch")

section("Call a tool", "Read-only tools are safe to try; write tools need a valid user id as 'actor'.")
c1, c2 = st.columns([1, 1.4])
with c1:
    names = sorted(tools)
    tool = st.selectbox("Tool", names, index=names.index("list_articles"))
    info = tools[tool]
    st.markdown(f"{badge(info.risk)} <span class='cq-muted'>{esc(info.server)} server</span><br>{esc(info.description)}", unsafe_allow_html=True)
    args_txt = st.text_area("Arguments (JSON)", json.dumps(EXAMPLE.get(tool, {}), indent=1), height=150, key=f"args_{tool}")
    validate = st.toggle("Validate arguments before sending", value=True)
    go = st.button("Send", type="primary")
with c2:
    t1, t2 = st.tabs(["Input schema", "Output schema"])
    t1.json(info.input_schema, expanded=2)
    t2.json(info.output_schema or {}, expanded=1)
if go:
    try:
        args = json.loads(args_txt or "{}")
    except json.JSONDecodeError as exc:
        st.error(f"Invalid JSON: {exc}")
        st.stop()
    tr = Trace()
    r = h.call(tool, args, settings=Settings(validate_arguments=validate), trace=tr, agent="Inspector")
    summary_strip([("Result", badge("success" if r.ok else "failed")), ("Category", esc(r.category or "-")), ("Attempts", r.attempts), ("Time", f"{r.duration_ms:.1f} ms"),
                   ("Output schema", badge("valid") if r.output_valid else ("-" if r.output_valid is None else badge("schema mismatch"))), ("Task", esc(f"{tr.trace_id} {r.task_id}"))])
    if r.ok:
        st.json(r.data, expanded=2)
    else:
        st.error(r.error)

section("Resources")
res = h.resources()
st.markdown(html_table(["Server", "URI", "Description"], [[esc(x["server"]), f"<code>{esc(x['uri'])}</code>", esc(x["description"] or "")] for x in res]), unsafe_allow_html=True)
if res:
    uri = st.selectbox("Resource", [x["uri"] for x in res])
    if st.button("Read resource"):
        srv = next(x["server"] for x in res if x["uri"] == uri)
        st.code(h.read_resource(srv, uri), language="json")
