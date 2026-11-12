from datetime import datetime


def _coerce_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if value is None:
        return False
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "t"}:
        return True
    if text in {"false", "0", "no", "n", "f", ""}:
        return False
    return bool(value)


def validate_transaction(txn):
    errors = []
    normalized = dict(txn)

    required_fields = [
        "transaction_id",
        "timestamp",
        "amount",
        "merchant",
        "location",
        "previous_location",
        "payment_method",
        "device_id",
        "transactions_last_10min",
        "failed_attempts",
        "is_new_merchant",
    ]

    for field in required_fields:
        if field not in normalized or normalized[field] in (None, ""):
            errors.append(f"Missing required field: {field}")

    if "amount" in normalized:
        try:
            amount = float(normalized["amount"])
            if amount <= 0:
                errors.append("amount must be greater than zero")
            normalized["amount"] = amount
        except (TypeError, ValueError):
            errors.append("amount must be numeric")

    if "transactions_last_10min" in normalized:
        try:
            normalized["transactions_last_10min"] = int(normalized["transactions_last_10min"])
        except (TypeError, ValueError):
            errors.append("transactions_last_10min must be numeric")

    if "failed_attempts" in normalized:
        try:
            normalized["failed_attempts"] = int(normalized["failed_attempts"])
        except (TypeError, ValueError):
            errors.append("failed_attempts must be numeric")

    if "timestamp" in normalized and normalized["timestamp"] not in (None, ""):
        try:
            datetime.strptime(str(normalized["timestamp"]), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            errors.append("timestamp must be in YYYY-MM-DD HH:MM:SS format")

    if "is_new_merchant" in normalized:
        try:
            normalized["is_new_merchant"] = _coerce_bool(normalized["is_new_merchant"])
        except (TypeError, ValueError):
            errors.append("is_new_merchant must be boolean")

    return {"is_valid": len(errors) == 0, "errors": errors, "normalized_transaction": normalized}
