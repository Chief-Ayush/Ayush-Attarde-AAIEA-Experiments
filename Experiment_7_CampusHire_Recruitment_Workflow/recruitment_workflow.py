"""Validation, normalisation, duplicate detection, weighted scoring and routing (the lab manual's recruitment_workflow.py).

Pure functions with no I/O side effects beyond reading input files, so the MCP servers, the tests and the evaluation can all
use exactly the same logic.  Scores are computed with Decimal so that boundary values (75, 74.99, 50) are classified exactly.
"""
from __future__ import annotations

import csv
import io
import hashlib
import json
import math
import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from campushire.errors import InputError, SecurityError

REQUIRED_FIELDS = ["candidate_id", "candidate_name", "email", "skills_score", "experience_score", "assessment_score", "application_role"]
OPTIONAL_FIELDS = ["college", "cover_note"]
ALL_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS
SCORE_FIELDS = ["skills_score", "experience_score", "assessment_score"]
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")
EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[A-Za-z]{2,}$")
_NUM_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$", re.ASCII)
ROUTES = ["SHORTLIST_REVIEW", "MANUAL_REVIEW", "NOT_SHORTLISTED_REVIEW"]

# Candidate-provided text that tries to steer the system.  The text is never used for scoring; it is only flagged for the reviewer.
INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier)", r"disregard (all |any |the )?(previous|prior|above|rules?)",
    r"(override|change|modify|update|bypass|skip|reset) (the |all |any )?(scoring|score|rules?|weights?|thresholds?|criteria|validation)",
    r"(set|give|assign|make) (my|the|this|his|her) (score|rating|status)", r"(^|[.!?\n])\s*(system|assistant|admin|developer)\s*:",
    r"(shortlist|approve|hire|select|accept) (me|this candidate|him|her|immediately|automatically)", r"you (must|should|are to) (shortlist|approve|rank|score)",
    r"score (of )?100", r"(edit|change|modify|overwrite|replace|ignore|update) (the |your )?rules?\.json",
    r"(ignora|ignore|ignorez|ignorieren)\s+(las|les|die|todas)?\s*(instrucciones|instructions|anweisungen)", r"(preselecciona|pr[ée]s[ée]lectionnez|w[äa]hlen sie mich)",
    r"(maximum|full|top|highest) (marks|score|points|rating)", r"(best|top|number one|first) (applicant|candidate|choice)", r"(treat|consider|rank|rate) (me|this applicant|this candidate) as", r"as an? (ai|assistant|language model)", r"pretend (that|to be)", r"new (instructions?|rules?)",
    r"mark (me|this) as", r"<\s*/?\s*(system|instructions?)\s*>",
]
_INJ = [re.compile(p, re.I) for p in INJECTION_PATTERNS]


# --------------------------------------------------------------------------------------------------------------------- ingestion
_HEADER_ALIASES = {"candidateid": "candidate_id", "id": "candidate_id", "applicationid": "candidate_id", "candidatename": "candidate_name", "name": "candidate_name",
                   "email": "email", "emailaddress": "email", "mail": "email", "skillsscore": "skills_score", "skills": "skills_score",
                   "experiencescore": "experience_score", "experience": "experience_score", "assessmentscore": "assessment_score", "assessment": "assessment_score",
                   "applicationrole": "application_role", "role": "application_role", "college": "college", "covernote": "cover_note"}


def _header_alias(name: str) -> str:
    """'Candidate ID', 'E-mail', 'Skills Score' ... map to the documented column names; other names are kept as they are."""
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    return _HEADER_ALIASES.get(key, name.strip())


def load_candidates(file_path: str | Path) -> list[dict]:
    """Read candidate records from CSV or JSON.  Raises InputError for a missing, unreadable or malformed file or missing headers."""
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        raise InputError(f"File not found: {path.name}")
    try:
        if path.suffix.lower() == ".json":
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                data = data.get("candidates", data.get("records"))
            if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
                raise InputError("Malformed JSON: expected a list of candidate objects")
            records = [{k: ("" if v is None else v) for k, v in r.items()} for r in data]
            headers = sorted({k for r in records for k in r}) if records else []
        elif path.suffix.lower() in (".csv", ".txt"):
            raw = path.read_bytes()
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in raw:
                    raise                                              # UTF-16 and binary files are not guessed
                text = raw.decode("cp1252", errors="replace")        # Excel on Windows saves "CSV" in Windows-1252
            first = text.splitlines()[0] if text.strip() else ""
            delim = max([",", ";", "\t"], key=first.count) if first else ","
            reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delim)
            names = [h.strip() for h in (reader.fieldnames or [])]
            if not names:
                raise InputError("Malformed CSV: no header row")
            headers = [_header_alias(h) for h in names]
            dups = sorted({h for h in headers if h and headers.count(h) > 1})
            if dups:
                raise InputError("Malformed CSV: repeated column(s): " + ", ".join(dups))
            records = []
            for i, row in enumerate(reader, start=2):
                if None in row:                                   # more cells than headers
                    raise InputError(f"Malformed CSV: line {i} has more values than columns")
                if any(v is None for v in row.values()):          # fewer cells than headers
                    raise InputError(f"Malformed CSV: line {i} has fewer values than columns")
                if not any(str(v).strip() for v in row.values()):
                    continue                                      # blank line from a spreadsheet export
                records.append({headers[j]: v for j, v in enumerate(row.values())})
        else:
            raise InputError(f"Unsupported file type: {path.suffix or 'none'} (use .csv or .json)")
    except UnicodeDecodeError as exc:
        raise InputError(f"Malformed file: not valid UTF-8 text ({exc.reason})") from exc
    except json.JSONDecodeError as exc:
        raise InputError(f"Malformed JSON: {exc.msg} (line {exc.lineno})") from exc
    except csv.Error as exc:
        raise InputError(f"Malformed CSV: {exc}") from exc
    missing = [f for f in REQUIRED_FIELDS if f not in set(headers)]
    if missing:
        raise InputError("Missing column(s): " + ", ".join(missing))
    return records


# --------------------------------------------------------------------------------------------------------------------- normalisation
def _clean(value) -> str:
    text = "".join(ch for ch in str(value if value is not None else "") if unicodedata.category(ch) != "Cf")   # zero-width and other invisible characters
    return re.sub(r"\s+", " ", text).strip()


def _fold_text(text: str) -> str:
    """Text as the injection scan sees it: invisible characters removed, compatibility forms folded, simple digit-for-letter substitutions undone."""
    t = "".join(ch for ch in unicodedata.normalize("NFKC", str(text or "")) if unicodedata.category(ch) != "Cf")
    return t.translate(str.maketrans("013457@$", "oieastas"))


def email_key(email: str) -> str:
    """Identity of a mailbox: lower case, '+tag' removed, dots ignored for Gmail."""
    local, _, domain = email.lower().rpartition("@")
    local = local.split("+")[0]
    if domain in ("gmail.com", "googlemail.com"):
        local, domain = local.replace(".", ""), "gmail.com"
    return f"{local}@{domain}"


def valid_email(email: str) -> bool:
    if not EMAIL_RE.match(email):
        return False
    local, _, domain = email.rpartition("@")
    if local.startswith(".") or local.endswith(".") or ".." in local or ":" in local:
        return False
    return all(lab and not lab.startswith("-") and not lab.endswith("-") for lab in domain.split("."))


def _role_tokens(value: str) -> frozenset:
    words = re.sub(r"[^a-z0-9]+", " ", value.lower()).split()
    return frozenset({"jr": "junior", "sr": "senior"}.get(w, w) for w in words)


def canonical_role(value: str, known_roles: list[str] | None = None) -> str:
    v = _clean(value)
    for r in known_roles or []:
        if v.lower() == r.lower():
            return r
    for r in known_roles or []:
        if v and _role_tokens(v) == _role_tokens(r):          # "Jr Software Developer", "Software Developer, Junior", "Data-Analyst"
            return r
    return v


def normalize_candidate(raw: dict, known_roles: list[str] | None = None) -> dict:
    """Trim whitespace, lower-case the e-mail, collapse spaces, canonicalise the role.  Scores stay as the cleaned input text."""
    c = {f: _clean(raw.get(f, "")) for f in ALL_FIELDS}
    c["candidate_id"] = c["candidate_id"].upper()
    c["email"] = c["email"].lower()
    c["application_role"] = canonical_role(c["application_role"], known_roles)
    c["cover_note"] = str(raw.get("cover_note", "") or "").strip()[:2000]
    for f in SCORE_FIELDS:                                            # "85%" and "+85" become "85"; anything else stays as typed and fails validation
        try:
            c[f] = format(to_decimal(c[f]), "f")
        except ValueError:
            pass
    return c


def record_hash(c: dict) -> str:
    """Content hash of the normalised record (used for repeat-import / idempotency detection).  The cover note is part of the content."""
    payload = json.dumps({f: c.get(f, "") for f in ALL_FIELDS}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def to_decimal(value) -> Decimal:
    """Plain decimal numbers only (optional sign, ASCII digits, one point, optional trailing %).  No exponents, separators or other scripts."""
    text = str(value).strip()
    if text.endswith("%"):
        text = text[:-1].strip()
    if not _NUM_RE.match(text):
        raise ValueError("not a number")
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ValueError("not a number") from None


# --------------------------------------------------------------------------------------------------------------------- validation
def validate_candidate(candidate: dict, known_roles: list[str] | None = None) -> tuple[bool, str, str]:
    """Return (valid, message, code).  Codes: OK, MISSING_FIELD, INVALID_NUMBER, OUT_OF_RANGE, INVALID_EMAIL, INVALID_ID, UNKNOWN_ROLE."""
    for field in REQUIRED_FIELDS:
        if not _clean(candidate.get(field, "")):
            return False, f"Missing field: {field}", "MISSING_FIELD"
    if not ID_RE.match(candidate["candidate_id"]):
        return False, "Invalid candidate_id (letters, digits, - and _ only, at most 20 characters)", "INVALID_ID"
    if len(candidate["candidate_name"]) > 100:
        return False, "candidate_name is longer than 100 characters", "INVALID_FIELD"
    for field in SCORE_FIELDS:
        try:
            score = to_decimal(candidate[field])
        except ValueError:
            return False, f"Invalid numeric value: {field}", "INVALID_NUMBER"
        if not Decimal(0) <= score <= Decimal(100):
            return False, f"{field} must be between 0 and 100", "OUT_OF_RANGE"
    if not valid_email(candidate["email"]):
        return False, "Invalid email format", "INVALID_EMAIL"
    if known_roles is not None and candidate["application_role"] not in known_roles:
        return False, f"Unknown application_role: {candidate['application_role'][:40]}", "UNKNOWN_ROLE"
    return True, "Valid record", "OK"


def scan_text(text: str) -> list[str]:
    """Names of the steering patterns found in free text (empty list = clean)."""
    folded = _fold_text(text)
    return [p.pattern for p in _INJ if p.search(text or "") or p.search(folded)]


NEXT_ACTION = {
    "MISSING_FIELD": "Ask the candidate or the source system for the missing field", "INVALID_NUMBER": "Correct the score in the source file",
    "OUT_OF_RANGE": "Correct the score in the source file", "INVALID_EMAIL": "Obtain a valid contact address through the approved process",
    "INVALID_ID": "Assign a valid application reference", "INVALID_FIELD": "Correct the field in the source file", "UNKNOWN_ROLE": "HR to confirm which role the candidate applied for",
    "DUPLICATE_REPEAT": "No action: identical repeat of an earlier import", "DUPLICATE_CONFLICT": "HR to decide which version is current (updated application or error)",
}


def validate_batch(records: list[dict], known_roles: list[str] | None = None, existing: dict[str, str] | None = None) -> dict:
    """Validate and normalise a list of raw records.

    `existing` maps candidate_id -> record hash already imported into the drive.  Nothing is silently dropped: every record ends up in
    `valid`, `skipped` (already imported, identical) or `rejected` with a reason code and a suggested next action.
    """
    existing = existing or {}
    normed = [normalize_candidate(r, known_roles) for r in records]
    by_id: dict[str, list[int]] = {}
    for i, c in enumerate(normed):
        by_id.setdefault(c["candidate_id"], []).append(i)
    valid, rejected, skipped, warnings = [], [], [], []
    emails: dict[str, str] = {}
    for i, c in enumerate(normed):
        raw_row = i + 1
        cid = c["candidate_id"]
        ok, msg, code = validate_candidate(c, known_roles)
        h = record_hash(c)
        group = by_id.get(cid, [])
        dup_note = None
        if cid and len(group) > 1:
            hashes = {record_hash(normed[j]) for j in group}
            if len(hashes) > 1:
                ok, msg, code = False, f"Duplicate candidate_id {cid} with different data ({len(group)} rows) - possible updated application", "DUPLICATE_CONFLICT"
            elif i != group[0]:
                ok, msg, code = False, f"Duplicate candidate_id {cid}: identical to row {group[0] + 1} (repeated import)", "DUPLICATE_REPEAT"
        if ok and cid in existing:
            if existing[cid] == h:
                skipped.append({"candidate_id": cid, "row": raw_row, "reason": "Already imported with identical data (repeat input ignored)"})
                continue
            ok, msg, code = False, f"candidate_id {cid} already exists in this drive with different data - possible updated application", "DUPLICATE_CONFLICT"
        if not ok:
            rejected.append({"row": raw_row, "candidate_id": cid, "candidate_name": c["candidate_name"], "code": code, "reason": msg,
                             "next_action": NEXT_ACTION.get(code, "HR to review"), "record_hash": h,
                             "duplicate": code.startswith("DUPLICATE"), "raw": c})
            continue
        flags = []
        pats = scan_text(c["cover_note"]) + scan_text(c["candidate_name"]) + scan_text(c["college"])
        if pats:
            flags.append({"flag": "TEXT_INSTRUCTION", "detail": "The free text tries to give instructions to the system; it was ignored and the scoring rules were not changed."})
        ek = email_key(c["email"])
        if ek in emails and emails[ek] != cid:
            flags.append({"flag": "SAME_EMAIL", "detail": f"Same mailbox as {emails[ek]} (e-mail address or an alias of it) - possible duplicate applicant, please check."})
            warnings.append({"candidate_id": cid, "warning": f"same email as {emails[ek]}"})
        emails.setdefault(ek, cid)
        c["record_hash"] = h
        c["flags"] = flags
        valid.append(c)
    return {"valid": valid, "rejected": rejected, "skipped": skipped, "warnings": warnings,
            "counts": {"total": len(records), "valid": len(valid), "rejected": len(rejected), "skipped": len(skipped),
                       "duplicates": sum(1 for r in rejected if r["duplicate"])}}


# --------------------------------------------------------------------------------------------------------------------- rules
def validate_rules(cfg: dict) -> None:
    """Raise InputError if the scoring configuration is missing, malformed or uses an undocumented criterion."""
    if not isinstance(cfg, dict) or not isinstance(cfg.get("versions"), dict) or not cfg["versions"]:
        raise InputError("Malformed rules: 'versions' is missing")
    catalogue = cfg.get("criteria_catalogue") or {}
    for ver, body in cfg["versions"].items():
        roles = (body or {}).get("roles")
        if not isinstance(roles, dict) or not roles:
            raise InputError(f"Malformed rules: version {ver} has no roles")
        for role, rr in roles.items():
            crit, thr = (rr or {}).get("criteria"), (rr or {}).get("thresholds")
            if not isinstance(crit, dict) or not crit:
                raise InputError(f"Malformed rules: {ver}/{role} has no criteria")
            for k, w in crit.items():
                if k not in catalogue or k not in SCORE_FIELDS:
                    raise InputError(f"Criterion '{k}' is not a documented job-related criterion ({ver}/{role})")
                if isinstance(w, bool) or not isinstance(w, (int, float)) or w < 0:
                    raise InputError(f"Weight for '{k}' must be a non-negative number ({ver}/{role})")
            if abs(sum(crit.values()) - 100) > 1e-9:
                raise InputError(f"Weights must add up to 100 ({ver}/{role} adds up to {sum(crit.values())})")
            if not isinstance(thr, dict) or not {"shortlist_review", "manual_review"} <= set(thr):
                raise InputError(f"Malformed rules: thresholds missing ({ver}/{role})")
            if not 0 <= thr["manual_review"] < thr["shortlist_review"] <= 100:
                raise InputError(f"Thresholds must satisfy 0 <= manual_review < shortlist_review <= 100 ({ver}/{role})")
    active = cfg.get("active_version") or cfg.get("rule_version")
    if active not in cfg["versions"]:
        raise InputError(f"Active rule version '{active}' is not defined")


def load_rules(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise InputError("Rules file not found: " + p.name)
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InputError(f"Malformed rules file: {exc.msg} (line {exc.lineno})") from exc
    validate_rules(cfg)
    return cfg


def rules_for(cfg: dict, role: str, version: str | None = None) -> tuple[str, dict]:
    ver = version or cfg.get("active_version") or cfg.get("rule_version")
    if ver not in cfg["versions"]:
        raise InputError(f"Unknown rule version: {ver}")
    roles = cfg["versions"][ver]["roles"]
    if role not in roles:
        raise InputError(f"No rules for role '{role}' in version {ver}")
    return ver, roles[role]


# --------------------------------------------------------------------------------------------------------------------- scoring
def _q(d: Decimal) -> Decimal:
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_score(candidate: dict, cfg: dict, version: str | None = None) -> dict:
    """Weighted score out of 100 with a criterion-wise explanation.  Only the documented numeric criteria are read."""
    ver, rr = rules_for(cfg, candidate["application_role"], version)
    catalogue = cfg.get("criteria_catalogue", {})
    parts, total = [], Decimal(0)
    for crit, weight in rr["criteria"].items():
        value = to_decimal(candidate[crit])
        if not Decimal(0) <= value <= Decimal(100):
            raise InputError(f"{crit} must be between 0 and 100")
        contribution = value * Decimal(str(weight)) / Decimal(100)
        total += contribution
        parts.append({"criterion": crit, "label": catalogue.get(crit, {}).get("label", crit), "input": float(value), "weight": float(weight),
                      "contribution": float(_q(contribution)), "max": float(weight)})
    score = _q(total)
    if not Decimal(0) <= score <= Decimal(100):
        raise InputError("Computed score outside 0-100")
    return {"candidate_id": candidate["candidate_id"], "role": candidate["application_role"], "score": float(score), "breakdown": parts,
            "rule_version": ver, "thresholds": rr["thresholds"], "_exact": str(total)}


def route_candidate(score, thresholds: dict | None = None) -> str:
    """75-100 SHORTLIST_REVIEW, 50-74.99 MANUAL_REVIEW, below 50 NOT_SHORTLISTED_REVIEW (thresholds come from rules.json)."""
    thr = thresholds or {"shortlist_review": 75, "manual_review": 50}
    s = Decimal(str(score))
    if s >= Decimal(str(thr["shortlist_review"])):
        return "SHORTLIST_REVIEW"
    if s >= Decimal(str(thr["manual_review"])):
        return "MANUAL_REVIEW"
    return "NOT_SHORTLISTED_REVIEW"


def explain(result: dict) -> str:
    lines = [f"Candidate ID: {result['candidate_id']}", f"Overall Score: {result['score']:g}/100"]
    lines += [f"{p['label']} contribution: {p['contribution']:g}/{p['max']:g}" for p in result["breakdown"]]
    lines.append(f"Rule Version: {result['rule_version']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------------------------------- review actions
def review_action(proposed_status: str, action: str, new_status: str | None = None) -> str:
    """APPROVE keeps the proposal, EDIT applies the reviewer's documented change (REQUIRES_REVIEW when none is given), HOLD pauses,
    REQUEST_INFO waits for more information."""
    if action == "APPROVE":
        return proposed_status
    if action == "EDIT":
        return new_status if new_status in ROUTES else "REQUIRES_REVIEW"
    if action == "HOLD":
        return "ON_HOLD"
    if action == "REQUEST_INFO":
        return "AWAITING_INFORMATION"
    raise InputError("Invalid review action")


def csv_safe(value) -> str:
    """Neutralise spreadsheet formula injection when text is written to CSV (a leading = + - @ tab or CR makes Excel run it)."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s
