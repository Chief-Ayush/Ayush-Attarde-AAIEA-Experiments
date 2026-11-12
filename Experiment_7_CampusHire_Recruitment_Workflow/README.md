# CampusHire - Automated Recruitment Workflow System

Lab 7 case study (HR domain): a campus hiring drive for two roles, **Junior Software Developer** and **Data Analyst**.
Candidate files are validated, scored with versioned weighted rules, routed to a *proposed* status, turned into *draft* notifications and then
held for a human reviewer. Nothing is sent until a reviewer has decided and approved the message. Every step is a tool call over MCP and is audited.

## Quick start

```bash
pip install -r requirements.txt
python -m campushire.cli run --file candidates.csv        # end-to-end console run (lab step 12)
python -m campushire.cli demo                             # seed a demo drive for the dashboard
streamlit run app.py                                      # dashboard
python -m pytest -q                                       # unit + integration tests
python -m campushire.cli evaluate                         # full evaluation (about 2.5 minutes)
```

Users (`users.json`): `priya.reviewer` (hr_reviewer), `arjun.admin` (admin), `meera.viewer` (viewer). Roles are looked up by the servers, never supplied by the caller.

## Files named in the lab manual

| File | Purpose |
|---|---|
| `candidates.csv` | 120 candidate records. The first five are the manual's C101-C105; the rest include missing e-mails, duplicate ids, out-of-range and non-numeric scores, 49.99 / 50 / 74.99 / 75 boundaries, delivery failures and applications that try to steer the system |
| `rules.json` | Versioned criteria, weights and thresholds per role (1.0 baseline, 1.1 gives the assessment more weight) |
| `recruitment_workflow.py` | Loading CSV/JSON, normalisation, validation, duplicate detection, Decimal-exact scoring, routing, review-action mapping |
| `notifications.py` | DRAFT templates (no scores, no final rejection wording) and the simulated mail gateway |
| `test_recruitment.py` | Unit tests for the manual's T01-T15 cases and the rule / file edge cases |
| `rejected_records.csv`, `audit_log.csv` | Quarantined records and the append-only audit trail (created by the runs) |
| `users.json` | Reviewer ids and roles |

## Architecture

```
Streamlit dashboard / CLI
        |
   LangGraph workflow (campushire/workflow.py)
   ingest -> validate -> score -> draft -> [HUMAN REVIEW: interrupt] -> apply decisions -> dispatch -> report
        |
   MCP hub (campushire/mcp_client.py): persistent stdio sessions, schema validation, retry, reconnect, TRACE / TASK ids
        |
   +-----------+-------------+-------------+--------------+
   | ingest    | scoring     | comms       | records      |   four FastMCP servers (campushire/servers)
   | 4 tools   | 7 tools     | 6 tools     | 13 tools     |
   +-----------+-------------+-------------+--------------+
        |
   SQLite store (campushire/store.py): hash-chained audit table with UPDATE / DELETE blocked by triggers
```

* **MCP (all steps).** Servers expose typed tools with read-only / destructive annotations and a LOW / MEDIUM / HIGH risk level. The client validates arguments
  against the tool schema before calling, classifies failures as INPUT, TOOL, SECURITY, TIMEOUT or EXECUTION, retries only the retryable ones
  (maximum two), and reconnects a crashed server. Traces go to `storage/mcp_logs/mcp_traces.jsonl` and `execution.log`.
* **Human in the loop.** Reviewer actions are APPROVE, EDIT (needs a status and a documented override reason), HOLD and REQUEST_INFO (both need a note).
  The proposed status is stored separately from the final decision. A message can be sent only after the reviewer has approved its exact text.
* **Delivery.** SENT, FAILED (after 1 + 2 attempts) or NOT_SENT. Addresses at `bounce.example` simulate a refused mailbox.
* **Safety.** Candidate free text is never an input to scoring; instruction-like text is flagged for the reviewer. Rules that use any criterion other than the
  three job-related scores are refused. CSV exports neutralise formula injection. Paths outside the project are refused.

## Variations implemented

Streamlit dashboard, SQLite persistence, notification retry (max 2), summary report, role-based access, fairness review, two roles with their own weights,
SLA reminders (with a simulated clock), sequential vs batch vs parallel execution, and a rule version 1.0 vs 1.1 comparison.

## Dashboard pages

Overview, Run workflow, Review queue, Quarantine, Audit log, Reports (summary, overrides, rule versions, fairness), MCP inspector, Batch testing,
Evaluation suite, Evaluation report, Traces and logs, Settings.

## Evaluation

`python -m campushire.cli evaluate` runs seven sections, every case through the real MCP servers in its own drive:
functional (T01-T15 and edge cases), accuracy (120 records against an independent reference computed with exact fractions), human review, security,
failure handling (fault injection per error category, server crash), performance (three execution modes, three runs each) and analysis (rule versions, fairness).
Reports are stored in `storage/reports/run_*/` (CSV + `report.json`); `results/` holds the observation table and the testing summary for the lab report.
The lab-manual metrics (data validation rate, workflow success rate, notification draft rate, test pass rate, average time per valid record,
human review completion rate) are in the Evaluation report page and in `results/evaluation_headline.json`.

Known limitation: instruction-like text is detected by patterns, so a paraphrased request with no trigger words is not flagged. It still cannot affect the score,
because free text is never read by the scoring step.
