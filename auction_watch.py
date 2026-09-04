from datetime import datetime


def as_bool(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def total_cost(price, shipping_fee):
    if price is None or shipping_fee is None:
        return None
    return price + shipping_fee


def discovery_action(item, buy_limit):
    """Return immediate, watch, or ignore. Unknown is conservatively watched."""
    if buy_limit is None:
        return "ignore"
    if item.buy_now_price is not None:
        return "immediate" if total_cost(item.buy_now_price, item.shipping_fee) <= buy_limit else "watch"
    if item.listing_type == "fixed":
        return "immediate" if total_cost(item.price, item.shipping_fee) is not None and total_cost(item.price, item.shipping_fee) <= buy_limit else "ignore"
    return "watch"


def minutes_to_end(end_at, now):
    if not end_at:
        return None
    try:
        ending = datetime.strptime(str(end_at), "%Y-%m-%d %H:%M:%S").replace(tzinfo=now.tzinfo)
    except (TypeError, ValueError):
        return None
    return int((ending - now).total_seconds() / 60)


def due_for_check(row, now, threshold_minutes=30):
    if as_bool(row.get("詳細確認済み")):
        return False
    remaining = minutes_to_end(row.get("終了日時"), now)
    return remaining is not None and 0 <= remaining <= threshold_minutes


def ending_result(current_price, shipping_fee, buy_limit):
    total = total_cost(current_price, shipping_fee)
    if total is None or not buy_limit or total > buy_limit:
        return {"notify": False, "total": total, "ratio": None, "level": ""}
    ratio = total / buy_limit * 100
    level = "🚨 超有力候補" if ratio <= 80 else "🔥 有力候補" if ratio <= 85 else "終了間近候補"
    return {"notify": True, "total": total, "ratio": ratio, "level": level}


def should_cleanup(status, failure_count=0, max_failures=3):
    return status in {"sold", "ended", "cancelled"} or int(failure_count or 0) >= max_failures
