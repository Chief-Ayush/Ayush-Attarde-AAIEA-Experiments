import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from engine.risk_score import assess_transaction
from config import RULE_WEIGHTS, RISK_SETTINGS, RISK_THRESHOLDS, REASON_MAP
from app.report_service import generate_batch_report, render_markdown_report

# ──────────────────────────────────────────────
# Page config
# ──────────────────────────────────────────────
st.set_page_config(
    page_title="FraudLens — Transaction Fraud Detection",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ──────────────────────────────────────────────
# Session state init
# ──────────────────────────────────────────────
if "audit_log" not in st.session_state:
    st.session_state.audit_log = []
if "batch_results" not in st.session_state:
    st.session_state.batch_results = None
if "batch_df" not in st.session_state:
    st.session_state.batch_df = None
if "batch_report" not in st.session_state:
    st.session_state.batch_report = None

# Store original defaults for the "Restore Defaults" button
import copy
if "_default_rule_weights" not in st.session_state:
    st.session_state._default_rule_weights = copy.deepcopy(RULE_WEIGHTS)
if "_default_risk_settings" not in st.session_state:
    st.session_state._default_risk_settings = copy.deepcopy(RISK_SETTINGS)
if "_default_risk_thresholds" not in st.session_state:
    st.session_state._default_risk_thresholds = copy.deepcopy(RISK_THRESHOLDS)

# ──────────────────────────────────────────────
# Custom CSS
# ──────────────────────────────────────────────
st.markdown("""
<style>
    /* Import Google Font */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

    * { font-family: 'Inter', sans-serif; }

    /* Hide default Streamlit branding */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}

    /* KPI metric cards */
    .kpi-card {
        background: linear-gradient(135deg, #1A1F2E 0%, #252B3B 100%);
        border: 1px solid rgba(0, 212, 170, 0.15);
        border-radius: 16px;
        padding: 24px;
        text-align: center;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    .kpi-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 25px rgba(0, 212, 170, 0.1);
    }
    .kpi-value {
        font-size: 2.4rem;
        font-weight: 700;
        margin: 8px 0 4px;
        line-height: 1.2;
    }
    .kpi-label {
        font-size: 0.85rem;
        color: #8892A0;
        text-transform: uppercase;
        letter-spacing: 1px;
        font-weight: 500;
    }
    .kpi-green { color: #00D4AA; }
    .kpi-amber { color: #FFB547; }
    .kpi-red { color: #FF4B6E; }
    .kpi-blue { color: #4B9EFF; }

    /* Risk badge */
    .risk-badge {
        display: inline-block;
        padding: 6px 18px;
        border-radius: 20px;
        font-weight: 600;
        font-size: 0.9rem;
        letter-spacing: 0.5px;
    }
    .badge-legitimate { background: rgba(0, 212, 170, 0.15); color: #00D4AA; border: 1px solid rgba(0, 212, 170, 0.3); }
    .badge-suspicious { background: rgba(255, 181, 71, 0.15); color: #FFB547; border: 1px solid rgba(255, 181, 71, 0.3); }
    .badge-high-risk { background: rgba(255, 75, 110, 0.15); color: #FF4B6E; border: 1px solid rgba(255, 75, 110, 0.3); }

    /* Rule indicator cards */
    .rule-card {
        background: #1A1F2E;
        border-radius: 12px;
        padding: 16px;
        text-align: center;
        border: 1px solid #2A3040;
        transition: all 0.2s ease;
    }
    .rule-card.triggered {
        border-color: rgba(255, 75, 110, 0.4);
        background: linear-gradient(135deg, #1A1F2E 0%, #2A1520 100%);
    }
    .rule-card.safe {
        border-color: rgba(0, 212, 170, 0.3);
        background: linear-gradient(135deg, #1A1F2E 0%, #152A25 100%);
    }
    .rule-icon { font-size: 1.5rem; margin-bottom: 6px; }
    .rule-name { font-size: 0.8rem; color: #8892A0; margin-bottom: 4px; }
    .rule-status { font-weight: 600; font-size: 0.85rem; }
    .rule-status.triggered { color: #FF4B6E; }
    .rule-status.safe { color: #00D4AA; }

    /* Insight card */
    .insight-card {
        background: linear-gradient(135deg, #1A1F2E 0%, #1E2535 100%);
        border-left: 3px solid #4B9EFF;
        border-radius: 0 12px 12px 0;
        padding: 16px 20px;
        margin-bottom: 12px;
    }
    .insight-card .insight-title {
        font-size: 0.8rem;
        color: #8892A0;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin-bottom: 4px;
    }
    .insight-card .insight-value {
        font-size: 1.15rem;
        font-weight: 600;
        color: #E0E0E0;
    }

    /* Section header */
    .section-header {
        font-size: 1.1rem;
        font-weight: 600;
        color: #E0E0E0;
        padding-bottom: 8px;
        margin-bottom: 16px;
        border-bottom: 2px solid rgba(0, 212, 170, 0.2);
    }

    /* Recommendation panel */
    .recommendation {
        background: linear-gradient(135deg, #1A1F2E 0%, #252B3B 100%);
        border-radius: 12px;
        padding: 20px;
        border: 1px solid #2A3040;
    }
    .rec-action {
        font-weight: 600;
        font-size: 1.05rem;
        margin-bottom: 6px;
    }
    .rec-detail {
        color: #8892A0;
        font-size: 0.9rem;
    }

    /* Transaction detail row */
    .txn-detail {
        display: flex;
        justify-content: space-between;
        padding: 8px 0;
        border-bottom: 1px solid rgba(42, 48, 64, 0.5);
    }
    .txn-detail-label { color: #8892A0; font-size: 0.85rem; }
    .txn-detail-value { color: #E0E0E0; font-weight: 500; font-size: 0.85rem; }

    /* Sidebar styling */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0E1117 0%, #141822 100%);
    }

    /* Tab styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px;
        padding: 10px 20px;
    }

    /* Onboarding card */
    .onboard-card {
        background: linear-gradient(135deg, #1A1F2E 0%, #1E2535 100%);
        border: 1px dashed rgba(0, 212, 170, 0.3);
        border-radius: 16px;
        padding: 40px;
        text-align: center;
    }
    .onboard-card h3 { color: #E0E0E0; margin-bottom: 8px; }
    .onboard-card p { color: #8892A0; font-size: 0.95rem; }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────
# Plotly chart defaults
# ──────────────────────────────────────────────
PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Inter, sans-serif", color="#E0E0E0"),
    margin=dict(l=40, r=40, t=40, b=40),
    hoverlabel=dict(bgcolor="#1A1F2E", font_color="#E0E0E0", bordercolor="#2A3040"),
)

RISK_COLORS = {
    "LEGITIMATE": "#00D4AA",
    "SUSPICIOUS": "#FFB547",
    "HIGH_RISK": "#FF4B6E",
}


# ──────────────────────────────────────────────
# Helper functions
# ──────────────────────────────────────────────
def _risk_badge(level):
    cls = {
        "LEGITIMATE": "badge-legitimate",
        "SUSPICIOUS": "badge-suspicious",
        "HIGH_RISK": "badge-high-risk",
    }.get(level, "badge-suspicious")
    return f'<span class="risk-badge {cls}">{level}</span>'


def _kpi_card(label, value, color_class="kpi-blue"):
    return f"""
    <div class="kpi-card">
        <div class="kpi-label">{label}</div>
        <div class="kpi-value {color_class}">{value}</div>
    </div>
    """


def _rule_indicator_card(rule_key, triggered):
    icons = {
        "HIGH_AMOUNT": "💰",
        "UNUSUAL_TIME": "🕐",
        "LOCATION_CHANGE": "📍",
        "HIGH_VELOCITY": "⚡",
        "NEW_MERCHANT": "🏪",
        "MULTIPLE_FAILED_ATTEMPTS": "🚫",
    }
    names = {
        "HIGH_AMOUNT": "High Amount",
        "UNUSUAL_TIME": "Unusual Time",
        "LOCATION_CHANGE": "Location Change",
        "HIGH_VELOCITY": "High Velocity",
        "NEW_MERCHANT": "New Merchant",
        "MULTIPLE_FAILED_ATTEMPTS": "Failed Attempts",
    }
    state_cls = "triggered" if triggered else "safe"
    status_text = "⚠️ TRIGGERED" if triggered else "✅ SAFE"
    icon = icons.get(rule_key, "📌")
    name = names.get(rule_key, rule_key)
    weight = RULE_WEIGHTS.get(rule_key, 0)
    weight_str = f"+{weight}" if triggered else "—"

    return f"""
    <div class="rule-card {state_cls}">
        <div class="rule-icon">{icon}</div>
        <div class="rule-name">{name}</div>
        <div class="rule-status {state_cls}">{status_text}</div>
        <div style="color: #6B7280; font-size: 0.75rem; margin-top: 4px;">Score: {weight_str}</div>
    </div>
    """


def _get_recommendation(risk_level, risk_score):
    if risk_level == "LEGITIMATE":
        return (
            "✅ Auto-Approve",
            "Transaction shows no fraud indicators. Safe to process automatically.",
            "#00D4AA",
        )
    elif risk_level == "SUSPICIOUS":
        return (
            "⚠️ Flag for Manual Review",
            f"Risk score {risk_score}/100 warrants human review before processing.",
            "#FFB547",
        )
    else:
        return (
            "🚨 Block & Escalate",
            f"Risk score {risk_score}/100 exceeds safety threshold. Block transaction and escalate to fraud team.",
            "#FF4B6E",
        )


def _make_gauge(score):
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=score,
        number=dict(suffix="/100", font=dict(size=40, color="#E0E0E0")),
        gauge=dict(
            axis=dict(range=[0, 100], tickcolor="#4A5568", dtick=20),
            bar=dict(color="#4B9EFF", thickness=0.3),
            bgcolor="rgba(26, 31, 46, 0.5)",
            borderwidth=0,
            steps=[
                dict(range=[0, 29], color="rgba(0, 212, 170, 0.2)"),
                dict(range=[30, 59], color="rgba(255, 181, 71, 0.2)"),
                dict(range=[60, 100], color="rgba(255, 75, 110, 0.2)"),
            ],
            threshold=dict(
                line=dict(color="#FF4B6E", width=3),
                thickness=0.8,
                value=60,
            ),
        ),
    ))
    fig.update_layout(
        **PLOTLY_LAYOUT,
        height=250,
        margin=dict(l=30, r=30, t=30, b=10),
    )
    return fig


def _add_to_audit(result, source="single"):
    entry = {
        "analyzed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        **result,
    }
    st.session_state.audit_log.append(entry)


def _process_batch_row(row):
    tx = row.to_dict()
    tx["amount"] = float(tx.get("amount", 0) or 0)
    tx["transactions_last_10min"] = int(tx.get("transactions_last_10min", 0) or 0)
    tx["failed_attempts"] = int(tx.get("failed_attempts", 0) or 0)
    val = str(tx.get("is_new_merchant", "false")).strip().lower()
    tx["is_new_merchant"] = val in {"true", "1", "yes", "y"}
    return assess_transaction(tx)


# ──────────────────────────────────────────────
# Sidebar
# ──────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div style="text-align: center; padding: 20px 0;">
        <div style="font-size: 2.5rem;">🛡️</div>
        <h2 style="margin: 8px 0 4px; color: #E0E0E0; font-weight: 700;">FraudLens</h2>
        <p style="color: #8892A0; font-size: 0.85rem; margin: 0;">Deterministic Fraud Detection Engine</p>
    </div>
    """, unsafe_allow_html=True)

    st.divider()

    # ── Engine Configuration (hidden behind toggle) ──
    if st.toggle("⚙️ Engine Configuration", value=False, key="show_config"):
        st.caption("Adjust rule weights and detection settings. Changes apply to future analyses.")

        # ── Restore defaults ──
        if st.button("🔄 Restore Defaults", use_container_width=True, key="restore_defaults"):
            for k, v in st.session_state._default_rule_weights.items():
                RULE_WEIGHTS[k] = v
            for k, v in st.session_state._default_risk_settings.items():
                RISK_SETTINGS[k] = v
            for k, v in st.session_state._default_risk_thresholds.items():
                RISK_THRESHOLDS[k] = v
            st.toast("✅ Restored to default values!", icon="🔄")
            st.rerun()

        with st.expander("Rule Weights", expanded=True):
            for rule_key in list(RULE_WEIGHTS.keys()):
                label = REASON_MAP.get(rule_key, rule_key)
                default_val = st.session_state._default_rule_weights[rule_key]
                new_val = st.slider(
                    label,
                    min_value=0, max_value=50,
                    value=RULE_WEIGHTS[rule_key],
                    step=5,
                    key=f"w_{rule_key}",
                    help=f"Default: {default_val}",
                )
                RULE_WEIGHTS[rule_key] = new_val

        with st.expander("Risk Thresholds", expanded=False):
            new_legit = st.slider(
                "Legitimate max score",
                min_value=0, max_value=99,
                value=RISK_THRESHOLDS["LEGITIMATE_MAX"],
                key="t_legit",
                help=f"Default: {st.session_state._default_risk_thresholds['LEGITIMATE_MAX']}",
            )
            RISK_THRESHOLDS["LEGITIMATE_MAX"] = new_legit

            new_susp = st.slider(
                "Suspicious max score",
                min_value=new_legit + 1, max_value=99,
                value=max(RISK_THRESHOLDS["SUSPICIOUS_MAX"], new_legit + 1),
                key="t_susp",
                help=f"Default: {st.session_state._default_risk_thresholds['SUSPICIOUS_MAX']}",
            )
            RISK_THRESHOLDS["SUSPICIOUS_MAX"] = new_susp

            st.markdown(f"**High Risk**: {new_susp + 1} – 100")

        with st.expander("Detection Settings", expanded=False):
            new_amount = st.slider(
                "Amount threshold (₹)",
                min_value=500, max_value=50000, step=500,
                value=RISK_SETTINGS["amount_threshold"],
                key="s_amount",
                help=f"Default: ₹{st.session_state._default_risk_settings['amount_threshold']:,}",
            )
            RISK_SETTINGS["amount_threshold"] = new_amount

            hour_c1, hour_c2 = st.columns(2)
            with hour_c1:
                new_h_start = st.number_input(
                    "Unusual hour start",
                    min_value=0, max_value=23,
                    value=RISK_SETTINGS["unusual_hour_start"],
                    key="s_h_start",
                )
                RISK_SETTINGS["unusual_hour_start"] = new_h_start
            with hour_c2:
                new_h_end = st.number_input(
                    "Unusual hour end",
                    min_value=0, max_value=23,
                    value=RISK_SETTINGS["unusual_hour_end"],
                    key="s_h_end",
                )
                RISK_SETTINGS["unusual_hour_end"] = new_h_end

            new_velocity = st.slider(
                "Velocity threshold (txns/10min)",
                min_value=1, max_value=20,
                value=RISK_SETTINGS["velocity_threshold"],
                key="s_velocity",
                help=f"Default: {st.session_state._default_risk_settings['velocity_threshold']}",
            )
            RISK_SETTINGS["velocity_threshold"] = new_velocity

            new_failed = st.slider(
                "Failed attempt threshold",
                min_value=1, max_value=10,
                value=RISK_SETTINGS["failed_attempt_threshold"],
                key="s_failed",
                help=f"Default: {st.session_state._default_risk_settings['failed_attempt_threshold']}",
            )
            RISK_SETTINGS["failed_attempt_threshold"] = new_failed

    st.divider()
    audit_count = len(st.session_state.audit_log)
    st.markdown(f"""
    <div style="text-align: center; padding: 10px 0;">
        <div style="color: #8892A0; font-size: 0.8rem;">Session Activity</div>
        <div style="color: #4B9EFF; font-size: 1.4rem; font-weight: 700;">{audit_count}</div>
        <div style="color: #8892A0; font-size: 0.75rem;">transactions analyzed</div>
    </div>
    """, unsafe_allow_html=True)


# ──────────────────────────────────────────────
# Main tabs
# ──────────────────────────────────────────────
tab_dashboard, tab_batch, tab_audit = st.tabs([
    "🏠 Dashboard",
    "📊 Batch Analysis",
    "📋 Audit Log",
])

# ──────────────────────────────────────────────
# TAB 1: Dashboard
# ──────────────────────────────────────────────
with tab_dashboard:
    st.markdown("## Dashboard Overview")

    if st.session_state.batch_results is not None:
        results = st.session_state.batch_results
        sdf = pd.DataFrame(results)
        total = len(sdf)
        legit = (sdf["risk_level"] == "LEGITIMATE").sum()
        suspicious = (sdf["risk_level"] == "SUSPICIOUS").sum()
        high_risk = (sdf["risk_level"] == "HIGH_RISK").sum()
        avg_score = round(sdf["risk_score"].mean(), 1)
        max_score = sdf["risk_score"].max()
        fraud_rate = round((suspicious + high_risk) / total * 100, 1) if total > 0 else 0

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.markdown(_kpi_card("Total Transactions", f"{total:,}", "kpi-blue"), unsafe_allow_html=True)
        with c2:
            st.markdown(_kpi_card("Fraud Rate", f"{fraud_rate}%", "kpi-red" if fraud_rate > 10 else "kpi-amber"), unsafe_allow_html=True)
        with c3:
            st.markdown(_kpi_card("Avg Risk Score", str(avg_score), "kpi-amber"), unsafe_allow_html=True)
        with c4:
            st.markdown(_kpi_card("Max Risk Score", str(max_score), "kpi-red" if max_score >= 60 else "kpi-green"), unsafe_allow_html=True)

        st.markdown("")

        col_chart, col_stats = st.columns([2, 1])

        with col_chart:
            dist_data = pd.DataFrame({
                "Risk Level": ["LEGITIMATE", "SUSPICIOUS", "HIGH_RISK"],
                "Count": [int(legit), int(suspicious), int(high_risk)],
            })
            fig_donut = px.pie(
                dist_data, values="Count", names="Risk Level",
                color="Risk Level",
                color_discrete_map=RISK_COLORS,
                hole=0.55,
            )
            fig_donut.update_layout(**PLOTLY_LAYOUT, height=350, showlegend=True,
                                    legend=dict(orientation="h", yanchor="bottom", y=-0.15, xanchor="center", x=0.5))
            fig_donut.update_traces(textinfo="percent+value", textfont_size=13)
            st.plotly_chart(fig_donut, use_container_width=True, key="dash_donut")

        with col_stats:
            st.markdown('<div class="section-header">Quick Stats</div>', unsafe_allow_html=True)
            st.markdown(f"""
            <div class="insight-card"><div class="insight-title">Legitimate</div><div class="insight-value" style="color: #00D4AA;">{legit} ({round(legit/total*100,1)}%)</div></div>
            <div class="insight-card"><div class="insight-title">Suspicious</div><div class="insight-value" style="color: #FFB547;">{suspicious} ({round(suspicious/total*100,1)}%)</div></div>
            <div class="insight-card"><div class="insight-title">High Risk</div><div class="insight-value" style="color: #FF4B6E;">{high_risk} ({round(high_risk/total*100,1)}%)</div></div>
            """, unsafe_allow_html=True)

    else:
        st.markdown("")
        st.markdown("""
        <div class="onboard-card">
            <div style="font-size: 3rem; margin-bottom: 12px;">🛡️</div>
            <h3>Welcome to FraudLens</h3>
            <p>A deterministic, rule-based fraud detection engine that analyzes transactions<br>
            without any LLM or external AI dependency.</p>
            <div style="margin-top: 24px; display: flex; gap: 16px; justify-content: center; flex-wrap: wrap;">
                <div style="background: #1A1F2E; border-radius: 12px; padding: 16px 24px; border: 1px solid #2A3040; flex: 1; min-width: 180px; max-width: 250px;">
                    <div style="font-size: 1.3rem;">📊</div>
                    <div style="color: #E0E0E0; font-weight: 600; margin: 6px 0 4px;">Batch Analysis</div>
                    <div style="color: #8892A0; font-size: 0.8rem;">Upload a CSV dataset to analyze hundreds of transactions at once</div>
                </div>
                <div style="background: #1A1F2E; border-radius: 12px; padding: 16px 24px; border: 1px solid #2A3040; flex: 1; min-width: 180px; max-width: 250px;">
                    <div style="font-size: 1.3rem;">📋</div>
                    <div style="color: #E0E0E0; font-weight: 600; margin: 6px 0 4px;">Audit Trail</div>
                    <div style="color: #8892A0; font-size: 0.8rem;">Browse, filter, and export all analyzed transactions</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("")
        st.info("💡 **Get started** — navigate to the **📊 Batch Analysis** tab to upload a CSV dataset.")



# ──────────────────────────────────────────────
# TAB 3: Batch Analysis
# ──────────────────────────────────────────────
with tab_batch:
    st.markdown("## Batch Analysis")
    st.caption("Upload a CSV dataset to analyze multiple transactions and generate data insights.")

    uploaded_file = st.file_uploader("Upload a CSV file", type=["csv"], key="batch_uploader")

    if uploaded_file is not None:
        df = pd.read_csv(uploaded_file)

        with st.expander("📋 Dataset Preview", expanded=True):
            st.dataframe(df.head(10), use_container_width=True)
            st.caption(f"Dataset contains **{len(df)} rows** and **{len(df.columns)} columns**.")

        if st.button("🚀  Run Fraud Analysis", use_container_width=True, type="primary"):
            progress_bar = st.progress(0, text="Analyzing transactions...")
            results = []
            total_rows = len(df)

            for idx, (_, row) in enumerate(df.iterrows()):
                result = _process_batch_row(row)
                results.append(result)
                _add_to_audit(result, source="batch")
                if (idx + 1) % max(1, total_rows // 20) == 0 or idx == total_rows - 1:
                    progress_bar.progress((idx + 1) / total_rows, text=f"Analyzed {idx + 1}/{total_rows} transactions...")

            progress_bar.empty()
            st.toast("✅ Batch analysis complete!", icon="🎉")

            st.session_state.batch_results = results
            st.session_state.batch_df = df
            st.session_state.batch_report = generate_batch_report(results, use_llm=False)

        # ── Render results if available ──
        if st.session_state.batch_results is not None:
            results = st.session_state.batch_results
            sdf = pd.DataFrame(results)
            raw_df = st.session_state.batch_df

            total = len(sdf)
            legit = int((sdf["risk_level"] == "LEGITIMATE").sum())
            suspicious = int((sdf["risk_level"] == "SUSPICIOUS").sum())
            high_risk = int((sdf["risk_level"] == "HIGH_RISK").sum())
            avg_score = round(sdf["risk_score"].mean(), 1)
            max_score = int(sdf["risk_score"].max())
            min_score = int(sdf["risk_score"].min())
            median_score = round(sdf["risk_score"].median(), 1)
            fraud_rate = round((suspicious + high_risk) / total * 100, 1) if total > 0 else 0

            st.markdown("---")

            st.markdown('<div class="section-header">🤖 Ask FraudLens About This Batch</div>', unsafe_allow_html=True)
            st.caption("Your request is applied only to the generated explanation. Risk scores and classifications remain deterministic.")
            llm_prompt = st.text_area(
                "Report prompt",
                value="Summarize the main fraud patterns and recommend next operational steps.",
                key="llm_prompt",
                height=90,
            )
            if st.button("✨ Generate FraudLens Report", type="primary", key="generate_gemini_report"):
                st.session_state.batch_report = generate_batch_report(
                    results,
                    use_llm=True,
                    user_prompt=llm_prompt,
                )
                st.toast("Gemini report generated.", icon="🤖")

            if st.session_state.batch_report.get("llm_explanation"):
                st.markdown('<div class="section-header">🤖 Gemini Response</div>', unsafe_allow_html=True)
                st.info(st.session_state.batch_report["llm_explanation"])
            elif st.session_state.batch_report.get("llm_status") == "incomplete_response":
                st.warning(
                    "Gemini returned an incomplete response after an automatic retry. "
                    "The deterministic summary is shown instead."
                )
                st.info(st.session_state.batch_report.get("deterministic_summary", "No summary available."))
            else:
                st.caption(
                    f"Gemini response: {st.session_state.batch_report.get('llm_status', 'unavailable')}. "
                    "The deterministic report remains authoritative."
                )

            # ── KPI Row ──
            k1, k2, k3, k4, k5 = st.columns(5)
            with k1:
                st.markdown(_kpi_card("Total", f"{total:,}", "kpi-blue"), unsafe_allow_html=True)
            with k2:
                st.markdown(_kpi_card("Legitimate", str(legit), "kpi-green"), unsafe_allow_html=True)
            with k3:
                st.markdown(_kpi_card("Suspicious", str(suspicious), "kpi-amber"), unsafe_allow_html=True)
            with k4:
                st.markdown(_kpi_card("High Risk", str(high_risk), "kpi-red"), unsafe_allow_html=True)
            with k5:
                st.markdown(_kpi_card("Fraud Rate", f"{fraud_rate}%", "kpi-red" if fraud_rate > 10 else "kpi-amber"), unsafe_allow_html=True)

            st.markdown("")

            # ── Visualizations ──
            st.markdown('<div class="section-header">📊 Visual Analytics</div>', unsafe_allow_html=True)

            # Row 1: Donut + Histogram
            viz_c1, viz_c2 = st.columns(2)

            with viz_c1:
                st.markdown("##### Risk Level Distribution")
                dist_data = pd.DataFrame({
                    "Risk Level": ["LEGITIMATE", "SUSPICIOUS", "HIGH_RISK"],
                    "Count": [legit, suspicious, high_risk],
                })
                fig_pie = px.pie(
                    dist_data, values="Count", names="Risk Level",
                    color="Risk Level", color_discrete_map=RISK_COLORS, hole=0.55,
                )
                fig_pie.update_layout(**PLOTLY_LAYOUT, height=350, showlegend=True,
                                      legend=dict(orientation="h", yanchor="bottom", y=-0.15, xanchor="center", x=0.5))
                fig_pie.update_traces(textinfo="percent+value", textfont_size=12)
                st.plotly_chart(fig_pie, use_container_width=True, key="batch_pie")

            with viz_c2:
                st.markdown("##### Risk Score Distribution")
                fig_hist = px.histogram(
                    sdf, x="risk_score", nbins=20,
                    color_discrete_sequence=["#4B9EFF"],
                    labels={"risk_score": "Risk Score"},
                )
                fig_hist.add_vrect(x0=0, x1=29, fillcolor="#00D4AA", opacity=0.08, line_width=0)
                fig_hist.add_vrect(x0=30, x1=59, fillcolor="#FFB547", opacity=0.08, line_width=0)
                fig_hist.add_vrect(x0=60, x1=100, fillcolor="#FF4B6E", opacity=0.08, line_width=0)
                fig_hist.update_layout(**PLOTLY_LAYOUT, height=350, bargap=0.05,
                                       xaxis_title="Risk Score", yaxis_title="Count")
                st.plotly_chart(fig_hist, use_container_width=True, key="batch_hist")

            # Row 2: Amount vs Score scatter + Rule Frequency
            viz_c3, viz_c4 = st.columns(2)

            with viz_c3:
                st.markdown("##### Amount vs Risk Score")
                # Merge amount from raw_df
                scatter_df = sdf.copy()
                if "amount" in raw_df.columns:
                    scatter_df["amount"] = raw_df["amount"].values[:len(scatter_df)]
                else:
                    scatter_df["amount"] = 0
                fig_scatter = px.scatter(
                    scatter_df, x="amount", y="risk_score",
                    color="risk_level", color_discrete_map=RISK_COLORS,
                    labels={"amount": "Amount (₹)", "risk_score": "Risk Score", "risk_level": "Risk Level"},
                    opacity=0.7,
                )
                fig_scatter.update_layout(**PLOTLY_LAYOUT, height=380,
                                          legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5))
                fig_scatter.update_traces(marker=dict(size=8, line=dict(width=0.5, color="#1A1F2E")))
                st.plotly_chart(fig_scatter, use_container_width=True, key="batch_scatter")

            with viz_c4:
                st.markdown("##### Rule Trigger Frequency")
                all_triggered = []
                for r in results:
                    all_triggered.extend(r.get("triggered_rules", []))
                rule_counts = Counter(all_triggered)
                if rule_counts:
                    freq_df = pd.DataFrame([
                        {"Rule": REASON_MAP.get(k, k), "Count": v}
                        for k, v in rule_counts.most_common()
                    ])
                    fig_freq = px.bar(
                        freq_df, x="Count", y="Rule", orientation="h",
                        color="Count",
                        color_continuous_scale=["#4B9EFF", "#FFB547", "#FF4B6E"],
                        text="Count",
                    )
                    fig_freq.update_layout(**PLOTLY_LAYOUT, height=380,
                                           coloraxis_showscale=False, yaxis=dict(autorange="reversed"))
                    fig_freq.update_traces(textposition="outside", textfont_size=13)
                    st.plotly_chart(fig_freq, use_container_width=True, key="batch_freq")
                else:
                    st.info("No rules were triggered in this dataset.")

            # Row 3: Payment Method + Location breakdown
            viz_c5, viz_c6 = st.columns(2)

            with viz_c5:
                st.markdown("##### Risk by Payment Method")
                if "payment_method" in raw_df.columns:
                    pm_df = pd.DataFrame({
                        "payment_method": raw_df["payment_method"].values[:len(sdf)],
                        "risk_score": sdf["risk_score"].values,
                        "risk_level": sdf["risk_level"].values,
                    })
                    pm_agg = pm_df.groupby("payment_method").agg(
                        avg_score=("risk_score", "mean"),
                        count=("risk_score", "count"),
                        high_risk_count=("risk_level", lambda x: (x == "HIGH_RISK").sum()),
                    ).reset_index()
                    pm_agg["avg_score"] = pm_agg["avg_score"].round(1)
                    fig_pm = px.bar(
                        pm_agg, x="payment_method", y="avg_score",
                        color="avg_score",
                        color_continuous_scale=["#00D4AA", "#FFB547", "#FF4B6E"],
                        text="avg_score",
                        labels={"payment_method": "Payment Method", "avg_score": "Avg Risk Score"},
                        hover_data=["count", "high_risk_count"],
                    )
                    fig_pm.update_layout(**PLOTLY_LAYOUT, height=380, coloraxis_showscale=False)
                    fig_pm.update_traces(textposition="outside", textfont_size=12)
                    st.plotly_chart(fig_pm, use_container_width=True, key="batch_pm")
                else:
                    st.info("No `payment_method` column found in the dataset.")

            with viz_c6:
                st.markdown("##### Risk by Location")
                if "location" in raw_df.columns:
                    loc_df = pd.DataFrame({
                        "location": raw_df["location"].values[:len(sdf)],
                        "risk_score": sdf["risk_score"].values,
                        "risk_level": sdf["risk_level"].values,
                    })
                    loc_agg = loc_df.groupby("location").agg(
                        avg_score=("risk_score", "mean"),
                        count=("risk_score", "count"),
                        high_risk_count=("risk_level", lambda x: (x == "HIGH_RISK").sum()),
                    ).reset_index()
                    loc_agg = loc_agg.sort_values("avg_score", ascending=False).head(12)
                    loc_agg["avg_score"] = loc_agg["avg_score"].round(1)
                    fig_loc = px.bar(
                        loc_agg, x="avg_score", y="location", orientation="h",
                        color="avg_score",
                        color_continuous_scale=["#00D4AA", "#FFB547", "#FF4B6E"],
                        text="avg_score",
                        labels={"location": "Location", "avg_score": "Avg Risk Score"},
                        hover_data=["count", "high_risk_count"],
                    )
                    fig_loc.update_layout(**PLOTLY_LAYOUT, height=380, coloraxis_showscale=False,
                                          yaxis=dict(autorange="reversed"))
                    fig_loc.update_traces(textposition="outside", textfont_size=12)
                    st.plotly_chart(fig_loc, use_container_width=True, key="batch_loc")
                else:
                    st.info("No `location` column found in the dataset.")

            # Row 4: Hourly timeline + Merchant risk
            viz_c7, viz_c8 = st.columns(2)

            with viz_c7:
                st.markdown("##### Hourly Activity & Risk")
                if "timestamp" in raw_df.columns:
                    time_df = pd.DataFrame({
                        "timestamp": pd.to_datetime(raw_df["timestamp"].values[:len(sdf)], errors="coerce"),
                        "risk_score": sdf["risk_score"].values,
                        "risk_level": sdf["risk_level"].values,
                    }).dropna(subset=["timestamp"])
                    if not time_df.empty:
                        time_df["hour"] = time_df["timestamp"].dt.hour
                        hourly = time_df.groupby("hour").agg(
                            count=("risk_score", "count"),
                            avg_score=("risk_score", "mean"),
                        ).reset_index()
                        hourly["avg_score"] = hourly["avg_score"].round(1)

                        fig_hourly = go.Figure()
                        fig_hourly.add_trace(go.Bar(
                            x=hourly["hour"], y=hourly["count"],
                            name="Transaction Count",
                            marker_color="#4B9EFF",
                            opacity=0.7,
                        ))
                        fig_hourly.add_trace(go.Scatter(
                            x=hourly["hour"], y=hourly["avg_score"],
                            name="Avg Risk Score",
                            line=dict(color="#FF4B6E", width=3),
                            yaxis="y2",
                        ))
                        fig_hourly.update_layout(
                            **PLOTLY_LAYOUT, height=380,
                            xaxis=dict(title="Hour of Day", dtick=2),
                            yaxis=dict(title="Count", side="left"),
                            yaxis2=dict(title="Avg Risk Score", side="right", overlaying="y", range=[0, 100]),
                            legend=dict(orientation="h", yanchor="bottom", y=-0.25, xanchor="center", x=0.5),
                            barmode="overlay",
                        )
                        st.plotly_chart(fig_hourly, use_container_width=True, key="batch_hourly")
                    else:
                        st.info("Could not parse timestamps for hourly analysis.")
                else:
                    st.info("No `timestamp` column found.")

            with viz_c8:
                st.markdown("##### Top Risky Merchants")
                if "merchant" in raw_df.columns:
                    merch_df = pd.DataFrame({
                        "merchant": raw_df["merchant"].values[:len(sdf)],
                        "risk_score": sdf["risk_score"].values,
                        "risk_level": sdf["risk_level"].values,
                    })
                    merch_agg = merch_df.groupby("merchant").agg(
                        avg_score=("risk_score", "mean"),
                        count=("risk_score", "count"),
                        high_risk_count=("risk_level", lambda x: (x == "HIGH_RISK").sum()),
                    ).reset_index()
                    merch_agg = merch_agg.sort_values("avg_score", ascending=False).head(10)
                    merch_agg["avg_score"] = merch_agg["avg_score"].round(1)
                    fig_merch = px.bar(
                        merch_agg, x="avg_score", y="merchant", orientation="h",
                        color="avg_score",
                        color_continuous_scale=["#00D4AA", "#FFB547", "#FF4B6E"],
                        text="avg_score",
                        labels={"merchant": "Merchant", "avg_score": "Avg Risk Score"},
                        hover_data=["count", "high_risk_count"],
                    )
                    fig_merch.update_layout(**PLOTLY_LAYOUT, height=380, coloraxis_showscale=False,
                                            yaxis=dict(autorange="reversed"))
                    fig_merch.update_traces(textposition="outside", textfont_size=12)
                    st.plotly_chart(fig_merch, use_container_width=True, key="batch_merch")
                else:
                    st.info("No `merchant` column found.")

            # ── Auto-generated insights ──
            st.markdown('<div class="section-header">🧠 Auto-Generated Insights</div>', unsafe_allow_html=True)

            insights = []

            # Most common rule
            if rule_counts:
                top_rule_key, top_rule_count = rule_counts.most_common(1)[0]
                top_rule_name = REASON_MAP.get(top_rule_key, top_rule_key)
                pct = round(top_rule_count / total * 100, 1)
                insights.append(("🏆 Most Common Trigger", f'"{top_rule_name}" fired in {pct}% of all transactions ({top_rule_count}/{total}).'))

            # Riskiest merchant
            if "merchant" in raw_df.columns:
                merch_risk = pd.DataFrame({
                    "merchant": raw_df["merchant"].values[:len(sdf)],
                    "risk_score": sdf["risk_score"].values,
                }).groupby("merchant")["risk_score"].mean()
                riskiest = merch_risk.idxmax()
                riskiest_score = round(merch_risk.max(), 1)
                insights.append(("🏪 Riskiest Merchant", f'"{riskiest}" has the highest average risk score of {riskiest_score}/100.'))

            # Riskiest time
            if "timestamp" in raw_df.columns:
                time_df2 = pd.DataFrame({
                    "timestamp": pd.to_datetime(raw_df["timestamp"].values[:len(sdf)], errors="coerce"),
                    "risk_score": sdf["risk_score"].values,
                }).dropna(subset=["timestamp"])
                if not time_df2.empty:
                    time_df2["hour"] = time_df2["timestamp"].dt.hour
                    hourly_risk = time_df2.groupby("hour")["risk_score"].mean()
                    peak_hour = hourly_risk.idxmax()
                    peak_score = round(hourly_risk.max(), 1)
                    insights.append(("🕐 Riskiest Time Window", f"{peak_hour}:00–{(peak_hour+1)%24}:00 has the highest avg risk score ({peak_score}/100)."))

            # Location insight
            if "location" in raw_df.columns:
                loc_risk = pd.DataFrame({
                    "location": raw_df["location"].values[:len(sdf)],
                    "risk_score": sdf["risk_score"].values,
                }).groupby("location")["risk_score"].mean()
                riskiest_loc = loc_risk.idxmax()
                riskiest_loc_score = round(loc_risk.max(), 1)
                insights.append(("📍 Riskiest Location", f'"{riskiest_loc}" leads with an avg risk score of {riskiest_loc_score}/100.'))

            # Payment method insight
            if "payment_method" in raw_df.columns:
                pm_risk = pd.DataFrame({
                    "payment_method": raw_df["payment_method"].values[:len(sdf)],
                    "risk_score": sdf["risk_score"].values,
                }).groupby("payment_method")["risk_score"].mean()
                riskiest_pm = pm_risk.idxmax()
                riskiest_pm_score = round(pm_risk.max(), 1)
                insights.append(("💳 Riskiest Payment Method", f'"{riskiest_pm}" averages a risk score of {riskiest_pm_score}/100.'))

            # Score stats
            insights.append(("📈 Score Statistics", f"Mean: {avg_score} | Median: {median_score} | Min: {min_score} | Max: {max_score}"))

            # Amount-risk correlation
            if "amount" in raw_df.columns:
                hr_amounts = raw_df["amount"].values[:len(sdf)][sdf["risk_level"].values == "HIGH_RISK"]
                non_hr_amounts = raw_df["amount"].values[:len(sdf)][sdf["risk_level"].values != "HIGH_RISK"]
                if len(hr_amounts) > 0 and len(non_hr_amounts) > 0:
                    hr_avg_amt = round(float(hr_amounts.mean()), 0)
                    non_hr_avg_amt = round(float(non_hr_amounts.mean()), 0)
                    ratio = round(hr_avg_amt / non_hr_avg_amt, 1) if non_hr_avg_amt > 0 else 0
                    insights.append(("💰 Amount Analysis", f"HIGH_RISK transactions average ₹{hr_avg_amt:,.0f} — {ratio}x higher than non-flagged transactions (₹{non_hr_avg_amt:,.0f})."))

            i_cols = st.columns(2)
            for idx, (title, desc) in enumerate(insights):
                with i_cols[idx % 2]:
                    st.markdown(f"""
                    <div class="insight-card">
                        <div class="insight-title">{title}</div>
                        <div class="insight-value">{desc}</div>
                    </div>
                    """, unsafe_allow_html=True)

            st.markdown("")

            # ── Top flagged transactions table ──
            st.markdown('<div class="section-header">🚩 Top Flagged Transactions</div>', unsafe_allow_html=True)
            top_flagged = sdf.nlargest(15, "risk_score")[["transaction_id", "risk_score", "risk_level", "triggered_rules", "reason_list"]]
            st.dataframe(top_flagged, use_container_width=True, hide_index=True)

            # ── Full results + Export ──
            with st.expander("📋 View Full Results Table"):
                st.dataframe(sdf, use_container_width=True, hide_index=True)

            dl_c1, dl_c2 = st.columns(2)
            with dl_c1:
                st.download_button(
                    "📥 Download Results (JSON)",
                    data=json.dumps(results, default=str),
                    file_name="fraud_results.json",
                    mime="application/json",
                    use_container_width=True,
                )
            with dl_c2:
                st.download_button(
                    "📥 Download Results (CSV)",
                    data=sdf.to_csv(index=False),
                    file_name="fraud_results.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

            report_c1, report_c2 = st.columns(2)
            with report_c1:
                st.download_button(
                    "📄 Download Summary (JSON)",
                    data=json.dumps(st.session_state.batch_report, indent=2, default=str),
                    file_name="fraud_summary.json",
                    mime="application/json",
                    use_container_width=True,
                )
            with report_c2:
                st.download_button(
                    "📝 Download Final LLM Report (Markdown)",
                    data=render_markdown_report(st.session_state.batch_report, results),
                    file_name="fraud_report.md",
                    mime="text/markdown",
                    use_container_width=True,
                )

    else:
        st.markdown("""
        <div class="onboard-card" style="margin-top: 20px;">
            <div style="font-size: 2.5rem; margin-bottom: 12px;">📂</div>
            <h3>Upload a Dataset</h3>
            <p>Upload a CSV file with transaction data to run batch fraud analysis.<br>
            The file should include columns: transaction_id, timestamp, amount, merchant, location, etc.</p>
        </div>
        """, unsafe_allow_html=True)


# ──────────────────────────────────────────────
# TAB 3: Audit Log
# ──────────────────────────────────────────────
with tab_audit:
    st.markdown("## Audit Log")
    st.caption("Browse all uploaded transactions with filtering by risk level and date.")

    if st.session_state.audit_log:
        audit_df = pd.DataFrame(st.session_state.audit_log)

        # Parse dates for filtering
        if "analyzed_at" in audit_df.columns:
            audit_df["_dt"] = pd.to_datetime(audit_df["analyzed_at"], errors="coerce")
        else:
            audit_df["_dt"] = pd.NaT

        # ── Summary KPIs ──
        a_total = len(audit_df)
        a_legit = int((audit_df["risk_level"] == "LEGITIMATE").sum()) if "risk_level" in audit_df.columns else 0
        a_suspicious = int((audit_df["risk_level"] == "SUSPICIOUS").sum()) if "risk_level" in audit_df.columns else 0
        a_high_risk = int((audit_df["risk_level"] == "HIGH_RISK").sum()) if "risk_level" in audit_df.columns else 0

        ac1, ac2, ac3, ac4 = st.columns(4)
        with ac1:
            st.markdown(_kpi_card("Total Transactions", str(a_total), "kpi-blue"), unsafe_allow_html=True)
        with ac2:
            st.markdown(_kpi_card("Legitimate", str(a_legit), "kpi-green"), unsafe_allow_html=True)
        with ac3:
            st.markdown(_kpi_card("Suspicious", str(a_suspicious), "kpi-amber"), unsafe_allow_html=True)
        with ac4:
            st.markdown(_kpi_card("High Risk", str(a_high_risk), "kpi-red"), unsafe_allow_html=True)

        st.markdown("")
        st.markdown('<div class="section-header">🔎 Filter Transactions</div>', unsafe_allow_html=True)

        # ── Filters row ──
        filter_c1, filter_c2, filter_c3 = st.columns([1, 1, 1])

        with filter_c1:
            risk_filter = st.multiselect(
                "Risk Level",
                ["LEGITIMATE", "SUSPICIOUS", "HIGH_RISK"],
                default=["LEGITIMATE", "SUSPICIOUS", "HIGH_RISK"],
                key="audit_risk_filter",
            )

        with filter_c2:
            # Date range filter
            valid_dates = audit_df["_dt"].dropna()
            if not valid_dates.empty:
                min_date = valid_dates.min().date()
                max_date = valid_dates.max().date()
                date_range = st.date_input(
                    "Date Range",
                    value=(min_date, max_date),
                    min_value=min_date,
                    max_value=max_date,
                    key="audit_date_filter",
                )
            else:
                date_range = None
                st.info("No valid dates to filter.")

        with filter_c3:
            search_text = st.text_input(
                "🔍 Search (ID or Merchant)",
                placeholder="e.g. TXN0042 or Amazon",
                key="audit_search",
            )

        # ── Apply filters ──
        filtered = audit_df.copy()

        # Risk level filter
        if "risk_level" in filtered.columns:
            filtered = filtered[filtered["risk_level"].isin(risk_filter)]

        # Date range filter
        if date_range is not None and len(date_range) == 2:
            start_dt, end_dt = date_range
            mask = filtered["_dt"].notna()
            filtered = filtered[
                (~mask) |
                ((filtered["_dt"].dt.date >= start_dt) & (filtered["_dt"].dt.date <= end_dt))
            ]

        # Text search filter
        if search_text.strip():
            search_lower = search_text.strip().lower()
            text_mask = pd.Series(False, index=filtered.index)
            if "transaction_id" in filtered.columns:
                text_mask |= filtered["transaction_id"].astype(str).str.lower().str.contains(search_lower, na=False)
            # Check if original batch data has merchant column
            if st.session_state.batch_df is not None and "merchant" in st.session_state.batch_df.columns:
                # Build a txn_id → merchant lookup from the raw data
                raw = st.session_state.batch_df
                if "transaction_id" in raw.columns and "transaction_id" in filtered.columns:
                    merchant_map = dict(zip(raw["transaction_id"].astype(str), raw["merchant"].astype(str).str.lower()))
                    text_mask |= filtered["transaction_id"].astype(str).map(lambda tid: search_lower in merchant_map.get(tid, ""))
            filtered = filtered[text_mask]

        # ── Show active filter tags ──
        active_filters = []
        if len(risk_filter) < 3:
            for rl in risk_filter:
                badge_cls = {"LEGITIMATE": "badge-legitimate", "SUSPICIOUS": "badge-suspicious", "HIGH_RISK": "badge-high-risk"}.get(rl, "")
                active_filters.append(f'<span class="risk-badge {badge_cls}" style="font-size: 0.75rem; padding: 4px 12px;">{rl}</span>')
        if date_range and len(date_range) == 2:
            active_filters.append(f'<span class="risk-badge badge-legitimate" style="font-size: 0.75rem; padding: 4px 12px;">📅 {date_range[0]} → {date_range[1]}</span>')
        if search_text.strip():
            active_filters.append(f'<span class="risk-badge badge-suspicious" style="font-size: 0.75rem; padding: 4px 12px;">🔍 "{search_text.strip()}"</span>')

        if active_filters:
            st.markdown(f'<div style="margin: 8px 0 16px;">Active filters: {" ".join(active_filters)} &nbsp; — &nbsp; <span style="color: #8892A0;">{len(filtered)} of {a_total} transactions</span></div>', unsafe_allow_html=True)

        st.markdown("")

        # ── Results table ──
        display_cols = [c for c in ["analyzed_at", "transaction_id", "risk_score", "risk_level", "triggered_rules", "reason_list", "status"] if c in filtered.columns]
        st.dataframe(
            filtered[display_cols],
            use_container_width=True,
            hide_index=True,
            column_config={
                "analyzed_at": st.column_config.TextColumn("Analyzed At", width="medium"),
                "transaction_id": st.column_config.TextColumn("Transaction ID", width="small"),
                "risk_score": st.column_config.ProgressColumn("Risk Score", min_value=0, max_value=100, format="%d"),
                "risk_level": st.column_config.TextColumn("Risk Level", width="small"),
                "triggered_rules": st.column_config.ListColumn("Triggered Rules"),
                "reason_list": st.column_config.ListColumn("Reasons"),
                "status": st.column_config.TextColumn("Status", width="small"),
            },
        )

        st.markdown("")

        # ── Export buttons ──
        dl_a1, dl_a2, dl_a3 = st.columns([1, 1, 2])
        with dl_a1:
            st.download_button(
                "📥 Export Filtered (JSON)",
                data=json.dumps(filtered.drop(columns=["_dt"], errors="ignore").to_dict(orient="records"), default=str),
                file_name="audit_log.json",
                mime="application/json",
                use_container_width=True,
            )
        with dl_a2:
            st.download_button(
                "📥 Export Filtered (CSV)",
                data=filtered.drop(columns=["_dt"], errors="ignore").to_csv(index=False),
                file_name="audit_log.csv",
                mime="text/csv",
                use_container_width=True,
            )
        with dl_a3:
            if st.button("🗑️ Clear Audit Log", use_container_width=True):
                st.session_state.audit_log = []
                st.rerun()

    else:
        st.markdown("""
        <div class="onboard-card" style="margin-top: 20px;">
            <div style="font-size: 2.5rem; margin-bottom: 12px;">📋</div>
            <h3>No Transactions Yet</h3>
            <p>Upload a CSV dataset in the <strong>📊 Batch Analysis</strong> tab to get started.<br>
            All analyzed transactions will appear here with full filtering capabilities.</p>
        </div>
        """, unsafe_allow_html=True)
