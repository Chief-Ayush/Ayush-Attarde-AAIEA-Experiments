"""Prompt-injection detection and safe handling for untrusted transaction text."""

import base64
import binascii
import re
import unicodedata
from typing import Any


_INJECTION_PATTERNS = {
    "direct_override": re.compile(
        r"\b(ignore|disregard|override|bypass|forget)\b.{0,80}\b(instruction|rule|decision|classification|previous|system)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "role_impersonation": re.compile(
        r"\b(you are now|act as|pretend to be|developer message|system message|admin mode)\b",
        re.IGNORECASE,
    ),
    "system_prompt_extraction": re.compile(
        r"\b(reveal|show|print|repeat|expose)\b.{0,80}\b(system prompt|hidden prompt|instructions|policy|secret)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "delimiter_injection": re.compile(
        r"(<\/?system>|<\/?assistant>|<\/?user>|\[\[?system\]?\]|```(?:system|assistant|developer))",
        re.IGNORECASE,
    ),
    "indirect_instruction": re.compile(
        r"\b(when you read this|follow these instructions|important instruction|do not tell the user|mark as safe|approve this transaction)\b",
        re.IGNORECASE,
    ),
    "tool_or_code_execution": re.compile(
        r"\b(run|execute|call|invoke)\b.{0,60}\b(tool|function|shell|python|sql|command)\b",
        re.IGNORECASE | re.DOTALL,
    ),
}


def _decoded_variants(text: str) -> list[str]:
    """Return likely decoded forms for detecting simple encoded instructions."""
    variants = []
    compact = re.sub(r"\s+", "", text)
    if len(compact) >= 16 and re.fullmatch(r"[A-Za-z0-9+/=]+", compact):
        try:
            decoded = base64.b64decode(compact, validate=True).decode("utf-8", errors="ignore")
            if decoded.strip():
                variants.append(decoded)
        except (binascii.Error, ValueError):
            pass
    return variants


def inspect_untrusted_text(value: Any) -> list[str]:
    """Return injection categories found in a value, without executing it."""
    if value is None:
        return []
    text = unicodedata.normalize("NFKC", str(value))
    decoded = _decoded_variants(text)
    candidates = [text, *decoded]
    findings = set()
    for candidate in candidates:
        for category, pattern in _INJECTION_PATTERNS.items():
            if pattern.search(candidate):
                findings.add(category)
    if decoded and any(
        pattern.search(candidate)
        for candidate in decoded
        for pattern in _INJECTION_PATTERNS.values()
    ):
        findings.add("obfuscation")
    return sorted(findings)


def inspect_transaction(transaction: dict[str, Any]) -> dict[str, Any]:
    """Inspect every transaction value as data and return an audit-friendly result."""
    findings = {}
    for field, value in transaction.items():
        field_findings = inspect_untrusted_text(value)
        if field_findings:
            findings[field] = field_findings
    return {
        "is_suspicious": bool(findings),
        "findings": findings,
        "finding_count": sum(len(categories) for categories in findings.values()),
    }


def safe_text(value: Any, max_length: int = 160) -> str:
    """Bound and neutralize control characters before text reaches a report prompt."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = "".join(char if char.isprintable() else " " for char in text)
    return re.sub(r"\s+", " ", text).strip()[:max_length]
