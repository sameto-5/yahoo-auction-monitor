import re
import unicodedata
from dataclasses import dataclass


RULE_HEADERS = [
    "rule_id", "enabled", "priority", "group_name", "query", "model",
    "include_words", "exclude_words", "condition_policy", "expected_sale_price",
    "max_purchase_price", "estimated_buy_shipping", "group_default_buy_shipping",
    "estimated_sale_shipping", "sale_fee_rate", "other_cost", "minimum_profit",
    "notify_minutes", "priority_lookup_model", "notes",
]
REQUIRED_HEADERS = {"rule_id", "enabled", "group_name", "query", "model", "condition_policy", "expected_sale_price"}
CONDITION_POLICIES = {
    "working_only", "working_unchecked", "working_unchecked_unknown", "all_except_junk", "all",
}


def enabled(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "有効"}


def normalize(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s\-_‐‑‒–—・./]+", "", text)


def split_words(value):
    return tuple(word.strip() for word in re.split(r"[|,\n]", str(value or "")) if word.strip())


def optional_int(value, name):
    text = str(value or "").replace(",", "").strip()
    if not text:
        return None
    if not text.isdigit():
        raise ValueError(f"{name} must be a non-negative integer")
    return int(text)


def optional_rate(value, default):
    text = str(value or "").strip()
    rate = default if not text else float(text)
    if rate < 0 or rate >= 1:
        raise ValueError("sale_fee_rate must be >= 0 and < 1")
    return rate


@dataclass(frozen=True)
class WatchRule:
    rule_id: str
    priority: str
    group_name: str
    query: str
    model: str
    include_words: tuple[str, ...]
    exclude_words: tuple[str, ...]
    condition_policy: str
    expected_sale_price: int
    max_purchase_price: int | None
    estimated_buy_shipping: int | None
    group_default_buy_shipping: int | None
    estimated_sale_shipping: int | None
    sale_fee_rate: float
    other_cost: int
    minimum_profit: int
    notify_minutes: tuple[int, ...]
    priority_lookup_model: str
    notes: str


def parse_rules(rows, headers, default_minimum_profit=5000, default_sale_fee_rate=0.10):
    missing = sorted(REQUIRED_HEADERS - set(headers))
    if missing:
        raise ValueError("yahoo_auction_rules missing headers: " + ", ".join(missing))
    rules, ids, group_shipping = [], set(), {}
    for row in rows:
        if not enabled(row.get("enabled")):
            continue
        rid = str(row.get("rule_id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", rid):
            raise ValueError(f"invalid rule_id: {rid!r}")
        if rid in ids:
            raise ValueError(f"duplicate rule_id: {rid}")
        ids.add(rid)
        policy = str(row.get("condition_policy") or "").strip()
        if policy not in CONDITION_POLICIES:
            raise ValueError(f"invalid condition_policy for {rid}: {policy}")
        query, model, group = (str(row.get(k) or "").strip() for k in ("query", "model", "group_name"))
        if not query or not model or not group:
            raise ValueError(f"query, model and group_name are required for {rid}")
        group_ship = optional_int(row.get("group_default_buy_shipping"), "group_default_buy_shipping")
        if group_ship is not None and group in group_shipping and group_shipping[group] != group_ship:
            raise ValueError(f"conflicting group_default_buy_shipping for {group}")
        if group_ship is not None:
            group_shipping[group] = group_ship
        notify = tuple(sorted({optional_int(v, "notify_minutes") for v in split_words(row.get("notify_minutes"))}, reverse=True))
        if any(v is None or not 1 <= v <= 1440 for v in notify):
            raise ValueError(f"invalid notify_minutes for {rid}")
        expected_sale_price = optional_int(row.get("expected_sale_price"), "expected_sale_price")
        if expected_sale_price is None:
            raise ValueError(f"expected_sale_price is required for {rid}")
        rules.append(WatchRule(
            rid, str(row.get("priority") or "C").strip().upper(), group, query, model,
            split_words(row.get("include_words")), split_words(row.get("exclude_words")), policy,
            expected_sale_price,
            optional_int(row.get("max_purchase_price"), "max_purchase_price"),
            optional_int(row.get("estimated_buy_shipping"), "estimated_buy_shipping"), group_ship,
            optional_int(row.get("estimated_sale_shipping"), "estimated_sale_shipping"),
            optional_rate(row.get("sale_fee_rate"), default_sale_fee_rate),
            optional_int(row.get("other_cost"), "other_cost") or 0,
            optional_int(row.get("minimum_profit"), "minimum_profit") if str(row.get("minimum_profit") or "").strip() else default_minimum_profit,
            notify, str(row.get("priority_lookup_model") or model).strip(), str(row.get("notes") or "")[:500],
        ))
    return rules
