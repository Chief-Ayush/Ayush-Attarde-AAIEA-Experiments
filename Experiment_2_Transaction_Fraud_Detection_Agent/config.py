RISK_THRESHOLDS = {
    "LEGITIMATE_MAX": 29,
    "SUSPICIOUS_MAX": 59,
    "HIGH_RISK_MAX": 100,
}

RULE_WEIGHTS = {
    "HIGH_AMOUNT": 25,
    "UNUSUAL_TIME": 15,
    "LOCATION_CHANGE": 20,
    "HIGH_VELOCITY": 20,
    "NEW_MERCHANT": 10,
    "MULTIPLE_FAILED_ATTEMPTS": 10,
}

RISK_SETTINGS = {
    "amount_threshold": 5000,
    "unusual_hour_start": 0,
    "unusual_hour_end": 5,
    "velocity_threshold": 4,
    "failed_attempt_threshold": 2,
    "high_amount_multiplier": 2.0,
}

REASON_MAP = {
    "HIGH_AMOUNT": "High transaction amount",
    "UNUSUAL_TIME": "Unusual transaction time",
    "LOCATION_CHANGE": "Location change",
    "HIGH_VELOCITY": "High transaction velocity",
    "NEW_MERCHANT": "New merchant",
    "MULTIPLE_FAILED_ATTEMPTS": "Multiple failed attempts",
}

RISK_LEVELS = {
    "LEGITIMATE": "LEGITIMATE",
    "SUSPICIOUS": "SUSPICIOUS",
    "HIGH_RISK": "HIGH_RISK",
}
