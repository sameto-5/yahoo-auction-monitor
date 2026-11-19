import math


POLICY_ALLOWED = {
    "working_only": {"working"},
    "working_unchecked": {"working", "unchecked"},
    "working_unchecked_unknown": {"working", "unchecked", "unknown"},
    "all_except_junk": {"working", "unchecked", "unknown"},
    "all": {"working", "unchecked", "unknown", "junk"},
}


def evaluate(rule, current_price, actual_shipping, status_class, priority_limit=None,
             allow_junk_unknown=False, allow_unchecked=False, default_sale_shipping=None,
             default_buy_shipping=None):
    condition_allowed = status_class in POLICY_ALLOWED[rule.condition_policy]
    if allow_junk_unknown and status_class in {"junk", "unknown"}:
        condition_allowed = True
    if allow_unchecked and status_class == "unchecked":
        condition_allowed = True
    if not condition_allowed:
        return {"decision": "EXCLUDED", "reason_code": "CONDITION_POLICY_REJECTED"}
    if actual_shipping is not None:
        shipping, shipping_source = actual_shipping, "ACTUAL"
    elif rule.estimated_buy_shipping is not None:
        shipping, shipping_source = rule.estimated_buy_shipping, "RULE_ESTIMATE"
    elif rule.group_default_buy_shipping is not None:
        shipping, shipping_source = rule.group_default_buy_shipping, "GROUP_DEFAULT"
    elif default_buy_shipping is not None:
        # Unknown shipping must never become zero.  A configurable, conservative
        # estimate lets it remain a clearly-labelled reference candidate.
        shipping, shipping_source = default_buy_shipping, "SAFE_DEFAULT"
    else:
        return {"decision": "SHIPPING_UNKNOWN", "reason_code": "BUY_SHIPPING_NOT_AVAILABLE"}
    sale_shipping = rule.estimated_sale_shipping
    if sale_shipping is None and default_sale_shipping is not None:
        # When the rule has no resale-shipping estimate, use the higher of the
        # observed purchase shipping and a configurable safety floor.  We never
        # turn an unknown purchase shipping into zero.
        sale_shipping = max(shipping, default_sale_shipping)
    if sale_shipping is None:
        return {"decision": "DECISION_UNAVAILABLE", "reason_code": "SALE_SHIPPING_NOT_AVAILABLE"}
    if current_price is None or rule.expected_sale_price is None:
        return {"decision": "DECISION_UNAVAILABLE", "reason_code": "EXPECTED_SALE_PRICE_MISSING"}
    sale_fee = math.ceil(rule.expected_sale_price * rule.sale_fee_rate)
    calculated_limit = rule.expected_sale_price - sale_fee - sale_shipping - rule.other_cost - rule.minimum_profit
    limits = [v for v in (rule.max_purchase_price, priority_limit, calculated_limit) if v is not None and v >= 0]
    if not limits:
        return {"decision": "DECISION_UNAVAILABLE", "reason_code": "PURCHASE_LIMIT_NOT_AVAILABLE"}
    total = current_price + shipping
    profit = rule.expected_sale_price - sale_fee - sale_shipping - total - rule.other_cost
    effective = min(limits)
    common = {
        "buy_shipping": shipping, "buy_shipping_source": shipping_source,
        "estimated_sale_shipping": sale_shipping,
        "estimated_total_cost": total, "sale_fee": sale_fee, "estimated_profit": profit,
        "calculated_purchase_limit": calculated_limit, "effective_purchase_limit": effective,
    }
    if total > effective:
        return {**common, "decision": "OVER_LIMIT", "reason_code": "CURRENT_TOTAL_OVER_EFFECTIVE_LIMIT"}
    if profit < rule.minimum_profit:
        return {**common, "decision": "BELOW_MINIMUM_PROFIT", "reason_code": "ESTIMATED_PROFIT_BELOW_MINIMUM"}
    return {**common, "decision": "CANDIDATE", "reason_code": "PROFIT_AND_LIMIT_OK"}
