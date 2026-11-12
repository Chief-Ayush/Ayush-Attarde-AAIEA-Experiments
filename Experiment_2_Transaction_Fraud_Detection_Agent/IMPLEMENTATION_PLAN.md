# Transaction Fraud Detection Agent — Deterministic Build Plan

## 1. Goal

Build a fraud-detection project with deterministic risk decisions and an optional LLM narrative layer. The system analyzes a transaction or dataset, computes a trusted fraud risk score, classifies the outcome, and generates summaries and reports without allowing an LLM to alter the decision.

This version is designed to be:

- Deterministic
- Explainable
- Easy to test
- Defense-in-depth prompt-injection detection around every untrusted text field
- Suitable for local development without external API keys

---

## 2. Core Principle

The central design decision is:

> The engine decides. The explanation follows the engine.

This means:

- Transaction data is validated
- Fraud indicators are evaluated using fixed logic
- A risk score is calculated
- A risk label is assigned
- Explanations are generated from the triggered rules
- No external model is allowed to override or reinterpret the result

---

## 3. Functional Scope

The build will implement the following required behaviors from the README:

- Accept a single transaction
- Accept a batch of transactions
- Validate required fields
- Normalize input values
- Detect fraud indicators
- Compute a risk score
- Classify into `LEGITIMATE`, `SUSPICIOUS`, or `HIGH_RISK`
- Generate reason strings based on triggered rules
- Produce summary statistics for datasets
- Save audit logs locally
- Support basic visualizations
- Run unit tests for logic correctness
- Generate JSON summaries and Markdown reports
- Generate one optional LLM narrative per batch
- Record injection findings without changing the risk decision

The LLM remains optional and this build does not include:

- Model-based fraud classification
- Model access without explicit `FRAUD_LLM_API_KEY` configuration

---

## 4. Project Structure

```text
transaction-fraud-agent/
├── README.md
├── IMPLEMENTATION_PLAN.md
├── requirements.txt
├── main.py
├── config.py
├── app/
│   ├── __init__.py
│   ├── transaction_service.py
│   ├── risk_service.py
│   ├── report_service.py
│   └── audit_service.py
├── agents/
│   ├── __init__.py
│   ├── transaction_agent.py
│   ├── risk_agent.py
│   ├── explanation_agent.py
│   ├── guardrail_agent.py
│   └── audit_agent.py
├── engine/
│   ├── __init__.py
│   ├── fraud_rules.py
│   ├── risk_score.py
│   ├── validators.py
│   └── classification.py
├── data/
│   ├── __init__.py
│   ├── generator.py
│   ├── loader.py
│   ├── transactions_demo.csv
│   ├── transactions_small.csv
│   └── transactions_large.csv
├── analysis/
│   ├── __init__.py
│   ├── batch_analysis.py
│   ├── statistics.py
│   └── historical_analysis.py
├── visualization/
│   ├── __init__.py
│   ├── risk_distribution.py
│   ├── score_distribution.py
│   └── transaction_trends.py
├── logging_utils/
│   ├── __init__.py
│   └── audit_logger.py
├── outputs/
│   ├── reports/
│   ├── charts/
│   └── logs/
├── tests/
│   ├── test_validation.py
│   ├── test_rules.py
│   ├── test_risk_score.py
│   ├── test_dataset.py
│   ├── test_batch_analysis.py
│   └── test_integration.py
└── .gitignore
```

---

## 5. Core Workflow

```text
Transaction Input
   ↓
Transaction Validation
   ↓
Feature Extraction
   ↓
Fraud Rule Evaluation
   ↓
Risk Score Calculation
   ↓
Risk Classification
   ↓
Explanation Generation from Rules
   ↓
Audit Logging
   ↓
Final Fraud Report
```

This replaces any LLM-based explanation step with a strict logic-based explanation engine.

---

## 6. Rule Engine Design

### 6.1 Fraud indicators and scores

The scoring model will use the same rule values described in the README:

| Indicator | Score |
|---|---:|
| High transaction amount | +25 |
| Unusual transaction time | +15 |
| Unusual location | +20 |
| High transaction velocity | +20 |
| New or unusual merchant | +10 |
| Multiple failed attempts | +10 |

Maximum score: 100

### 6.2 Classification thresholds

| Score Range | Risk Level |
|---:|---|
| 0–29 | `LEGITIMATE` |
| 30–59 | `SUSPICIOUS` |
| 60–100 | `HIGH_RISK` |

### 6.3 Example logic

```python
score = 0
triggered_rules = []

if amount > threshold_amount:
    score += 25
    triggered_rules.append("HIGH_AMOUNT")

if is_unusual_time(timestamp):
    score += 15
    triggered_rules.append("UNUSUAL_TIME")

if location_changed(previous_location, location):
    score += 20
    triggered_rules.append("LOCATION_CHANGE")

if transactions_last_10min >= velocity_threshold:
    score += 20
    triggered_rules.append("HIGH_VELOCITY")

if is_new_merchant:
    score += 10
    triggered_rules.append("NEW_MERCHANT")

if failed_attempts >= 2:
    score += 10
    triggered_rules.append("MULTIPLE_FAILED_ATTEMPTS")
```

---

## 7. Validation and Data Model

### 7.1 Required transaction fields

For the base implementation, each transaction should include:

- `transaction_id`
- `timestamp`
- `amount`
- `merchant`
- `location`
- `previous_location`
- `payment_method`
- `device_id`
- `transactions_last_10min`
- `failed_attempts`
- `is_new_merchant`

Optional fields can include:

- `merchant_category`
- `account_age_days`
- `average_transaction_amount`
- `label`

### 7.2 Validation rules

The validator will reject or normalize records such as:

- Missing `transaction_id`
- Missing/invalid timestamp
- Amount less than or equal to zero
- Non-numeric transaction counts
- Invalid location or merchant values
- Empty dataset rows
- Malformed transaction records

A validation result should return:

- `is_valid`
- `errors`
- `normalized_transaction`

---

## 8. Agent Responsibilities

### 8.1 Transaction Analysis Agent

Responsibilities:

- Validate input
- Normalize values
- Check required fields
- Convert strings to numbers where needed
- Prepare transaction for scoring

### 8.2 Risk Assessment Agent

Responsibilities:

- Run rule evaluation
- Calculate risk score
- Choose risk level
- Return triggered reasons

### 8.3 Explanation Agent

Responsibilities:

- Generate explanation by converting triggered rules into plain English
- Use a rule-to-text mapping, not a model
- Ensure the explanation matches the actual score and classification

Example rule-to-text mapping:

```python
reason_map = {
    "HIGH_AMOUNT": "High transaction amount",
    "UNUSUAL_TIME": "Unusual transaction time",
    "LOCATION_CHANGE": "Location change",
    "HIGH_VELOCITY": "High transaction velocity",
    "NEW_MERCHANT": "New merchant",
    "MULTIPLE_FAILED_ATTEMPTS": "Multiple failed attempts"
}
```

### 8.4 Guardrail Agent

Since no LLM is used, the guardrail function is simple and deterministic:

- Validate that explanation output is generated from the risk engine
- Block any attempt to override score or label in the application flow
- Ensure all outputs are derived from the same rule result

This is a static logic gate, not a prompt filter.

### 8.5 Audit Agent

Responsibilities:

- Record transaction ID
- Save timestamp
- Save risk score and level
- Store triggered rules
- Save validation status
- Save explanation status
- Write structured logs as JSON or CSV

---

## 9. Deterministic Logic Rules

The system must be purely rule-based and repeatable.

### Design constraints

- No random scoring
- No model inference
- No hidden state changes between runs
- Same input must produce the same score and label
- All thresholds and weights should be in config

### Configuration file

`config.py` should hold:

```python
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
}
```

---

## 10. Batch Analysis

The project must support dataset-level analysis.

### Batch outputs

For a batch, compute:

- Total transactions
- Legitimate count
- Suspicious count
- High-risk count
- Fraud rate
- Average risk score
- Maximum risk score
- Average amount

### Example summary output

```text
========================================
       FRAUD ANALYSIS SUMMARY
========================================

Total Transactions : 1000
Legitimate         : 812
Suspicious         : 136
High Risk          : 52

Average Risk Score : 24.73
High-Risk Rate     : 5.20%
========================================
```

---

## 11. Data Strategy

### 11.1 Synthetic data for development

Use synthetic CSVs as the primary data source because they can exactly match the risk rules.

Recommended files:

- `transactions_demo.csv`
- `transactions_small.csv`
- `transactions_large.csv`

The dataset should be generated with scenarios such as:

- Legitimate transactions
- Suspicious transactions
- Fraudulent transactions

### 11.2 CSV schema

```csv
transaction_id,timestamp,amount,merchant,merchant_category,location,previous_location,payment_method,device_id,transactions_last_10min,failed_attempts,account_age_days,is_new_merchant,average_transaction_amount,label
TXN001,2026-08-10 10:30:00,850,Amazon,Shopping,Mumbai,Mumbai,UPI,DEV01,1,0,800,0,1200,LEGITIMATE
TXN002,2026-08-10 02:15:00,85000,UnknownMerchant,Electronics,Delhi,Mumbai,UPI,DEV98,5,2,12,1,1800,HIGH_RISK
```

---

## 12. Visualization Requirements

The implementation should produce the following basic plots:

- Risk distribution by class
- Risk score histogram
- Transaction amount vs risk score
- Activity trend over time

Optional visuals:

- Fraud rate by merchant category
- Fraud rate by payment method
- Risk by location
- Failed attempts vs risk

Libraries:

- `pandas`
- `matplotlib`
- `seaborn`

---

## 13. Logging and Audit Trail

Audit logs should store structured records such as:

```json
{
  "timestamp": "2026-08-13T10:30:00",
  "transaction_id": "TXN1024",
  "risk_score": 85,
  "risk_level": "HIGH_RISK",
  "triggered_rules": [
    "HIGH_AMOUNT",
    "UNUSUAL_TIME",
    "LOCATION_CHANGE",
    "HIGH_VELOCITY"
  ],
  "validation_errors": [],
  "explanation_generated": true
}
```

Important rule:

- No API keys, credentials, or secrets should be written to logs

---

## 14. Testing Plan

### 14.1 Unit tests

Build tests for:

- validation of required fields
- invalid amount handling
- invalid timestamp handling
- malformed records
- empty dataset handling
- rule triggering logic
- score calculation
- classification thresholds
- deterministic repeatability

### 14.2 Example test cases

```python
def test_high_amount_rule():
    transaction = {"amount": 80000, "timestamp": "2026-08-10 02:15:00", "location": "Delhi", "previous_location": "Mumbai", "transactions_last_10min": 5, "is_new_merchant": True, "failed_attempts": 2}
    result = assess_transaction(transaction)
    assert result["risk_score"] >= 60
    assert result["risk_level"] == "HIGH_RISK"
```

### 14.3 Determinism tests

The same input must always produce the same output:

```python
result_1 = assess_transaction(txn)
result_2 = assess_transaction(txn)
assert result_1 == result_2
```

---

## 15. Implementation Phases

### Phase 1 — Core data structures and validation

- Create transaction schema
- Create validator
- Create normalization utilities
- Handle bad input safely

### Phase 2 — Rule engine

- Add amount rule
- Add time rule
- Add location rule
- Add velocity rule
- Add merchant rule
- Add failed-attempt rule

### Phase 3 — Risk scoring and classification

- Combine rule weights
- Output `risk_score`
- Output `risk_level`
- Return triggered rules

### Phase 4 — Explanation layer

- Convert rules into plain English
- Ensure explanation matches the actual decision
- Create structured report output

### Phase 5 — Batch processing

- Load CSV data
- Assess every row
- Aggregate results
- Generate summary statistics

### Phase 6 — Visualization and logging

- Plot risk distribution
- Save charts to outputs
- Save JSON/CSV audit logs

### Phase 7 — Testing and stabilization

- Unit tests
- Regression tests
- Data edge cases
- Determinism verification

---

## 16. Example No-LLM Explanation Output

```text
====================================================
        TRANSACTION FRAUD ASSESSMENT
====================================================

Transaction ID : TXN1024
Amount         : ₹85,000
Merchant       : Unknown Merchant
Location       : Delhi
Payment Method : UPI

----------------------------------------------------
FRAUD INDICATORS
----------------------------------------------------

High Amount            : YES
Unusual Time           : YES
Location Change        : YES
High Velocity          : YES
New Merchant           : YES
Failed Attempts        : YES

----------------------------------------------------
RISK ASSESSMENT
----------------------------------------------------

Risk Score             : 100/100
Risk Level             : HIGH_RISK

Decision Basis:
• High transaction amount
• Unusual transaction time
• Location change
• High transaction velocity
• New merchant
• Multiple failed attempts

====================================================
```

This explanation is generated entirely from triggered rules, not from a language model.

---

## 17. Security Model

The deterministic engine is always authoritative. Untrusted transaction strings are inspected for direct overrides, role impersonation, system-prompt extraction, delimiter spoofing, indirect instructions, tool/code execution requests, and simple base64 obfuscation. Findings are recorded in `security_findings`; they cannot change the score or classification.

When configured, the optional LLM receives bounded transaction context plus the trusted assessment. It is instructed to write prose only, and its response is stored separately as `llm_explanation`. Missing credentials, timeouts, and malformed responses fall back to the deterministic report.

Optional environment variables:

```text
GEMINI_API_KEY
GEMINI_MODEL=gemini-2.0-flash
GEMINI_API_ENDPOINT=https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent
```

This means the app remains consistent, safe, and fully deterministic.

---

## 18. Recommended Build Order

1. Create project folders and config
2. Build input validator
3. Build fraud rules
4. Implement risk score and classification
5. Build explanation generator from rules
6. Add batch CSV processing
7. Add summary statistics
8. Add audit logging
9. Add charts
10. Write tests
11. Run full local validation

---

## 19. Success Criteria for This Version

The deterministic build is successful when it can:

- Accept individual transaction data
- Accept a dataset of transactions
- Validate malformed inputs
- Detect fraud indicators using code rules
- Compute risk scores consistently
- Classify correctly
- Explain decisions from the triggered rules
- Produce dataset summaries
- Save audit logs
- Generate basic visualizations
- Pass logic-based unit tests
- Run without any LLM or external AI dependency

---

## 20. Final Implementation Principle

The no-LLM version should follow this architecture:

```text
Transaction Data
   ↓
Validation Layer
   ↓
Rule-Based Feature Analysis
   ↓
Deterministic Risk Engine
   ↓
Risk Score + Classification
   ↓
Rule-Based Explanation
   ↓
Audit Log + Report Output
```

This version is intentionally simpler, more auditable, and more reliable than a model-driven approach, while still matching the project's core educational goal.
