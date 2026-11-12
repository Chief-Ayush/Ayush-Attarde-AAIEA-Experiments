"""Test values: type in one candidate and see exactly how it is validated, scored, routed and drafted. Nothing is saved."""
import pandas as pd
import streamlit as st

from ui.common import bar_chart, call, esc, html_table, label, page_header, page_setup, section, stat_tiles, status_badge

page_setup("Test values")
import notifications as nt  # noqa: E402

page_header("Test values", "Try any values. Nothing is saved or sent.")
PRESETS = {"Manual example (C101)": (85, 70, 90, "Junior Software Developer"), "Exactly 75": (75, 75, 75, "Junior Software Developer"),
           "Just below 75 (74.99)": (74.975, 75, 75, "Junior Software Developer"), "Exactly 50": (50, 50, 50, "Data Analyst"),
           "Just below 50 (49.99)": (49.99, 49.99, 49.99, "Data Analyst"), "Out of range": (120, 70, 80, "Data Analyst")}
preset = st.selectbox("Start from", list(PRESETS))
k, e, a, role = PRESETS[preset]
vers = call("get_rules").data["versions"]
with st.form("tv"):
    c1, c2, c3 = st.columns(3)
    name = c1.text_input("Name", "Test Candidate")
    email = c2.text_input("E-mail", "test.candidate@example.com")
    role = c3.selectbox("Role", ["Junior Software Developer", "Data Analyst"], index=["Junior Software Developer", "Data Analyst"].index(role))
    c1, c2, c3, c4 = st.columns(4)
    sk = c1.text_input("Skills", str(k))
    ex = c2.text_input("Experience", str(e))
    asm = c3.text_input("Assessment", str(a))
    ver = c4.selectbox("Rule version", vers)
    note = st.text_input("Cover note (never used for scoring)", "")
    go = st.form_submit_button("Check", type="primary")
if not go:
    st.stop()
rec = {"candidate_id": "TEST1", "candidate_name": name, "email": email, "skills_score": sk, "experience_score": ex, "assessment_score": asm, "application_role": role,
       "college": "", "cover_note": note}
v = call("validate_records", records=[rec]).data
if v["rejected"]:
    r = v["rejected"][0]
    st.error(f"Quarantined: {r['reason']}")
    st.caption("Next step: " + r["next_action"])
    st.stop()
c = v["valid"][0]
s = call("score_candidates", candidates=[c], version=ver).data["results"][0]
stat_tiles([("Score", f"{s['score']:.2f}", "out of 100"), ("Proposed status", label(s["proposed_status"])), ("Rule version", s["rule_version"]),
            ("Thresholds", f"{s['thresholds']['shortlist_review']:g} / {s['thresholds']['manual_review']:g}")])
if c["flags"]:
    st.warning(c["flags"][0]["detail"])
c1, c2 = st.columns([1.2, 1])
with c1:
    st.markdown(html_table(["Criterion", "Input", "Weight", "Contribution"], [[esc(p["label"]), f"{p['input']:g}", f"{p['weight']:g}%", f"<b>{p['contribution']:.2f}</b> / {p['max']:g}"] for p in s["breakdown"]],
                           num_cols=(1, 2, 3)), unsafe_allow_html=True)
    st.code(s["explanation"], language=None)
with c2:
    d = nt.generate_notification(c, s["proposed_status"])
    section("Draft message")
    st.text(d["message"])
    problems = nt.draft_problems(d, c)
    st.caption("Draft checks passed: no score, no final decision." if not problems else "Problems: " + "; ".join(problems))
