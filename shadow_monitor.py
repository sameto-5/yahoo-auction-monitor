import os
import time
import uuid
import importlib
from datetime import datetime, timedelta, timezone

import database
from condition import classify_item_condition, prices_for_condition
from profit_evaluator import evaluate
from rule_loader import enabled, normalize, parse_rules
from shadow_pipeline import choose_records, cycle_projection, mark_processed, select_rules, title_match


JST = timezone(timedelta(hours=9))
sheets = None
yahoo_client = None


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    return int(os.getenv(name, str(default)))


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
    return {
        "auction_id": str(item.item_id)[:100], "rule_id": rule.rule_id,
        "title": str(item.title)[:300], "item_url": str(item.url)[:500],
        "normalized_model": normalize(rule.model)[:150], "current_price": item.price,
        "buy_shipping": result.get("buy_shipping"), "buy_shipping_source": result.get("buy_shipping_source"),
        "estimated_total_cost": result.get("estimated_total_cost"),
        "expected_sale_price": rule.expected_sale_price, "sale_fee": result.get("sale_fee"),
        "estimated_sale_shipping": rule.estimated_sale_shipping, "other_cost": rule.other_cost,
        "minimum_profit": rule.minimum_profit, "estimated_profit": result.get("estimated_profit"),
        "explicit_purchase_limit": rule.max_purchase_price,
        "priority_purchase_limit": priority_purchase_limit,
        "calculated_purchase_limit": result.get("calculated_purchase_limit"),
        "effective_purchase_limit": result.get("effective_purchase_limit"),
        "minutes_remaining": remaining, "end_at": end_at,
        "decision": result["decision"], "reason_code": result["reason_code"],
        "reason_detail": str(result.get("reason_detail") or "")[:500],
        "expires_at": now + timedelta(days=retention_days(result["decision"])),
    }


def run(connection):
    started = datetime.now(timezone.utc)
    budget = env_int("YAHOO_AUCTION_RUN_BUDGET_SECONDS", 180)
    max_rules = env_int("YAHOO_AUCTION_MAX_RULES_PER_RUN", 30)
    max_items = env_int("YAHOO_AUCTION_MAX_ITEMS_PER_RUN", 100)
    max_details = env_int("YAHOO_AUCTION_MAX_DETAIL_FETCHES", 20)
    max_upserts = env_int("YAHOO_AUCTION_MAX_SHADOW_UPSERTS", 100)
    sample_limit = env_int("YAHOO_AUCTION_AUDIT_SAMPLE_LIMIT", 20)
    lookahead = env_int("YAHOO_AUCTION_LOOKAHEAD_MINUTES", 60)
    cron_minutes = env_int("YAHOO_AUCTION_EXPECTED_CRON_INTERVAL_MINUTES", 10)
    sheet_name = os.getenv("YAHOO_AUCTION_RULES_SHEET", "yahoo_auction_rules")
    book = sheets.open_book(os.getenv("GOOGLE_CREDENTIALS"), os.getenv("SPREADSHEET_ID"))
    rule_rows, headers = sheets.get_yahoo_rule_rows_read_only(book, sheet_name)
    priority_rows = sheets.get_priority_rows_read_only(book)
    rules = parse_rules(
        rule_rows, headers,
        env_int("YAHOO_AUCTION_DEFAULT_MINIMUM_PROFIT", 5000),
        float(os.getenv("YAHOO_AUCTION_DEFAULT_SALE_FEE_RATE", "0.10")),
    )
    state = database.load_scheduler_state(connection)
    selected = select_rules(rules, state, max_rules)
    projection = cycle_projection(len(rules), max_rules, cron_minutes)
    print(f"YAHOO_SHADOW_CYCLE: rules={len(rules)} per_run={max_rules} cron_minutes={cron_minutes} projected_minutes={projection['minutes']}")
    if projection["minutes"] > lookahead:
        print(f"YAHOO_SHADOW_CYCLE_WARNING: projected_minutes={projection['minutes']} lookahead_minutes={lookahead}")
    records, attempted, fetched, details, errors, ending, processed_items = [], [], 0, 0, 0, 0, 0
    grouped = {}
    for rule in selected:
        grouped.setdefault(rule.query, []).append(rule)
    stop = False
    for query, query_rules in grouped.items():
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
            fetched += len(items)
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
        for item in items:
            if processed_items >= max_items:
                stop = True
                break
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
                result = evaluate(rule, item.price, shipping, status_class, p_limit)
                records.append(make_record(item, rule, result, remaining, end_at, p_limit, now))
            if stop:
                break
        if stop:
            break
        interval = env_int("YAHOO_AUCTION_REQUEST_INTERVAL_MS", 1000) / 1000
        if interval > 0:
            time.sleep(interval)
    chosen = choose_records(records, max_upserts, sample_limit)
    database.upsert_shadow_records(connection, chosen)
    active_ids = {r.rule_id for r in rules}
    updated_state = {k: v for k, v in state.items() if k in active_ids}
    database.save_scheduler_state(connection, mark_processed(updated_state, attempted))
    deleted = database.cleanup(connection, env_int("YAHOO_AUCTION_CLEANUP_BATCH_SIZE", 500))
    finished = datetime.now(timezone.utc)
    stats = {
        "run_id": uuid.uuid4(), "status": "BUDGET_EXCEEDED" if stop else "COMPLETED",
        "started_at": started, "finished_at": finished,
        "duration_ms": int((finished - started).total_seconds() * 1000),
        "enabled_rule_count": len(rules), "searched_rule_count": len(attempted),
        "fetched_item_count": fetched, "ending_item_count": ending,
        "candidate_count": sum(r["decision"] == "CANDIDATE" for r in chosen),
        "shadow_upsert_count": len(chosen), "detail_fetch_count": details,
        "http_error_count": errors, "projected_cycle_minutes": projection["minutes"],
        "error_code": None, "expires_at": finished + timedelta(days=env_int("YAHOO_AUCTION_RUN_STATS_RETENTION_DAYS", 90)),
    }
    database.save_run_stats(connection, stats)
    connection.commit()
    print(f"YAHOO_SHADOW_END: attempted_rules={len(attempted)} fetched={fetched} ending={ending} saved={len(chosen)} cleanup={deleted}")


start_clock = 0.0


def main():
    global start_clock, sheets, yahoo_client
    if not env_bool("YAHOO_AUCTION_ENABLED", False):
        print("YAHOO_SHADOW_DISABLED: external_access=0")
        return
    if not env_bool("YAHOO_AUCTION_SHADOW", True):
        print("YAHOO_SHADOW_DISABLED: shadow_flag=false external_access=0")
        return
    if env_bool("YAHOO_AUCTION_NOTIFY_ENABLED", False):
        print("YAHOO_SHADOW_SAFE_STOP: notify_enabled_must_be_false external_access=0")
        return
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    start_clock = time.monotonic()
    with database.connect(database_url) as connection:
        with database.advisory_lock(connection) as acquired:
            if not acquired:
                print("YAHOO_SHADOW_SKIPPED_LOCKED: sheets_access=0 yahoo_access=0")
                return
            # Heavy/external-client modules are intentionally loaded only after all
            # safety gates and the non-blocking DB lock have passed.
            sheets = importlib.import_module("sheets")
            yahoo_client = importlib.import_module("yahoo_client")
            run(connection)


if __name__ == "__main__":
    main()
