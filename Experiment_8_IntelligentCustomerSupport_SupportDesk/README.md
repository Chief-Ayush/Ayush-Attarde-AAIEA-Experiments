# TaskNest SupportDesk (Experiment 8)

Intelligent customer support with escalation for a fictional project-management SaaS. Queries are validated, classified (rules or a learned model), matched to approved
knowledge-base articles with a confidence score and an evidence guard, routed to automatic answer / agent review / urgent escalation, tracked as tickets with P1-P4 queues,
SLA clocks and priority aging, and notified through a simulated gateway with backoff, idempotency keys and a dead-letter queue. 32 MCP tools on four servers, a LangGraph
workflow with a human-review interrupt, an append-only hash-chained audit log, and a Streamlit agent dashboard.

## Run
    pip install -r requirements.txt
    streamlit run app.py                    # agent dashboard (14 pages)
    python -m supportdesk.cli run           # console run of customer_queries.csv
    python -m supportdesk.cli evaluate      # full evaluation, report in storage/reports/
    pytest -q test_support.py               # unit and MCP tests

The first start fills the desk with a demo working day (56 tickets from `data/samples/demo_queries.csv`: answered, waiting for the customer, reopened, at risk, breached, a retried and a dead-lettered message). It runs once; set `SUPPORTDESK_NO_DEMO=1` to start empty, or delete `tickets.db` to start over.

## Layout
- classifier.py, resolver.py, workflow_router.py, ticket_manager.py, sla_monitor.py, notifications.py, query_ingestion.py: domain modules named as in the lab manual
- supportdesk/agents/: one module per agent (intake, triage, knowledge, router, ticketing, notifier, review, sla, reporter), plus base.py (state, runner), policy.py and catalog.py
- supportdesk/mcp/: client.py (MCP hub) and servers/ (intake, knowledge, tickets, comms FastMCP servers)
- supportdesk/demo.py (first-start demo data), supportdesk/graph.py (LangGraph wiring), workflow.py (start / resume / run_batch), store.py (SQLite), rbac.py, evaluation/
- views/, ui/: Streamlit pages (views/ rather than pages/, so every page address opens through app.py) and shared design layer
- knowledge_base.json, config.json (SLA targets, thresholds), users.json, customer_queries.csv, data/ (training queries, samples), tools/ (data generators)

Sign in as an agent, lead, admin or viewer from the sidebar (users.json). Delivery is simulated; no external service or key is used.
