from datetime import datetime

from config import REASON_MAP, RISK_LEVELS, RISK_THRESHOLDS, RISK_SETTINGS, RULE_WEIGHTS
from engine.security import inspect_transaction
from engine.validators import validate_transaction


def _classify_risk(score):
    if score <= RISK_THRESHOLDS["LEGITIMATE_MAX"]:
        return RISK_LEVELS["LEGITIMATE"]
    if score <= RISK_THRESHOLDS["SUSPICIOUS_MAX"]:
        return RISK_LEVELS["SUSPICIOUS"]
    return RISK_LEVELS["HIGH_RISK"]


def _is_unusual_time(timestamp):
    if not timestamp:
        return False
    try:
        dt = datetime.strptime(str(timestamp), "%Y-%m-%d %H:%M:%S")
        hour = dt.hour
        start = RISK_SETTINGS["unusual_hour_start"]
        end = RISK_SETTINGS["unusual_hour_end"]
        return hour >= start and hour <= end
    except ValueError:
        return False


def _location_changed(previous_location, current_location):
    return bool(previous_location and current_location and str(previous_location).strip() != str(current_location).strip())


def assess_transaction(transaction):
    security = inspect_transaction(transaction)
    validation = validate_transaction(transaction)
    if not validation["is_valid"]:
        return {
            "transaction_id": transaction.get("transaction_id"),
            "risk_score": 0,
            "risk_level": "LEGITIMATE",
            "triggered_rules": [],
            "reason_list": [],
            "validation_errors": validation["errors"],
            "security_findings": security,
            "status": "INVALID",
        }

    txn = validation["normalized_transaction"]
    risk_score = 0
    triggered_rules = []

    if float(txn.get("amount", 0)) > RISK_SETTINGS["amount_threshold"]:
        risk_score += RULE_WEIGHTS["HIGH_AMOUNT"]
        triggered_rules.append("HIGH_AMOUNT")

    if _is_unusual_time(txn.get("timestamp")):
        risk_score += RULE_WEIGHTS["UNUSUAL_TIME"]
        triggered_rules.append("UNUSUAL_TIME")

    if _location_changed(txn.get("previous_location"), txn.get("location")):
        risk_score += RULE_WEIGHTS["LOCATION_CHANGE"]
        triggered_rules.append("LOCATION_CHANGE")

    if int(txn.get("transactions_last_10min", 0)) >= RISK_SETTINGS["velocity_threshold"]:
        risk_score += RULE_WEIGHTS["HIGH_VELOCITY"]
        triggered_rules.append("HIGH_VELOCITY")

    if bool(txn.get("is_new_merchant")):
        risk_score += RULE_WEIGHTS["NEW_MERCHANT"]
        triggered_rules.append("NEW_MERCHANT")

    if int(txn.get("failed_attempts", 0)) >= RISK_SETTINGS["failed_attempt_threshold"]:
        risk_score += RULE_WEIGHTS["MULTIPLE_FAILED_ATTEMPTS"]
        triggered_rules.append("MULTIPLE_FAILED_ATTEMPTS")

    risk_level = _classify_risk(risk_score)
    reason_list = [REASON_MAP[rule] for rule in triggered_rules]

    return {
        "transaction_id": txn.get("transaction_id"),
        "risk_score": risk_score,
        "risk_level": risk_level,
        "triggered_rules": triggered_rules,
        "reason_list": reason_list,
        "validation_errors": [],
        "security_findings": security,
        "status": "OK",
    }
