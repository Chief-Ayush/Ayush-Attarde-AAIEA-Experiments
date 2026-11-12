"""Query classification: intent, complexity and priority (lab step 5 and variation 1).

Two interchangeable intent/complexity engines are provided:
  * "rules"   - ordered keyword rules (the manual's baseline, extended with the precedence the manual asks students to test);
  * "learned" - TF-IDF word + character n-grams with logistic regression, trained on data/train_queries.csv.
Priority is always assigned from explicit business rules (impact and type), never from tone, and the critical-incident
(outage / security) rules run first in both engines and cannot be overridden by the learned model.
"""
from __future__ import annotations

import csv
import pickle
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INTENTS = ["Login / Access", "Billing", "Technical Error", "Feature Request", "General Information", "Unresolved Issue"]
COMPLEXITY = ["Low", "Medium", "High"]
PRIORITIES = ["P1", "P2", "P3", "P4"]
TRAIN_CSV = ROOT / "data" / "train_queries.csv"
MODEL_FILE = ROOT / "storage" / "classifier_model.pkl"

STRONG_OUTAGE = ["application is down", "service outage", "service is down", "whole site", "complete service", "everything is down", "service unavailable", "unavailable for everyone",
                 "down for every", "gateway error", "gateway timeout", "an outage", "there an outage", "whole service", "platform is unreachable"]
GROUP_OUTAGE = ["all users", "all of our users", "all our users", "every team", "everyone in", "everyone on", "entire company", "entire organisation", "whole department", "no one in",
                "nobody in", "is down for", "all our teams", "all our staff", "any of our staff"]
INHERENT_GROUP = ["nobody here can", "none of our", "no one can", "nobody can"]              # these phrases already say that people cannot work
IMPAIRMENT = ["down", "unavailable", "cannot", "can't", "cant", "unable", "outage", "timeout", "timed out", "error", "locked out", "lost access", "not loading", "unreachable", "blank page",
              "no access", "blocked", "fails", "failing", "not working", "won't load", "503", "502", "504", "downtime", "gateway"]
OUTAGE = STRONG_OUTAGE + GROUP_OUTAGE + INHERENT_GROUP
SECURITY = ["security incident", "data breach", "breach", "hacked", "compromised", "unauthorized", "unauthorised", "phishing", "leaked", "posted publicly", "unknown admin",
            "stranger", "suspicious login", "from another country", "visible to another company", "visible to outsiders", "someone logged into", "someone unknown",
            "former employee is still", "stranger was added", "changed the billing owner", "never created", "ransom", "ransomware", "extortion", "encrypted our", "exfiltrat", "malware",
            "stolen data", "data was stolen"]
FORMER_STAFF = re.compile(r"(former|ex-|left (the )?(company|in \w+)|no longer (work|employ)\w*|resigned|terminated)[^.]{0,60}(still|continues)[^.]{0,40}(access|open|read|see|download|log)")
NEGATORS = {"never", "no", "not", "without", "neither", "nor", "n't", "hardly", "zero"}
PAST_CUES = ["last week", "last month", "yesterday", "earlier this", "previously", "a while ago", "in the past", "last year"]
RESOLVED_CUES = ["works", "working fine", "resolved", "back to normal", "fixed", "fine now", "is fine", "no longer"]
FEATURE_CUE = re.compile(r"\b(please add|can you add|could you add|would you add|would love|i wish|wish you|wish there|feature request|new feature|suggest(ion)?|it would be (great|nice|helpful)|would be nice|any plans? to|on your roadmap|please consider|could you build|can you build|please make)\b")
FAILURE_WORDS = ["error", "fails", "failed", "failing", "crash", "not working", "broken", "cannot", "can't", "unable", "bug", "stopped", "doesn't work", "does not work"]
TOPICS = {"refund": ["refund", "money back", "charged twice", "double charge", "overcharged"], "plan": ["upgrade", "downgrade", "cancel", "switch plan", "change plan", "change our plan"],
          "invoice": ["invoice", "receipt", "billing history"], "card": ["credit card", "payment method", "update card"], "tech": ["export", "error", "crash", "slow", "sync", "fails", "failing"],
          "login": ["password", "log in", "sign in", "locked", "2fa", "two-factor"], "data": ["delete all my data", "delete my data", "erase my data", "my personal data"]}
UNRESOLVED = ["still not", "still unresolved", "still fails", "still cannot", "still can't", "still slow", "still there", "did not work", "didn't work", "did not solve", "did not fix",
              "does not work for us", "third time", "second time", "again after", "same error again", "already tried", "nothing helped", "nothing changed", "problem persists",
              "came back", "remains", "not take effect", "re-opening", "reopening", "steps you sent", "followed your steps", "still"]
BILLING = ["invoice", "payment", "billing", "subscription", "refund", "charged", "charge", "receipt", "credit card", "card", "plan renewal", "overcharged", "cancel", "upgrade", "downgrade",
           "tax certificate", "pricing page", "annual billing", "money back"]
LOGIN = ["log in", "login", "sign in", "sign-in", "signin", "password", "locked", "lockout", "2fa", "two-factor", "authenticator", "verification code", "sso", "saml", "identity provider",
         "reset link", "invitation link", "backup codes", "access my account", "unlock"]
FEATURE = ["feature request", "add dark mode", "dark mode", "new feature", "please add", "would love", "wish list", "on your roadmap", "roadmap", "i suggest", "it would be great",
           "can you build", "would you consider", "idea:", "please make", "any plans to", "plans to support", "coming", "could you add", "suggestion", "would help to have", "do you plan"]
TECH = ["error", "fails", "failed", "failing", "crash", "bug", "slow", "lag", "timeout", "times out", "timing out", "export", "sync", "integration", "not syncing", "500", "empty",
        "not loading", "broken", "disappearing", "twice for", "wrong totals", "rate limit", "429", "upload", "not working properly", "stopped"]
GENERAL = ["plan", "browser", "mobile", "app", "ios", "android", "import", "trello", "asana", "invite", "guest", "member", "admin", "role", "permission", "include", "included",
           "discount", "gdpr", "dpa", "uptime", "training", "security questionnaire", "encrypted", "stored", "student", "education", "non-profit", "compliance", "price", "pricing"]
DOMAIN = set(sum([OUTAGE, SECURITY, BILLING, LOGIN, FEATURE, TECH, GENERAL], [])) - {"still", "card", "again after", "remains", "charge"}
KNOWN_TOPICS = ["basic plan", "pro plan", "business plan", "plan include", "included in", "difference between", "browser", "mobile app", "iphone", "ipad", "android", "ios", "safari",
                "import", "trello", "asana", "spreadsheet", "invite", "colleague", "guest", "member", "admin", "role", "permission", "unlimited", "audit export"]
DISPUTE = ["charged", "double charge", "twice", "refund", "overcharged", "declined", "failed", "higher than", "wrong", "never subscribed", "free trial", "suspended", "read-only", "money back"]
HARD_TECH = ["server error", "500", "internal", "fails every", "cannot", "can't", "unable"]
MEDIUM_LOGIN = ["2fa", "two-factor", "authenticator", "verification code", "backup code", "cannot log in", "can't log in", "cannot sign in", "can't sign in", "invitation", "sso", "saml",
                "identity provider", "looping", "keeps looping", "changed my email", "unable to log"]

_INJECTION = [r"ignore (all |any )?(previous|prior|above) (instructions|rules)", r"disregard (the )?(previous|above|system)", r"system prompt", r"you are now ", r"act as (an? )?(admin|system)",
              r"(mark|set|change) (this|the) (ticket|status|priority)", r"reveal (the )?(prompt|instructions|api key|secret)", r"override (the )?(policy|rules|sla)", r"close (this|the) ticket",
              r"(print|show|list) (all )?(customer|user)s? (data|emails|records)"]
_PII = [(re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[email]"), (re.compile(r"\b\d(?:[ -]?\d){12,18}\b"), "[card]"), (re.compile(r"(?<!\w)\+?\d[\d -]{8,}\d\b"), "[phone]"),
        (re.compile(r"(?i)\b(password|passcode|otp|token|api[_ -]?key)\s*(is|:|=)\s*\S+"), r"\1 [removed]")]


def _has(text: str, terms) -> list[str]:
    return [t for t in terms if t in text]


def scan_text(text: str) -> dict:
    """Flag - never act on - text that tries to instruct the system; the flags are shown to agents and logged."""
    low = text.lower()
    inj = [p for p in _INJECTION if re.search(p, low)]
    pii, rest = [], text
    for rx, name in _PII:                                   # each kind is looked for in what earlier kinds left, so a card number is not also reported as a phone number
        if rx.search(rest):
            pii.append(name)
            rest = rx.sub(name, rest)
    return {"injection": bool(inj), "pii": pii}


def redact(text: str) -> str:
    for rx, rep in _PII:
        text = rx.sub(rep, text)
    return text


def _negated(low: str, idx: int) -> bool:
    prev = re.findall(r"[a-z']+", low[max(0, idx - 40): idx])[-5:]
    return any(w in NEGATORS or w.endswith("n't") for w in prev)


def critical_signals(text: str) -> list[str]:
    """Outage and security triggers with context: a negated mention ('never had a breach') or a past, already-resolved one does not count, and a group phrase
    such as 'everyone on' counts only together with a word that says people are impaired."""
    low = text.lower()
    if any(c in low for c in PAST_CUES) and any(c in low for c in RESOLVED_CUES):
        return []
    hits: list[str] = []
    impaired = any(w in low for w in IMPAIRMENT)
    for kind, terms, needs_impair in (("security", SECURITY, False), ("outage", STRONG_OUTAGE, False), ("outage", GROUP_OUTAGE, True), ("outage", INHERENT_GROUP, False)):
        for t in terms:
            i = low.find(t)
            if i >= 0 and not _negated(low, i) and (impaired or not needs_impair):
                hits.append(f"{kind}:{t}")
    if FORMER_STAFF.search(low):
        hits.append("security:former staff still has access")
    return hits[:3]


def normalize_typos(text: str) -> str:
    """Repair obvious typos of product words ('pasword' -> 'password') so that ambiguity and keyword checks do not miss them."""
    import difflib
    vocab = _vocab()
    out = []
    for tok in re.findall(r"\w+|\W+", text):
        low = tok.lower()
        if tok.isalpha() and len(tok) >= 5 and low not in vocab:
            m = difflib.get_close_matches(low, [v for v in vocab if v[0] == low[0]], n=1, cutoff=0.82)
            tok = m[0] if m else tok
        out.append(tok)
    return "".join(out)


_VOCAB: set[str] = set()


def _vocab() -> set[str]:
    if not _VOCAB:
        terms = sum([BILLING, LOGIN, FEATURE, TECH, GENERAL, STRONG_OUTAGE, SECURITY], [])
        _VOCAB.update(w for t in terms for w in re.findall(r"[a-z]+", t) if len(w) >= 5)
        _VOCAB.update({"please", "workspace", "account", "invoice", "subscription", "password", "forgot", "unable"})
    return _VOCAB


def mixed_request(text: str) -> bool:
    low = text.lower()
    return sum(1 for terms in TOPICS.values() if any(t in low for t in terms)) >= 2


GENERIC = {"error", "broken", "issue", "problem", "bug", "stopped", "fails", "failed", "failing", "not working", "crash", "help", "slow"}


def is_ambiguous(text: str) -> bool:
    """Too short to say what is wrong: no product term at all, or only generic failure words in a very short message."""
    low = text.lower()
    words = re.findall(r"[a-z0-9']+", low)
    hits = [t for t in DOMAIN if t in low]
    if len(words) <= 7 and not hits:
        return True
    return len(words) <= 4 and all(h in GENERIC for h in hits)


# ---------------------------------------------------------------------------------------------------------------------- rules engine
def rule_intent(text: str) -> tuple[str, list[str]]:
    """Ordered rules: critical > unresolved > billing > login > feature > technical > general (a deliberate precedence)."""
    low = text.lower()
    for name, terms, intent in (("unresolved", UNRESOLVED, "Unresolved Issue"), ("billing", BILLING, "Billing"), ("login", LOGIN, "Login / Access"), ("feature", FEATURE, "Feature Request"),
                                ("technical", TECH, "Technical Error"), ("general", GENERAL, "General Information")):
        hit = _has(low, terms)
        if name == "unresolved":
            hit = [h for h in hit if h != "still"] or ([h for h in hit] if len(low.split()) > 6 else [])
        if hit:
            return intent, [f"{name}:{hit[0]}"]
    return "General Information", ["default"]


def rule_complexity(text: str, intent: str) -> str:
    low = text.lower()
    if intent == "Unresolved Issue":
        return "High"
    if intent == "Feature Request":
        return "Low"
    if intent == "Login / Access":
        return "Medium" if _has(low, MEDIUM_LOGIN) else "Low"
    if intent == "Billing":
        return "Medium" if _has(low, DISPUTE) or "currency" in low or "euro" in low or "tax" in low or "different plan" in low else "Low"
    if intent == "Technical Error":
        return "Medium"
    return "Low" if _has(low, KNOWN_TOPICS) else "Medium"


def assign_priority(intent: str, complexity: str, text: str, critical: bool) -> str:
    """Explicit business rules: impact and type decide priority; sentiment and exclamation marks are ignored."""
    low = text.lower()
    if critical:
        return "P1"
    if intent == "Unresolved Issue":
        return "P2"
    if intent == "Feature Request":
        return "P4"
    if intent == "General Information":
        return "P4" if complexity == "Low" else "P3"
    if intent == "Login / Access":
        if complexity == "Medium" and not _has(low, ["sso", "saml", "identity provider", "invitation", "invited"]):
            return "P2"
        return "P3"
    if intent == "Billing":
        return "P2" if complexity == "Medium" and _has(low, DISPUTE) else "P3"
    if intent == "Technical Error":
        return "P2" if _has(low, HARD_TECH) and ("export" in low or "error" in low or "fails" in low) and "slow" not in low else "P3"
    return "P3"


# ---------------------------------------------------------------------------------------------------------------------- learned engine
def _features():
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.pipeline import FeatureUnion
    return FeatureUnion([("w", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=1, lowercase=True)),
                         ("c", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, min_df=2, lowercase=True))])


def load_training(path: Path = TRAIN_CSV) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def train_model(rows: list[dict] | None = None, save: bool = True) -> dict:
    from sklearn.linear_model import LogisticRegression
    rows = rows or load_training()
    texts = [r["text"] for r in rows]
    feats = _features().fit(texts)
    X = feats.transform(texts)
    models = {}
    for target in ("intent", "complexity"):
        models[target] = LogisticRegression(C=12.0, max_iter=2000, class_weight="balanced").fit(X, [r[target] for r in rows])
    import sklearn
    bundle = {"features": feats, "models": models, "n": len(rows), "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"), "sklearn": sklearn.__version__}
    if save:
        try:
            MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = MODEL_FILE.with_suffix(".tmp")
            with open(tmp, "wb") as fh:
                pickle.dump(bundle, fh)
            tmp.replace(MODEL_FILE)
        except OSError:
            pass
    return bundle


_BUNDLE: dict | None = None


def get_model() -> dict:
    global _BUNDLE
    if _BUNDLE is None:
        if MODEL_FILE.exists() and (not TRAIN_CSV.exists() or MODEL_FILE.stat().st_mtime >= TRAIN_CSV.stat().st_mtime):
            try:
                import sklearn
                with open(MODEL_FILE, "rb") as fh:
                    b = pickle.load(fh)
                if b.get("sklearn") != sklearn.__version__:      # saved with another scikit-learn: retrain instead of risking a crash
                    raise ValueError("scikit-learn version differs")
                _BUNDLE = b
            except Exception:
                _BUNDLE = train_model()
        else:
            _BUNDLE = train_model()
    return _BUNDLE


def classify_query(query_text: str, method: str = "learned") -> dict:
    """Return {intent, complexity, priority, ambiguous, confidence, method, signals}.  Invalid labels can never be returned."""
    raw = (query_text or "").strip()
    text = normalize_typos(raw)
    crit = critical_signals(raw)
    if crit:
        return {"intent": "Technical Error", "complexity": "High", "priority": "P1", "ambiguous": False, "confidence": 1.0, "method": "critical-rule", "signals": crit}
    amb = is_ambiguous(text)
    signals: list[str] = []
    conf = 1.0
    low_ = text.lower()
    if FEATURE_CUE.search(low_) and not any(w in low_ for w in FAILURE_WORDS):     # an explicit request cue wins over product words in the sentence
        return {"intent": "Feature Request", "complexity": "Low", "priority": "P4", "ambiguous": False, "confidence": 0.95, "method": "feature-cue", "signals": ["feature-cue"]}
    if method == "learned":
        b = get_model()
        X = b["features"].transform([text])
        pi, pc = b["models"]["intent"].predict_proba(X)[0], b["models"]["complexity"].predict_proba(X)[0]
        intent = b["models"]["intent"].classes_[pi.argmax()]
        cx = b["models"]["complexity"].classes_[pc.argmax()]
        conf = float(pi.max())
        signals.append(f"learned:p={conf:.2f}")
    else:
        intent, signals = rule_intent(text)
        cx = rule_complexity(text, intent)
    if intent not in INTENTS or cx not in COMPLEXITY:   # validate labels before use (manual, step 5 extension)
        intent, cx, signals = "General Information", "Medium", signals + ["invalid-label-replaced"]
    if amb:
        intent, cx = "Technical Error", "Medium"
        signals.append("ambiguous")
    elif mixed_request(text) and cx == "Low":
        cx = "Medium"                                  # two different requests in one message are never answered automatically
        signals.append("mixed-request")
    pri = assign_priority(intent, cx, text, False)
    if amb:
        pri = "P3"
    return {"intent": intent, "complexity": cx, "priority": pri, "ambiguous": amb, "confidence": round(conf, 3), "method": method, "signals": signals}


def classify_baseline(query_text: str) -> dict:
    """The manual's starter classifier, verbatim in behaviour, kept for the comparison table."""
    t = query_text.lower()
    if any(w in t for w in ["security incident", "data breach", "application is down", "all users", "service outage"]):
        return {"intent": "Technical Error", "complexity": "High", "priority": "P1"}
    if any(w in t for w in ["invoice", "payment", "billing", "subscription"]):
        return {"intent": "Billing", "complexity": "Medium", "priority": "P2"}
    if any(w in t for w in ["cannot log in", "account locked", "password"]):
        return {"intent": "Login / Access", "complexity": "Medium", "priority": "P2"}
    if any(w in t for w in ["add dark mode", "feature request", "new feature"]):
        return {"intent": "Feature Request", "complexity": "Low", "priority": "P4"}
    if any(w in t for w in ["still unresolved", "did not work", "not working"]):
        return {"intent": "Unresolved Issue", "complexity": "High", "priority": "P2"}
    return {"intent": "General Information", "complexity": "Low", "priority": "P3"}
