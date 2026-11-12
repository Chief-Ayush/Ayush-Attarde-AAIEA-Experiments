"""Central configuration: paths, MCP server registry, roles and tunable Settings."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Paths:
    root: Path = ROOT
    data: Path = ROOT / "data"
    samples: Path = ROOT / "data" / "samples"
    uploads: Path = ROOT / "data" / "uploads"
    eval_data: Path = ROOT / "data" / "eval"
    storage: Path = ROOT / "storage"
    logs: Path = ROOT / "storage" / "mcp_logs"
    reports: Path = ROOT / "storage" / "reports"
    exports: Path = ROOT / "storage" / "exports"

    def ensure(self) -> "Paths":
        for p in (self.samples, self.uploads, self.eval_data, self.logs, self.reports, self.exports):
            p.mkdir(parents=True, exist_ok=True)
        return self

    # files named in the lab manual
    @property
    def queries_csv(self) -> Path: return self.root / "customer_queries.csv"
    @property
    def kb_json(self) -> Path: return self.root / "knowledge_base.json"
    @property
    def config_json(self) -> Path: return self.root / "config.json"
    @property
    def users_json(self) -> Path: return self.root / "users.json"
    @property
    def audit_csv(self) -> Path: return self.root / "audit_log.csv"
    @property
    def notification_csv(self) -> Path: return self.root / "notification_log.csv"
    @property
    def rejected_csv(self) -> Path: return self.root / "rejected_queries.csv"
    @property
    def db(self) -> Path: return self.root / "tickets.db"
    @property
    def trace_file(self) -> Path: return self.logs / "mcp_traces.jsonl"
    @property
    def execution_log(self) -> Path: return self.logs / "execution.log"
    @property
    def faults(self) -> Path: return self.logs / "faults.json"


PATHS = Paths().ensure()

SERVERS = {
    "intake": {"module": "supportdesk.mcp.servers.intake_server", "title": "Intake server", "purpose": "load, validate and store customer queries"},
    "knowledge": {"module": "supportdesk.mcp.servers.knowledge_server", "title": "Knowledge server", "purpose": "classify queries, search the approved knowledge base, choose a route"},
    "tickets": {"module": "supportdesk.mcp.servers.tickets_server", "title": "Tickets server", "purpose": "tickets, agent actions, queues, SLA monitoring, audit log"},
    "comms": {"module": "supportdesk.mcp.servers.comms_server", "title": "Communications server", "purpose": "notification drafts, simulated delivery with retry and dead-letter queue"},
}

INTENTS = ["Login / Access", "Billing", "Technical Error", "Feature Request", "General Information", "Unresolved Issue"]
COMPLEXITY = ["Low", "Medium", "High"]
PRIORITIES = ["P1", "P2", "P3", "P4"]
ROUTES = ["URGENT_HUMAN_ESCALATION", "TECHNICAL_HUMAN_REVIEW", "CREATE_TICKET_AND_ESCALATE", "PROPOSE_AUTO_RESOLUTION",
          "HUMAN_REVIEW", "PRODUCT_FEEDBACK_QUEUE", "REQUEST_INFORMATION_OR_ESCALATE"]
REVIEW_ACTIONS = ["APPROVE", "EDIT", "HOLD", "REQUEST_INFORMATION", "ESCALATE", "RESOLVE"]
TICKET_STATES = ["OPEN", "PENDING_APPROVAL", "IN_REVIEW", "APPROVED_FOR_RESPONSE", "ESCALATED", "ON_HOLD", "AWAITING_CUSTOMER", "RESOLVED", "CLOSED", "REOPENED"]

PERMISSIONS = {
    "viewer": {"view"},
    "agent": {"view", "review", "dispatch", "run"},
    "lead": {"view", "review", "dispatch", "run", "reassign", "clock"},
    "admin": {"view", "review", "dispatch", "run", "reassign", "clock", "reset", "settings"},
}

DEFAULT_USERS = {
    "riya.agent": {"name": "Riya Menon", "role": "agent", "team": "Billing Support"},
    "dev.agent": {"name": "Dev Kulkarni", "role": "agent", "team": "Technical Support"},
    "sana.lead": {"name": "Sana Qureshi", "role": "lead", "team": "Incident Response"},
    "arjun.admin": {"name": "Arjun Rao", "role": "admin", "team": "Tier 1 Support"},
    "meera.viewer": {"name": "Meera Iyer", "role": "viewer", "team": ""},
}


def load_users() -> dict:
    try:
        data = json.loads(PATHS.users_json.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data:
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return dict(DEFAULT_USERS)


def load_policy() -> dict:
    """config.json - SLA targets and thresholds (the file named in the manual)."""
    base = {"sla_minutes": {"P1": 15, "P2": 60, "P3": 240, "P4": 1440},
            "resolution_minutes": {"P1": 240, "P2": 480, "P3": 1440, "P4": 4320},
            "retry_attempts": 3, "knowledge_match_threshold": 70, "review_threshold": 40,
            "human_review_required_for_high_risk": True, "at_risk_fraction": 0.75, "auto_dispatch_min_score": 70}
    try:
        base.update(json.loads(PATHS.config_json.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        pass
    return base


@dataclass(frozen=True)
class Settings:
    execution_mode: str = "parallel"        # sequential | batch | parallel
    chunk_size: int = 25
    max_parallel: int = 6
    classifier: str = "learned"             # learned | rules
    retrieval: str = "semantic"             # semantic | keyword
    retry_policy: str = "controlled"        # controlled | naive | none
    max_retries: int = 2
    retry_backoff_s: float = 0.05
    tool_timeout_s: float = 25.0
    validate_arguments: bool = True
    reuse_connections: bool = True
    enforce_policy: bool = True
    auto_guard: bool = True                 # evidence checks before an answer may be proposed for automatic sending
    auto_dispatch: bool = True              # send low-risk KB answers without waiting for an agent
    notification_attempts: int = 3
    scheduling: str = "priority"            # priority | fifo  (how agents pick the next ticket)
    aging: bool = True                      # promote tickets that wait too long

    def with_(self, **kw) -> "Settings":
        return replace(self, **{k: v for k, v in kw.items() if v is not None})

    def to_dict(self) -> dict:
        return asdict(self)


PREFS_FILE = PATHS.storage / "settings.json"


def load_settings() -> Settings:
    try:
        data = json.loads(PREFS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    names = {f.name for f in fields(Settings)}
    return Settings(**{k: v for k, v in data.items() if k in names})


def save_settings(s: Settings) -> None:
    PREFS_FILE.write_text(json.dumps(s.to_dict(), indent=1), encoding="utf-8")
