"""Integration tests: the MCP servers, the LangGraph workflow and the audit log working together.  Run:  python -m pytest tests -q"""
import pytest

from campushire import store
from campushire.evaluation import suites as S
from campushire.mcp_client import MCPHub


@pytest.fixture(scope="module")
def env():
    e = S.Env(MCPHub())
    yield e
    e.hub.close()


def test_all_four_servers_expose_typed_tools(env):
    tools = {t["name"]: t for t in env.hub.discover()}
    assert len(tools) >= 25 and all(t["output_schema"] for t in tools.values())
    assert {t["server"] for t in tools.values()} == {"ingest", "scoring", "comms", "records"}


def test_end_to_end_ten_records(env):
    did, res = env.run(env.call("load_source", {"path": "data/samples/candidates_10.csv"}).data["records"], "pytest")
    st = res["state"]
    assert st["status"] == "completed" and st["counts"]["valid"] == 10
    assert store.verify_audit_chain()["ok"]
    assert {d["state"] for d in store.drafts(did)} == {"SENT"}


def test_nothing_is_sent_before_a_decision(env):
    wf, did = env.prepare([S.rec()], "pytest-gate")
    assert wf.pending() is not None
    assert [d["state"] for d in store.drafts(did)] == ["DRAFT"]


def test_viewer_cannot_decide(env):
    wf, did = env.prepare([S.rec()], "pytest-rbac")
    r = env.call("record_review", {"drive_id": did, "candidate_id": "T001", "action": "APPROVE", "reviewer": S.VIEWER})
    assert not r.ok and r.category == "SECURITY"


@pytest.mark.parametrize("suite", ["functional", "hitl"])
def test_evaluation_suites_pass(env, suite):
    df = getattr(S, f"run_{suite}")(env)
    assert (df["result"] == "PASS").all(), df[df["result"] != "PASS"][["id", "observed"]].to_string()
