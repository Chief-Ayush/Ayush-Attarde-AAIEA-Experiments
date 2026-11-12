"""Shared UI layer: design tokens, layout components and charts.  No business logic lives here.

Design rules used across every page
  * one accent colour (blue) for interactive elements and single-series charts; ink/grey for text
  * status colours (good / warning / critical) only for status, always paired with a text label
  * categorical chart colours assigned in a fixed order, at most 5 series per chart
  * every page = page header -> sections (title + one-line caption) -> content; tables before decoration
"""
from __future__ import annotations

import html
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}
INK, MUTED, LINE = "#1c1f24", "#5d636e", "#e2e4e8"

TONE = {"APPROVE": "good", "PASS": "good", "success": "good", "completed": "good", "passed": "good", "valid": "good", "allow": "good", "LOW": "good", "approve": "good", "reject": "bad",
        "created": "good", "alive": "good",
        "COMMENT": "info", "info": "plain", "low": "info", "deferred": "info", "running": "info",
        "REQUEST_CHANGES": "warn", "medium": "warn", "MEDIUM": "warn", "awaiting_human": "warn", "skipped": "plain", "needs_approval": "warn", "retry": "warn",
        "completed_with_errors": "warn", "no_tests": "warn", "created_tests_still_failing": "warn", "rolled_back": "warn", "not_understood": "warn",
        "BLOCK": "bad", "FAIL": "bad", "failed": "bad", "error": "bad", "high": "bad", "critical": "bad", "HIGH": "bad", "deny": "bad", "denied": "bad",
        "rejected": "bad", "rejected_by_reviewer": "bad", "schema mismatch": "bad", "down": "bad"}
VERDICT_TONE = {"APPROVE": "good", "COMMENT": "info", "REQUEST_CHANGES": "warn", "BLOCK": "bad"}
SEV_ORDER = ["critical", "high", "medium", "low", "info"]


CSS = """
<style>
:root { --ink:#1c1f24; --muted:#5d636e; --faint:#8a909a; --line:#e2e4e8; --panel:#f7f8fa; --accent:#2a78d6;
        --good:#0b7a0b; --good-bg:#eaf6ea; --warn:#8a5a00; --warn-bg:#fdf4e1; --bad:#b42f2f; --bad-bg:#fbeaea; --info:#1f62b5; --info-bg:#eaf2fc; }
.block-container {padding-top: 3.2rem; padding-bottom: 3rem; max-width: 1240px;}
/* page header */
.cq-head {border-bottom: 1px solid var(--line); padding-bottom: .9rem; margin-bottom: 1.2rem;}
.cq-head .eyebrow {font-size: .72rem; letter-spacing: .08em; text-transform: uppercase; color: var(--faint); font-weight: 650; margin-bottom: .15rem;}
.cq-head h1 {font-size: 1.75rem; font-weight: 700; margin: 0; padding: 0; color: var(--ink);}
.cq-head p {color: var(--muted); margin: .35rem 0 0 0; font-size: .97rem; max-width: 920px;}
/* section */
.cq-sec {margin: 1.5rem 0 .55rem 0;}
.cq-sec h3 {font-size: 1.08rem; font-weight: 650; margin: 0; padding: 0; color: var(--ink);}
.cq-sec p {color: var(--muted); font-size: .88rem; margin: .2rem 0 0 0;}
/* stat tiles */
.cq-stats {display: grid; gap: 0; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; margin: .3rem 0 .5rem 0;}
.cq-stat {padding: .75rem 1rem; border-right: 1px solid var(--line); background: #fff;}
.cq-stat:last-child {border-right: none;}
.cq-stat .l {font-size: .78rem; color: var(--muted); font-weight: 600;}
.cq-stat .v {font-size: 1.45rem; font-weight: 650; color: var(--ink); line-height: 1.25; margin-top: .15rem; font-variant-numeric: tabular-nums;}
.cq-stat .n {font-size: .76rem; color: var(--faint); margin-top: .1rem;}
/* badges */
.cq-badge {display: inline-flex; align-items: center; gap: 6px; padding: 1px 9px; border-radius: 4px; font-size: .78rem; font-weight: 600;
           border: 1px solid var(--line); color: var(--muted); background: #fff; white-space: nowrap; line-height: 1.55;}
.cq-badge::before {content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--faint);}
.cq-badge.good {color: var(--good); background: var(--good-bg); border-color: #cfe8cf;} .cq-badge.good::before {background: #0ca30c;}
.cq-badge.warn {color: var(--warn); background: var(--warn-bg); border-color: #f1dfb4;} .cq-badge.warn::before {background: #eda100;}
.cq-badge.bad {color: var(--bad); background: var(--bad-bg); border-color: #f0cccc;} .cq-badge.bad::before {background: #d03b3b;}
.cq-badge.info {color: var(--info); background: var(--info-bg); border-color: #cddff6;} .cq-badge.info::before {background: #2a78d6;}
.cq-badge.plain::before {display: none;}
/* summary strip */
.cq-strip {display: grid; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; margin: .2rem 0 .8rem 0;}
.cq-strip > div {padding: .5rem .75rem; border-right: 1px solid var(--line); min-width: 0;}
.cq-strip > div:last-child {border-right: none;}
.cq-strip .l {font-size: .7rem; color: var(--faint); text-transform: uppercase; letter-spacing: .05em; font-weight: 650;}
.cq-strip .v {font-size: .95rem; color: var(--ink); font-weight: 600; margin-top: .15rem; font-variant-numeric: tabular-nums;}
/* panels and simple tables */
.cq-panel {border: 1px solid var(--line); border-radius: 8px; padding: .9rem 1.1rem; background: #fff; margin-bottom: .8rem;}
.cq-panel h4 {font-size: .98rem; font-weight: 650; margin: 0 0 .5rem 0; padding: 0;}
.cq-tbl {border-collapse: collapse; width: 100%; font-size: .88rem;}
.cq-tbl th {text-align: left; font-weight: 600; color: var(--muted); background: var(--panel); border-bottom: 1px solid var(--line); padding: 6px 10px; font-size: .8rem;}
.cq-tbl td {border-bottom: 1px solid var(--line); padding: 6px 10px; vertical-align: top; color: var(--ink);}
.cq-tbl tr:last-child td {border-bottom: none;}
.cq-tbl td.num {text-align: right; font-variant-numeric: tabular-nums;}
.cq-muted {color: var(--muted); font-size: .86rem;}
.cq-tbl code {white-space: nowrap; font-size: .78rem; background: var(--panel); border: 1px solid var(--line); border-radius: 3px; padding: 0 4px; color: #3b4048;}
/* agent spec */
.cq-spec {border: 1px solid var(--line); border-radius: 8px; margin-bottom: 1rem; background: #fff; overflow: hidden;}
.cq-spec .hd {display: flex; align-items: center; gap: 10px; padding: .65rem 1.1rem; border-bottom: 1px solid var(--line); background: var(--panel);}
.cq-spec .hd .idx {font-variant-numeric: tabular-nums; color: var(--faint); font-weight: 650; font-size: .85rem; min-width: 22px;}
.cq-spec .hd .name {font-weight: 700; font-size: 1.02rem; color: var(--ink);}
.cq-spec .hd .meta {margin-left: auto; display: flex; gap: 6px; flex-wrap: wrap;}
.cq-spec .bd {display: grid; grid-template-columns: 1.3fr 1fr; gap: 0;}
.cq-spec .bd > div {padding: .8rem 1.1rem;}
.cq-spec .bd > div:first-child {border-right: 1px solid var(--line);}
.cq-spec .purpose {color: var(--ink); margin: 0 0 .55rem 0; font-size: .93rem;}
.cq-spec .lbl {font-size: .72rem; text-transform: uppercase; letter-spacing: .06em; color: var(--faint); font-weight: 650; margin: 0 0 .3rem 0;}
.cq-spec ul {margin: 0; padding-left: 1.1rem;} .cq-spec li {margin-bottom: .28rem; font-size: .87rem; color: var(--ink);}
.cq-spec dl {margin: 0; display: grid; grid-template-columns: 104px 1fr; row-gap: .5rem; column-gap: .6rem; font-size: .85rem;}
.cq-spec dt {color: var(--muted); font-weight: 600;} .cq-spec dd {margin: 0; color: var(--ink);}
.cq-spec code {font-size: .76rem; background: var(--panel); border: 1px solid var(--line); border-radius: 3px; padding: 0 4px; margin: 0 3px 3px 0; display: inline-block; color: #3b4048;}
.cq-spec .ft {border-top: 1px solid var(--line); padding: .55rem 1.1rem; font-size: .84rem; color: var(--muted); background: #fcfcfd;}
.cq-spec .ft b {color: var(--ink); font-weight: 600;}
/* execution flow */
.cq-flow {display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin: .3rem 0 .6rem 0;}
.cq-step {border: 1px solid var(--line); border-left: 3px solid #0ca30c; border-radius: 4px; padding: 4px 9px; font-size: .76rem; background: #fff; min-width: 92px;}
.cq-step b {display: block; font-size: .8rem; color: var(--ink); font-weight: 650;} .cq-step span {color: var(--muted);}
.cq-step.retry {border-left-color: #eda100;} .cq-step.failed {border-left-color: #d03b3b;}
.cq-par {display: flex; flex-direction: column; gap: 4px; padding: 4px; border: 1px dashed #b9c6d8; border-radius: 6px;}
.cq-arrow {color: var(--faint);}
/* sidebar */
.cq-side {font-size: .8rem; color: var(--muted); line-height: 1.55; border-top: 1px solid var(--line); padding-top: .7rem; margin-top: .6rem;}
.cq-side b {color: var(--ink); font-weight: 600;}
/* diff view */
.rp-diff {border: 1px solid var(--line); border-radius: 8px; overflow: hidden; margin-bottom: .8rem; background: #fff;}
.rp-diff .fh {display: flex; gap: 8px; align-items: center; padding: .45rem .8rem; background: var(--panel); border-bottom: 1px solid var(--line); font-size: .86rem;}
.rp-diff .fh b {font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .82rem; color: var(--ink);}
.rp-diff .fh .st {margin-left: auto; color: var(--muted); font-variant-numeric: tabular-nums;}
.rp-diff pre {margin: 0; padding: .35rem 0; font-size: .78rem; line-height: 1.45; overflow-x: auto; background: #fff;}
.rp-diff .ln {display: block; padding: 0 .8rem; white-space: pre; color: #3b4048; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;}
.rp-diff .ln.add {background: #eaf6ea; color: #0b5a0b;} .rp-diff .ln.del {background: #fbeaea; color: #8f2424;}
.rp-diff .ln.hunk {color: #1f62b5; background: #f2f6fc;} .rp-diff .ln.flag {box-shadow: inset 3px 0 0 #d03b3b;}
.rp-code {border: 1px solid var(--line); border-radius: 8px; background: #fcfcfd; padding: .5rem 0; font-size: .78rem; overflow-x: auto; max-height: 520px;}
.rp-code .ln {display: block; padding: 0 .8rem; white-space: pre; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color: #2b2f36;}
.rp-code .no {display: inline-block; width: 34px; color: var(--faint); text-align: right; margin-right: 12px; user-select: none;}
/* verdict banner */
.rp-verdict {display: grid; grid-template-columns: auto 1fr auto; gap: 1rem; align-items: center; border: 1px solid var(--line); border-left: 4px solid var(--faint);
             border-radius: 8px; padding: .8rem 1.1rem; margin: .4rem 0 .8rem 0; background: #fff;}
.rp-verdict.good {border-left-color: #0ca30c;} .rp-verdict.warn {border-left-color: #eda100;} .rp-verdict.bad {border-left-color: #d03b3b;} .rp-verdict.info {border-left-color: #2a78d6;}
.rp-verdict .v {font-size: 1.25rem; font-weight: 700; color: var(--ink); letter-spacing: .01em;}
.rp-verdict .s {color: var(--muted); font-size: .92rem;}
.rp-verdict .sc {text-align: right; font-variant-numeric: tabular-nums;} .rp-verdict .sc b {font-size: 1.35rem; color: var(--ink);} .rp-verdict .sc span {display: block; font-size: .75rem; color: var(--faint);}
/* phase lanes */
.rp-phases {display: grid; gap: 8px; margin: .3rem 0 .8rem 0;}
.rp-phase {display: grid; grid-template-columns: 92px 1fr; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; background: #fff;}
.rp-phase .ph {background: var(--panel); border-right: 1px solid var(--line); padding: .5rem .7rem; font-size: .8rem; color: var(--muted);}
.rp-phase .ph b {display: block; color: var(--ink); font-size: .86rem;}
.rp-phase .calls {display: flex; flex-wrap: wrap; gap: 6px; padding: .5rem .6rem;}
.rp-call {border: 1px solid var(--line); border-left: 3px solid #0ca30c; border-radius: 4px; padding: 3px 9px; font-size: .76rem; background: #fff;}
.rp-call b {display: block; font-size: .8rem; color: var(--ink); font-weight: 650;} .rp-call span {color: var(--muted);}
.rp-call.failed {border-left-color: #d03b3b;} .rp-call.skipped, .rp-call.denied {border-left-color: #8a909a; opacity: .8;} .rp-call.retry {border-left-color: #eda100;}
</style>
"""


@alt.theme.register("supportdesk", enable=True)
def _theme():
    return alt.theme.ThemeConfig({"config": {
        "background": "#ffffff", "font": "Source Sans Pro, sans-serif", "view": {"stroke": None},
        "axis": {"labelColor": MUTED, "titleColor": MUTED, "titleFontWeight": 600, "labelFontSize": 11, "titleFontSize": 11, "gridColor": "#eef0f2",
                 "domainColor": LINE, "tickColor": LINE},
        "legend": {"labelColor": MUTED, "titleColor": MUTED, "labelFontSize": 11, "titleFontSize": 11, "orient": "bottom", "symbolType": "square"},
        "title": {"color": INK, "fontSize": 13, "fontWeight": 600, "anchor": "start", "offset": 10},
        "range": {"category": SERIES}}})


# ---------------------------------------------------------------------------------------------------- setup
def page_setup(title: str = "SupportDesk") -> None:
    try:
        st.set_page_config(page_title=f"{title} · SupportDesk", layout="wide")
    except Exception:  # noqa: BLE001
        pass
    st.markdown(CSS, unsafe_allow_html=True)


@st.cache_resource(show_spinner="Starting the MCP servers ...")
def hub():
    from supportdesk.mcp import get_hub
    return get_hub()












# ---------------------------------------------------------------------------------------------------- components
def esc(x) -> str:
    return html.escape(str(x))


def page_header(title: str, description: str = "", eyebrow: str = "") -> None:
    eb = f"<div class='eyebrow'>{esc(eyebrow)}</div>" if eyebrow else ""
    st.markdown(f"<div class='cq-head'>{eb}<h1>{esc(title)}</h1>{f'<p>{esc(description)}</p>' if description else ''}</div>", unsafe_allow_html=True)


def section(title: str, caption: str = "") -> None:
    st.markdown(f"<div class='cq-sec'><h3>{esc(title)}</h3>{f'<p>{esc(caption)}</p>' if caption else ''}</div>", unsafe_allow_html=True)


def badge(text, tone: str | None = None) -> str:
    t = tone or TONE.get(str(text), "")
    return f"<span class='cq-badge {t}'>{esc(str(text).replace('_', ' '))}</span>"


def stat_tiles(items: list[tuple], cols: int | None = None) -> None:
    n = cols or len(items)
    cells = "".join(f"<div class='cq-stat'><div class='l'>{esc(l)}</div><div class='v'>{esc(v)}</div>"
                    + (f"<div class='n'>{esc(rest[0])}</div>" if rest and rest[0] else "") + "</div>" for l, v, *rest in items)
    st.markdown(f"<div class='cq-stats' style='grid-template-columns: repeat({n}, minmax(0,1fr));'>{cells}</div>", unsafe_allow_html=True)


def summary_strip(pairs: list[tuple], template: str | None = None) -> None:
    cells = "".join(f"<div><div class='l'>{esc(l)}</div><div class='v'>{v}</div></div>" for l, v in pairs)
    cols = template or f"repeat({len(pairs)}, minmax(0,1fr))"
    st.markdown(f"<div class='cq-strip' style='grid-template-columns: {cols};'>{cells}</div>", unsafe_allow_html=True)


def html_table(headers: list[str], rows: list[list], num_cols: tuple = ()) -> str:
    th = "".join(f"<th>{esc(h)}</th>" for h in headers)
    trs = "".join("<tr>" + "".join(f"<td class='{'num' if i in num_cols else ''}'>{c}</td>" for i, c in enumerate(r)) + "</tr>" for r in rows)
    head = f"<tr>{th}</tr>" if any(headers) else ""
    return f"<table class='cq-tbl'>{head}{trs}</table>"














def trace_frame(trace: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{"Trace ID": t["trace_id"], "Task ID": t["task_id"], "Agent": t.get("agent") or "-", "Phase": t.get("phase"),
                          "Tool": t["tool"], "Server": t["server"], "Risk": t.get("risk") or "-", "Arguments": str(t.get("arguments"))[:90],
                          "Status": t["status"], "Attempt": t["attempt"], "Execution time (ms)": t["execution_time_ms"],
                          "Timestamp": t["timestamp"], "Error": (str(t["error"])[:90] if isinstance(t.get("error"), str) and t.get("error") else "-")} for t in trace])






def bar_chart(df: pd.DataFrame, x: str, y: str, title: str = "", horizontal: bool = True, fmt: str = ".1f", color: str = SERIES[0], domain=None,
              height=None, x_title: str | None = None, y_title: str | None = None):
    scale = alt.Scale(domain=list(domain)) if domain else alt.Undefined
    if horizontal:
        enc = dict(y=alt.Y(f"{x}:N", sort="-x", title=x_title, axis=alt.Axis(labelLimit=280)), x=alt.X(f"{y}:Q", title=y_title or y.replace("_", " "), scale=scale))
    else:
        enc = dict(x=alt.X(f"{x}:N", sort=None, title=x_title or x.replace("_", " "), axis=alt.Axis(labelAngle=0)),
                   y=alt.Y(f"{y}:Q", title=y_title or y.replace("_", " "), scale=scale))
    base = alt.Chart(df).encode(**enc)
    bars = base.mark_bar(color=color, cornerRadiusEnd=4, size=14 if horizontal else 40).encode(tooltip=[alt.Tooltip(f"{x}:N"), alt.Tooltip(f"{y}:Q", format=fmt)])
    text = base.mark_text(align="left" if horizontal else "center", dx=4 if horizontal else 0, dy=0 if horizontal else -7, color=INK, fontSize=11).encode(
        text=alt.Text(f"{y}:Q", format=fmt))
    ch = bars + text
    if height:
        ch = ch.properties(height=height)
    return ch.properties(title=title) if title else ch


def grouped_bar(df: pd.DataFrame, x: str, series: str, y: str, title: str = "", fmt=".1f", y_title: str | None = None):
    cats = list(dict.fromkeys(df[series]))
    return (alt.Chart(df).mark_bar(cornerRadiusEnd=3, stroke="#fff", strokeWidth=1)
            .encode(y=alt.Y(f"{x}:N", title=None, sort=None, axis=alt.Axis(labelLimit=280)), yOffset=alt.YOffset(f"{series}:N", sort=cats),
                    x=alt.X(f"{y}:Q", title=y_title or y.replace("_", " ")),
                    color=alt.Color(f"{series}:N", scale=alt.Scale(domain=cats, range=SERIES[:len(cats)]), legend=alt.Legend(title=None)),
                    tooltip=[f"{x}:N", f"{series}:N", alt.Tooltip(f"{y}:Q", format=fmt)])
            .properties(title=title, height=alt.Step(13)))


def status_bar(counts: dict, title: str = "", cmap: dict | None = None):
    df = pd.DataFrame({"result": [str(k) for k in counts], "count": list(counts.values())})
    cm = {"PASS": STATUS["good"], "APPROVE": STATUS["good"], "COMMENT": SERIES[0], "REQUEST_CHANGES": STATUS["warning"], "BLOCK": STATUS["critical"],
          "FAIL": STATUS["critical"], "rejected": "#8a909a"} | (cmap or {})
    dom = list(df["result"])
    base = alt.Chart(df).encode(y=alt.Y("result:N", title=None, sort=dom, axis=alt.Axis(labelLimit=260)),
                                x=alt.X("count:Q", title="count", axis=alt.Axis(tickMinStep=1, format="d")))
    bars = base.mark_bar(cornerRadiusEnd=4, size=18).encode(color=alt.Color("result:N", scale=alt.Scale(domain=dom, range=[cm.get(d, "#8a909a") for d in dom]),
                                                                            legend=None), tooltip=["result:N", "count:Q"])
    text = base.mark_text(align="left", dx=4, color=INK, fontSize=11).encode(text="count:Q")
    return (bars + text).properties(title=title, height=alt.Step(28))


def confusion_chart(conf: dict, classes: list[str], title: str = ""):
    """conf[expected][predicted] = n"""
    rows = [{"expected": e, "predicted": p, "n": conf.get(e, {}).get(p, 0)} for e in classes for p in classes]
    df = pd.DataFrame(rows)
    base = alt.Chart(df).encode(x=alt.X("predicted:N", sort=classes, title="predicted", axis=alt.Axis(labelAngle=-30, labelLimit=140, labelFontSize=10)),
                                y=alt.Y("expected:N", sort=classes, title="labelled", axis=alt.Axis(labelLimit=140)))
    heat = base.mark_rect(stroke="#fff", strokeWidth=2).encode(color=alt.Color("n:Q", scale=alt.Scale(range=["#f4f7fb", "#2a78d6"]), legend=None), tooltip=["expected:N", "predicted:N", "n:Q"])
    mx = max(1, df.n.max())
    text = base.mark_text(fontSize=12, fontWeight=600).encode(text="n:Q", color=alt.condition(f"datum.n > {mx * 0.5}", alt.value("#ffffff"), alt.value(INK)))
    return (heat + text).properties(height=250, title=title)


# ---------------------------------------------------------------------------------------------------- SupportDesk specifics
TONE.update({"P1": "bad", "P2": "warn", "P3": "info", "P4": "plain",
             "WITHIN_SLA": "good", "AT_RISK": "warn", "BREACHED": "bad", "PAUSED": "plain",
             "OPEN": "info", "PENDING_APPROVAL": "warn", "IN_REVIEW": "warn", "APPROVED_FOR_RESPONSE": "info", "ESCALATED": "bad", "ON_HOLD": "plain",
             "AWAITING_CUSTOMER": "info", "RESOLVED": "good", "CLOSED": "plain", "REOPENED": "warn",
             "SENT": "good", "DEAD_LETTER": "bad", "DRAFT": "info", "SENDING": "info", "PENDING": "warn", "FAILED": "bad",
             "agent": "info", "lead": "good", "admin": "good", "viewer": "plain"})
STATUS_LABEL = {"WITHIN_SLA": "Within SLA", "AT_RISK": "At risk", "BREACHED": "Breached", "PAUSED": "Paused", "AWAITING_CUSTOMER": "Waiting for customer",
                "APPROVED_FOR_RESPONSE": "Approved", "SLA_AT_RISK": "SLA at risk", "SLA_BREACH": "SLA breach", "PENDING_APPROVAL": "Pending approval", "IN_REVIEW": "In review", "DEAD_LETTER": "Dead letter"}
ROUTE_LABEL = {"URGENT_HUMAN_ESCALATION": "Urgent escalation", "TECHNICAL_HUMAN_REVIEW": "Technical review", "CREATE_TICKET_AND_ESCALATE": "Escalate",
               "PROPOSE_AUTO_RESOLUTION": "Automatic answer", "HUMAN_REVIEW": "Agent review", "PRODUCT_FEEDBACK_QUEUE": "Product feedback",
               "REQUEST_INFORMATION_OR_ESCALATE": "Needs more detail"}
EVAL_PREFIXES = ("EVAL", "TC", "F0", "F1", "A0", "A1", "S0", "S1", "R0", "R1", "ACC", "HARD", "ABL", "PERF", "CONSOLE")


def label(x) -> str:
    return STATUS_LABEL.get(x) or ROUTE_LABEL.get(x) or str(x).replace("_", " ").capitalize()


def status_badge(x) -> str:
    return badge(label(x), TONE.get(str(x), "plain")) if x else "-"


def call(tool: str, **args):
    """Call an MCP tool through the shared hub with the saved settings."""
    from supportdesk.config import load_settings
    return hub().call(tool, args, settings=load_settings(), agent="dashboard")


def need(r):
    """Return r.data, or show the error and stop the page."""
    if not r.ok:
        st.error(str(r.error).replace("Error executing tool ", ""))
        st.stop()
    return r.data


def fmt_min(m) -> str:
    m = float(m or 0)
    if m < 60:
        return f"{m:.0f} min"
    if m < 1440:
        return f"{m / 60:.1f} h"
    return f"{m / 1440:.1f} d"


def sidebar():
    """Signed-in user picker and the service clock.  Returns (user_id, user)."""
    from supportdesk.config import PERMISSIONS, load_users
    users = load_users()
    ids = list(users)
    cur = st.session_state.get("user", "riya.agent" if "riya.agent" in ids else ids[0])
    uid = st.sidebar.selectbox("Signed in as", ids, index=ids.index(cur) if cur in ids else 0, key="user_picker",
                               format_func=lambda i: f"{users[i]['name']} ({users[i]['role']})")
    st.session_state["user"] = uid
    user = {**users[uid], "id": uid, "perms": PERMISSIONS.get(users[uid]["role"], set())}
    q = call("queue_summary")
    if q.ok:
        d = q.data
        br = sum(v["breached"] for v in d["by_priority"].values())
        ar = sum(v["at_risk"] for v in d["by_priority"].values())
        st.sidebar.markdown(f"<div class='cq-side'>Open tickets <b>{d['active']}</b><br>At risk <b>{ar}</b> · Breached <b>{br}</b><br>"
                            f"Team <b>{esc(user.get('team') or 'all')}</b><br>Service time <b>{esc(d['service_time'][:16].replace('T', ' '))}</b></div>", unsafe_allow_html=True)
    return uid, user


def is_test_prefix(tid: str) -> bool:
    return str(tid).startswith(EVAL_PREFIXES)
