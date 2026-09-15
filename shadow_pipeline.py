import math
import re
import unicodedata
from datetime import datetime, timezone

from rule_loader import normalize


SAVE_PRIORITY = {
    "CANDIDATE": 0, "SHIPPING_UNKNOWN": 1, "DECISION_UNAVAILABLE": 2,
    "OVER_LIMIT": 3, "BELOW_MINIMUM_PROFIT": 4, "FETCH_ERROR": 5,
    "EXCLUDED": 6, "MODEL_MISMATCH": 7,
}
SAMPLE_DECISIONS = {"EXCLUDED", "MODEL_MISMATCH"}


def select_rules(rules, last_processed_at, limit):
    rank = {"A": 0, "B": 1, "C": 2}
    oldest = "0000-01-01T00:00:00+00:00"
    return sorted(rules, key=lambda r: (
        last_processed_at.get(r.rule_id, oldest), rank.get(r.priority, 3), r.rule_id,
    ))[:max(0, limit)]


def cycle_projection(rule_count, rules_per_run, cron_interval_minutes):
    runs = math.ceil(rule_count / max(1, rules_per_run))
    return {"runs": runs, "minutes": runs * cron_interval_minutes}


def title_match(title, rule):
    value = normalize(title)
    model = normalize(rule.model)
    raw_model = unicodedata.normalize("NFKC", str(rule.model)).casefold()
    raw_title = unicodedata.normalize("NFKC", str(title)).casefold()
    chunks = [re.escape(v) for v in re.split(r"[\s\-_‐‑‒–—・./]+", raw_model) if v]
    separator = r"[\s\-_‐‑‒–—・./]*"
    pattern = r"(?<![a-z0-9])" + separator.join(chunks) + r"(?![a-z0-9])" if chunks else r"(?!)"
    bounded = bool(re.search(pattern, raw_title, re.IGNORECASE))
    if not model or not bounded:
        return "MODEL_MISMATCH", "MODEL_TOKEN_MISMATCH"
    for word in rule.exclude_words:
        if normalize(word) in value:
            return "EXCLUDED", "EXCLUDE_WORD_MATCH"
    for word in rule.include_words:
        if normalize(word) not in value:
            return "EXCLUDED", "REQUIRED_INCLUDE_WORD_MISSING"
    return None, None


def choose_records(records, max_records, sample_limit):
    regular = [r for r in records if r["decision"] not in SAMPLE_DECISIONS]
    samples = [r for r in records if r["decision"] in SAMPLE_DECISIONS]
    samples.sort(key=lambda r: (SAVE_PRIORITY[r["decision"]], r.get("auction_id", "")))
    combined = regular + samples[:max(0, sample_limit)]
    combined.sort(key=lambda r: (SAVE_PRIORITY.get(r["decision"], 99), r.get("auction_id", "")))
    return combined[:max(0, max_records)]


def mark_processed(state, rules, at=None):
    value = dict(state or {})
    stamp = (at or datetime.now(timezone.utc)).isoformat()
    for rule in rules:
        value[rule.rule_id] = stamp
    return value
