"""Central configuration: paths, MCP server registry, statuses, users / roles and tunable Settings."""
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
    batches: Path = ROOT / "storage" / "batches"
    exports: Path = ROOT / "storage" / "exports"

    def ensure(self) -> "Paths":
        for p in (self.samples, self.uploads, self.eval_data, self.logs, self.reports, self.batches, self.exports):
            p.mkdir(parents=True, exist_ok=True)
        return self

    # files named in the lab manual
    @property
    def candidates_csv(self) -> Path: return self.root / "candidates.csv"
    @property
    def rules_json(self) -> Path: return self.root / "rules.json"
    @property
    def users_json(self) -> Path: return self.root / "users.json"
    @property
    def audit_csv(self) -> Path: return self.root / "audit_log.csv"
    @property
    def rejected_csv(self) -> Path: return self.root / "rejected_records.csv"
    @property
    def db(self) -> Path: return self.storage / "campushire.db"
    @property
    def trace_file(self) -> Path: return self.logs / "mcp_traces.jsonl"
    @property
    def execution_log(self) -> Path: return self.logs / "execution.log"
    @property
    def faults(self) -> Path: return self.logs / "faults.json"


PATHS = Paths().ensure()

# MCP servers: name -> module started with `python -m` and talked to over stdio.
SERVERS = {
    "ingest": {"module": "campushire.servers.ingest_server", "title": "Ingestion server", "purpose": "load, validate, normalise and import candidate records"},
    "scoring": {"module": "campushire.servers.scoring_server", "title": "Scoring server", "purpose": "rules, weighted scores, explanations, routing, rule comparison, fairness review"},
    "comms": {"module": "campushire.servers.comms_server", "title": "Communications server", "purpose": "notification drafts, edits and simulated dispatch with retry"},
    "records": {"module": "campushire.servers.records_server", "title": "Records server", "purpose": "drives, review decisions, audit log, SLA reminders and reports"},
}

ROLES_APPLIED = ["Junior Software Developer", "Data Analyst"]

PROPOSED_STATUSES = ["SHORTLIST_REVIEW", "MANUAL_REVIEW", "NOT_SHORTLISTED_REVIEW"]
REVIEW_ACTIONS = ["APPROVE", "EDIT", "HOLD", "REQUEST_INFO"]
FINAL_STATUSES = PROPOSED_STATUSES + ["REQUIRES_REVIEW", "ON_HOLD", "AWAITING_INFORMATION"]
NOTIFICATION_STATES = ["DRAFT", "APPROVED", "SENT", "FAILED", "NOT_SENT"]

# permission matrix (role-based access, variation 9)
PERMISSIONS = {
    "viewer": {"view"},
    "hr_reviewer": {"view", "review", "dispatch", "run"},
    "admin": {"view", "review", "dispatch", "run", "rules", "reset"},
}

DEFAULT_USERS = {
    "priya.reviewer": {"name": "Priya Nair", "role": "hr_reviewer"},
    "arjun.admin": {"name": "Arjun Rao", "role": "admin"},
    "meera.viewer": {"name": "Meera Iyer", "role": "viewer"},
}


def load_users() -> dict:
    try:
        data = json.loads(PATHS.users_json.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data:
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return dict(DEFAULT_USERS)


@dataclass(frozen=True)
class Settings:
    # execution strategy (variation 8)
    execution_mode: str = "parallel"        # sequential | batch | parallel
    chunk_size: int = 20                    # records per tools/call in batch and parallel mode
    max_parallel: int = 6
    # reliability
    retry_policy: str = "controlled"        # controlled | naive | none
    max_retries: int = 2
    retry_backoff_s: float = 0.05
    tool_timeout_s: float = 20.0
    # protocol
    validate_arguments: bool = True
    reuse_connections: bool = True
    # safety
    enforce_policy: bool = True
    # notifications (variation 6)
    notification_retries: int = 2
    # human review
    approval_mode: str = "manual"           # manual | auto (the evaluation harness simulates reviewers)
    # SLA (variation 5)
    sla_hours: int = 48
    # rules
    rule_version: str = ""                  # empty = active version in rules.json

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
