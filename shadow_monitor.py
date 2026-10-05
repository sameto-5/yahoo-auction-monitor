import os
import time
import importlib
import hashlib
import json
from datetime import datetime, timedelta, timezone

from condition import classify_item_condition, prices_for_condition
from profit_evaluator import evaluate
from rule_loader import enabled, normalize, parse_rules
from sheets_shadow_store import ShadowSheetsStore
from shadow_pipeline import choose_records, cycle_projection, mark_processed, select_rules, title_match


JST = timezone(timedelta(hours=9))
sheets = None
yahoo_client = None
notifier = None


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    return int(os.getenv(name, str(default)))


def distribute_query_limits(query_count, total_limit, per_rule_limit):
    """Distribute the global item budget without starving later queries."""
    if query_count <= 0 or total_limit <= 0 or per_rule_limit <= 0:
        return [0] * max(query_count, 0)
    base = min(per_rule_limit, total_limit // query_count)
    limits = [base] * query_count
    remaining = total_limit - (base * query_count)
    for index in range(query_count):
        extra = min(per_rule_limit - limits[index], remaining)
        limits[index] += extra
        remaining -= extra
        if remaining <= 0:
            break
    return limits


def priority_limit(rule, priority_rows, status_class):
    key = normalize(rule.priority_lookup_model)
    limits = []
    for row in priority_rows:
        if enabled(row.get("有効")) and normalize(row.get("型番")) == key:
            value = prices_for_condition(row, status_class)["limit"]
            if value is not None:
                limits.append(value)
    return min(limits) if limits else None


def parse_end(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)
    except (TypeError, ValueError):
        return None


def retention_days(decision):
    if decision == "CANDIDATE":
        return env_int("YAHOO_AUCTION_CANDIDATE_RETENTION_DAYS", 30)
    if decision in {"EXCLUDED", "MODEL_MISMATCH"}:
        return env_int("YAHOO_AUCTION_EXCLUDED_RETENTION_DAYS", 3)
    return env_int("YAHOO_AUCTION_SHORT_RETENTION_DAYS", 7)


def make_record(item, rule, result, remaining, end_at, priority_purchase_limit, now):
    expected = rule.expected_sale_price or 0
    profit = result.get("estimated_profit")
    total = result.get("estimated_total_cost")
    limit = result.get("effective_purchase_limit")
    return {
        "auction_id": str(item.item_id)[:100], "rule_id": rule.rule_id,
        "title": str(item.title)[:300], "item_url": str(item.url)[:500],
        "normalized_model": normalize(rule.model)[:150], "current_price": item.price,
        "buy_shipping": result.get("buy_shipping"), "buy_shipping_source": result.get("buy_shipping_source"),
        "estimated_total_cost": result.get("estimated_total_cost"),
        "expected_sale_price": rule.expected_sale_price, "sale_fee": result.get("sale_fee"),
        "estimated_sale_shipping": rule.estimated_sale_shipping, "other_cost": rule.other_cost,
        "minimum_profit": rule.minimum_profit, "estimated_profit": profit,
        "estimated_margin_rate": (profit / expected) if profit is not None and expected > 0 else None,
        "headroom_yen": (limit - total) if limit is not None and total is not None else None,
        "condition_class": result.get("condition_class"),
        "explicit_purchase_limit": rule.max_purchase_price,
        "priority_purchase_limit": priority_purchase_limit,
        "calculated_purchase_limit": result.get("calculated_purchase_limit"),
        "effective_purchase_limit": result.get("effective_purchase_limit"),
        "minutes_remaining": remaining, "end_at": end_at,
        "decision": result["decision"], "reason_code": result["reason_code"],
        "reason_detail": str(result.get("reason_detail") or "")[:500],
        "expires_at": now + timedelta(days=retention_days(result["decision"])),
    }


def model_data_decision(rule, model_stats, now):
    row = model_stats.get(normalize(rule.model))
    if not row:
        return "DATA_INSUFFICIENT", "MODEL_STATS_MISSING"
    try:
        sample_count = int(float(row.get("sample_count") or 0))
    except (TypeError, ValueError):
        sample_count = 0
    if sample_count < env_int("YAHOO_AUCTION_MODEL_MIN_SAMPLES", 5):
        return "DATA_INSUFFICIENT", "MODEL_SAMPLE_TOO_SMALL"
    try:
        confidence = float(row.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    if confidence < float(os.getenv("YAHOO_AUCTION_MODEL_MIN_CONFIDENCE", "0.5")):
        return "DATA_INSUFFICIENT", "MODEL_CONFIDENCE_LOW"
    updated = str(row.get("updated_at") or "")
    try:
        updated_at = datetime.fromisoformat(updated.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return "DATA_INSUFFICIENT", "MODEL_STATS_STALE"
    if now - updated_at > timedelta(days=env_int("YAHOO_AUCTION_MODEL_MAX_AGE_DAYS", 30)):
        return "DATA_INSUFFICIENT", "MODEL_STATS_STALE"
    return None, None


def notification_stage(record, model_stat):
    if record.get("decision") != "CANDIDATE":
        return None
    remaining = record.get("minutes_remaining")
    if remaining is None or remaining < 0 or remaining > env_int("YAHOO_AUCTION_NOTIFY_WINDOW_MINUTES", 30):
        return None
    required = (
        "normalized_model", "buy_shipping", "expected_sale_price", "effective_purchase_limit",
        "estimated_profit", "estimated_margin_rate", "headroom_yen", "condition_class",
    )
    if any(record.get(name) in (None, "") for name in required):
        return None
    if record["condition_class"] in {"junk", "unknown"}:
        return None
    if record["estimated_total_cost"] > record["effective_purchase_limit"]:
        return None
    if record["estimated_profit"] < record["minimum_profit"]:
        return None
    if record["estimated_margin_rate"] < float(os.getenv("YAHOO_AUCTION_MINIMUM_MARGIN_RATE", "0.10")):
        return None
    try:
        sample_count = int(float(model_stat.get("sample_count") or 0))
        confidence = float(model_stat.get("confidence") or 0)
    except (TypeError, ValueError):
        return None
    if sample_count < env_int("YAHOO_AUCTION_MODEL_MIN_SAMPLES", 5):
        return None
    if confidence < float(os.getenv("YAHOO_AUCTION_MODEL_MIN_CONFIDENCE", "0.5")):
        return None
    strong = (
        remaining <= env_int("YAHOO_AUCTION_STRONG_WINDOW_MINUTES", 15)
        and record["headroom_yen"] >= env_int("YAHOO_AUCTION_STRONG_MIN_HEADROOM_YEN", 3000)
        and confidence >= float(os.getenv("YAHOO_AUCTION_STRONG_MIN_CONFIDENCE", "0.8"))
        and record.get("buy_shipping_source") == "ACTUAL"
        and record.get("condition_class") == "working"
    )
    return "STRONG_CANDIDATE" if strong else "CANDIDATE"


def _retry_time(now, attempt_count, retry_after=None):
    delay = env_int("YAHOO_AUCTION_NOTIFY_RETRY_BASE_SECONDS", 30) * (2 ** max(0, attempt_count - 1))
    return now + timedelta(seconds=max(delay, retry_after or 0))


def deliver_notifications(store, records, model_stats, now):
    webhook = os.getenv("YAHOO_DISCORD_WEBHOOK_URL", "").strip()
    limit = env_int("YAHOO_AUCTION_MAX_NOTIFICATIONS_PER_RUN", 10)
    sent_keys = store.sent_notification_keys()
    sent = failed = retries = rate_limits = 0
    for row in list(store.retry_rows()):
        if sent + failed >= limit:
            break
        key = (str(row.get("auction_id", "")), str(row.get("rule_id", "")), str(row.get("notification_stage", "")))
        if key in sent_keys:
            store.remove_retry(key)
            continue
        try:
            end_at = datetime.fromisoformat(str(row.get("end_at")).replace("Z", "+00:00"))
            next_at = datetime.fromisoformat(str(row.get("next_attempt_at")).replace("Z", "+00:00"))
            payload = json.loads(row.get("payload_json") or "{}")
        except (TypeError, ValueError):
            store.remove_retry(key)
            continue
        attempts = int(float(row.get("attempt_count") or 0))
        if end_at.astimezone(timezone.utc) <= now or attempts >= 3:
            store.remove_retry(key)
            continue
        if next_at.astimezone(timezone.utc) > now:
            continue
        message = str(payload.get("message") or "")
        result = notifier.send_discord(webhook, message, env_int("YAHOO_AUCTION_NOTIFY_TIMEOUT_SECONDS", 20))
        retries += 1
        if result.success:
            store.mark_notification_sent(row, key[2], now, hashlib.sha256(message.encode()).hexdigest())
            sent_keys.add(key)
            sent += 1
        else:
            failed += 1
            rate_limits += int(result.status_code == 429)
            store.queue_retry(row, key[2], payload, attempts + 1, _retry_time(now, attempts + 1, result.retry_after_seconds), result.error, now)

    for record in records:
        if sent + failed >= limit:
            break
        stat = model_stats.get(normalize(record.get("normalized_model"))) or {}
        stage = notification_stage(record, stat)
        if not stage:
            continue
        key = (str(record["auction_id"]), str(record["rule_id"]), stage)
        if key in sent_keys:
            continue
        message = notifier.format_notification(record, stage, stat, now)
        result = notifier.send_discord(webhook, message, env_int("YAHOO_AUCTION_NOTIFY_TIMEOUT_SECONDS", 20))
        if result.success:
            store.mark_notification_sent(record, stage, now, hashlib.sha256(message.encode()).hexdigest())
            sent_keys.add(key)
            sent += 1
        else:
            failed += 1
            rate_limits += int(result.status_code == 429)
            store.queue_retry(record, stage, {"message": message}, 1, _retry_time(now, 1, result.retry_after_seconds), result.error, now)
    return {"sent": sent, "failed": failed, "retries": retries, "rate_limits": rate_limits}


def run(store):
    started = datetime.now(timezone.utc)
    budget = env_int("YAHOO_AUCTION_RUN_BUDGET_SECONDS", 180)
    max_rules = env_int("YAHOO_AUCTION_MAX_RULES_PER_RUN", 30)
    max_items = env_int("YAHOO_AUCTION_MAX_ITEMS_PER_RUN", 50)
    max_items_per_rule = env_int("YAHOO_AUCTION_MAX_ITEMS_PER_RULE", 15)
    max_details = env_int("YAHOO_AUCTION_MAX_DETAIL_FETCHES", 20)
    max_upserts = env_int("YAHOO_AUCTION_MAX_SHADOW_UPSERTS", 100)
    sample_limit = env_int("YAHOO_AUCTION_AUDIT_SAMPLE_LIMIT", 20)
    lookahead = env_int("YAHOO_AUCTION_LOOKAHEAD_MINUTES", 180)
    cron_minutes = env_int("YAHOO_AUCTION_EXPECTED_CRON_INTERVAL_MINUTES", 30)
    rule_rows = store.rows[store.rules_sheet]
    headers = store.rule_headers
    priority_rows = store.rows["priority_items"]
    rules = parse_rules(
        rule_rows, headers,
        env_int("YAHOO_AUCTION_DEFAULT_MINIMUM_PROFIT", 5000),
        float(os.getenv("YAHOO_AUCTION_DEFAULT_SALE_FEE_RATE", "0.10")),
    )
    state = store.scheduler_state()
    model_stats = store.model_stats()
    selected = select_rules(rules, state, max_rules)
    projection = cycle_projection(len(rules), max_rules, cron_minutes)
    print(
        f"YAHOO_SHADOW_CYCLE: rules={len(rules)} per_run={max_rules} "
        f"items_per_rule={max_items_per_rule} items_per_run={max_items} "
        f"cron_minutes={cron_minutes} projected_minutes={projection['minutes']}"
    )
    if projection["minutes"] > lookahead:
        print(f"YAHOO_SHADOW_CYCLE_WARNING: projected_minutes={projection['minutes']} lookahead_minutes={lookahead}")
    records, attempted, fetched, details, errors, ending, processed_items = [], [], 0, 0, 0, 0, 0
    grouped = {}
    for rule in selected:
        grouped.setdefault(rule.query, []).append(rule)
    query_limits = distribute_query_limits(len(grouped), max_items, max_items_per_rule)
    if query_limits and min(query_limits) == 0:
        print(
            f"YAHOO_SHADOW_ITEM_BUDGET_WARNING: query_groups={len(grouped)} "
            f"items_per_run={max_items} action=increase_global_limit"
        )
    stop = False
    for (query, query_rules), query_limit in zip(grouped.items(), query_limits):
        if query_limit <= 0:
            continue
        if time.monotonic() - start_clock >= budget - 21:
            stop = True
            break
        try:
            items = yahoo_client.search(
                query,
                timeout=env_int("YAHOO_AUCTION_HTTP_TIMEOUT_SECONDS", 20),
                retries=env_int("YAHOO_AUCTION_HTTP_MAX_RETRIES", 2),
                backoff_base_seconds=env_int("YAHOO_AUCTION_HTTP_BACKOFF_SECONDS", 5),
            )
            attempted.extend(query_rules)
        except yahoo_client.RateLimitError as error:
            print(f"YAHOO_SHADOW_RATE_LIMIT_STOP: {type(error).__name__}")
            errors += 1
            attempted.extend(query_rules)
            break
        except Exception as error:
            print(f"YAHOO_SHADOW_SEARCH_ERROR: query={query!r} error={type(error).__name__}")
            errors += 1
            attempted.extend(query_rules)
            continue
        items = items[:query_limit]
        fetched += len(items)
        for item in items:
            processed_items += 1
            for rule in query_rules:
                now = datetime.now(timezone.utc)
                end_at = parse_end(item.end_at)
                list_remaining = int((end_at - now.astimezone(JST)).total_seconds() / 60) if end_at else None
                # Obvious non-targets are sampled only when the listing itself proves it is in-window.
                if list_remaining is not None and (list_remaining < 0 or list_remaining > lookahead):
                    continue
                decision, reason = title_match(item.title, rule)
                if decision:
                    if list_remaining is not None:
                        records.append(make_record(item, rule, {"decision": decision, "reason_code": reason}, list_remaining, end_at, None, now))
                    continue
                shipping = item.shipping_fee
                detail_error = None
                if (end_at is None or shipping is None) and details < max_details and time.monotonic() - start_clock < budget - 21:
                    details += 1
                    try:
                        detail = yahoo_client.get_detail(item.url, env_int("YAHOO_AUCTION_HTTP_TIMEOUT_SECONDS", 20), 0)
                        end_at = parse_end(detail.get("end_at")) or end_at
                        shipping = detail.get("shipping_fee") if detail.get("shipping_fee") is not None else shipping
                        item.price = detail.get("current_price") if detail.get("current_price") is not None else item.price
                    except Exception as error:
                        errors += 1
                        detail_error = type(error).__name__
                if end_at is None:
                    if detail_error:
                        records.append(make_record(
                            item, rule,
                            {"decision": "FETCH_ERROR", "reason_code": "DETAIL_FETCH_ERROR", "reason_detail": detail_error},
                            None, None, None, now,
                        ))
                    continue
                remaining = int((end_at - now.astimezone(JST)).total_seconds() / 60)
                if remaining < 0 or remaining > lookahead:
                    continue
                ending += 1
                status_class, _ = classify_item_condition(item.title, item.description, item.store_condition)
                p_limit = priority_limit(rule, priority_rows, status_class)
                result = {**evaluate(rule, item.price, shipping, status_class, p_limit), "condition_class": status_class}
                if result["decision"] == "CANDIDATE":
                    data_decision, data_reason = model_data_decision(rule, model_stats, now)
                    if data_decision:
                        result = {**result, "decision": data_decision, "reason_code": data_reason}
                records.append(make_record(item, rule, result, remaining, end_at, p_limit, now))
        interval = env_int("YAHOO_AUCTION_REQUEST_INTERVAL_MS", 1000) / 1000
        if interval > 0:
            time.sleep(interval)
    chosen = choose_records(records, max_upserts, sample_limit)
    now = datetime.now(timezone.utc)
    store.upsert_results(chosen, now)
    notification_metrics = {"sent": 0, "failed": 0, "retries": 0, "rate_limits": 0}
    if env_bool("YAHOO_AUCTION_NOTIFY_ENABLED", False):
        notification_metrics = deliver_notifications(store, chosen, model_stats, now)
    active_ids = {r.rule_id for r in rules}
    updated_state = {k: v for k, v in state.items() if k in active_ids}
    store.apply_state(mark_processed(updated_state, attempted), now)
    deleted = store.cleanup(now, min(500, env_int("YAHOO_AUCTION_CLEANUP_BATCH_SIZE", 500)))
    finished = datetime.now(timezone.utc)
    stats = {
        "run_id": store.lease.run_id, "status": "BUDGET_EXCEEDED" if stop else "COMPLETED",
        "started_at": started, "finished_at": finished,
        "duration_ms": int((finished - started).total_seconds() * 1000),
        "enabled_rule_count": len(rules), "searched_rule_count": len(attempted),
        "fetched_item_count": fetched, "ending_item_count": ending,
        "candidate_count": sum(r["decision"] == "CANDIDATE" for r in chosen),
        "shadow_upsert_count": len(chosen), "detail_fetch_count": details,
        "http_error_count": errors, "projected_cycle_minutes": projection["minutes"],
        "sheets_read_calls": store.read_calls, "sheets_write_calls": store.write_calls + 1,
    }
    store.aggregate_run_stats(stats)
    store.release_and_flush()
    print(
        f"YAHOO_SHADOW_END: attempted_rules={len(attempted)} fetched={fetched} ending={ending} "
        f"saved={len(chosen)} cleanup={deleted} sheets_reads={store.read_calls} "
        f"sheets_writes={store.write_calls} notifications={notification_metrics['sent']} "
        f"notification_failures={notification_metrics['failed']} retries={notification_metrics['retries']} "
        f"discord_429={notification_metrics['rate_limits']}"
    )


start_clock = 0.0


def main():
    global start_clock, sheets, yahoo_client, notifier
    if not env_bool("YAHOO_AUCTION_ENABLED", False):
        print("YAHOO_SHADOW_DISABLED: external_access=0")
        return
    if not env_bool("YAHOO_AUCTION_SHADOW", True):
        print("YAHOO_SHADOW_DISABLED: shadow_flag=false external_access=0")
        return
    if env_bool("YAHOO_AUCTION_NOTIFY_ENABLED", False) and not os.getenv("YAHOO_DISCORD_WEBHOOK_URL", "").strip():
        print("YAHOO_PHASE8E_SAFE_STOP: yahoo_webhook_not_configured external_access=0")
        return
    start_clock = time.monotonic()
    # Heavy/external-client modules are intentionally loaded only after all safety gates.
    sheets = importlib.import_module("sheets")
    yahoo_client = importlib.import_module("yahoo_client")
    notifier = importlib.import_module("yahoo_phase8e_notifier") if env_bool("YAHOO_AUCTION_NOTIFY_ENABLED", False) else None
    book = sheets.open_book(os.getenv("GOOGLE_CREDENTIALS"), os.getenv("SPREADSHEET_ID"))
    store = ShadowSheetsStore(book, lease_seconds=env_int("YAHOO_AUCTION_LEASE_SECONDS", 300))
    try:
        store.ensure_sheets()
        store.load_all(os.getenv("YAHOO_AUCTION_RULES_SHEET", "yahoo_auction_rules"))
        lease = store.acquire_lease(owner_id=os.getenv("RENDER_INSTANCE_ID") or None)
        if not lease:
            print("YAHOO_SHADOW_SKIPPED_LOCKED: yahoo_access=0")
            return
        run(store)
    finally:
        store.release_best_effort()


if __name__ == "__main__":
    main()
