import json
import math
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone

from condition import LABELS, classify_item_condition, prices_for_condition
from notifications import send_discord, send_line_notifications
from sheets import (
    append_records,
    get_or_create_sheet,
    get_priority_rows_read_only,
    load_state,
    open_book,
    records_by,
    save_states,
    update_records,
)
import sheets
from sheets_access import SheetsAccessStopped
import yahoo_client


JST = timezone(timedelta(hours=9))
YAHOO_BATCH_SIZE = int(os.getenv("YAHOO_BATCH_SIZE", os.getenv("MAX_SEARCHES_PER_RUN", "30")))
MAX_STATUS_CHECKS_PER_RUN = int(os.getenv("MAX_STATUS_CHECKS_PER_RUN", "30"))
YAHOO_ACTIVE_START_HOUR = int(os.getenv("YAHOO_ACTIVE_START_HOUR", "9"))
YAHOO_ACTIVE_END_HOUR = int(os.getenv("YAHOO_ACTIVE_END_HOUR", "21"))
SEARCH_DELAY_MIN_SECONDS = float(os.getenv("YAHOO_SEARCH_DELAY_MIN_SECONDS", os.getenv("SEARCH_DELAY_MIN_SECONDS", "5")))
SEARCH_DELAY_MAX_SECONDS = float(os.getenv("YAHOO_SEARCH_DELAY_MAX_SECONDS", os.getenv("SEARCH_DELAY_MAX_SECONDS", "8")))
HTTP_TIMEOUT_SECONDS = int(os.getenv("HTTP_TIMEOUT_SECONDS", "20"))
HTTP_RETRIES = int(os.getenv("YAHOO_HTTP_MAX_RETRIES", os.getenv("HTTP_RETRIES", "2")))
HTTP_BACKOFF_BASE_SECONDS = float(os.getenv("YAHOO_HTTP_BACKOFF_BASE_SECONDS", "5"))
SHEETS_MAX_RETRIES = int(os.getenv("SHEETS_MAX_RETRIES", "3"))
SHEETS_BACKOFF_BASE_SECONDS = float(os.getenv("SHEETS_BACKOFF_BASE_SECONDS", "3"))
FAST_SOLD_MINUTES = int(os.getenv("FAST_SOLD_MINUTES", "10"))
DRY_RUN = os.getenv("DRY_RUN", "0").strip().lower() in {"1", "true", "yes", "on"}

GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DISCORD_WEBHOOK_URL = os.getenv("YAHOO_DISCORD_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL")
DISCORD_PRIORITY_WEBHOOK_URL = os.getenv("YAHOO_DISCORD_PRIORITY_WEBHOOK_URL") or os.getenv("DISCORD_PRIORITY_WEBHOOK_URL")
DISCORD_SOLD_WEBHOOK_URL = os.getenv("YAHOO_DISCORD_SOLD_WEBHOOK_URL") or os.getenv("DISCORD_SOLD_WEBHOOK_URL")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")
LINE_GROUP_ID = os.getenv("LINE_GROUP_ID")
LINE_NOTIFY_MODE = os.getenv("LINE_NOTIFY_MODE", "personal").strip().lower()

ITEM_HEADERS = [
    "商品ID", "商品URL", "商品名", "初回検知日時", "最終確認日時", "初回価格", "最新価格",
    "出品状態", "状態理由", "出品者", "status_class", "status_reason", "型番キー",
]
HISTORY_HEADERS = [
    "商品ID", "商品URL", "日時", "イベント", "商品名", "価格", "出品状態", "出品者",
    "型番キー", "優先度", "status_class", "status_reason", "備考",
]
NOTIFIED_HEADERS = ["商品ID", "商品URL", "通知日時", "通知種別", "商品名", "価格"]
SOLD_HEADERS = [
    "商品ID", "商品URL", "商品名", "初回検知日時", "SOLD確認日時", "SOLDまで分数",
    "価格", "出品者", "型番キー", "status_class", "status_reason",
]
CANDIDATE_HEADERS = [
    "型番キー", "初回候補日時", "最終候補日時", "即売れ件数", "直近価格", "status_class", "根拠",
]


def now_jst():
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")


def normalize_match_text(value):
    text = str(value or "").lower()
    text = text.translate(str.maketrans("０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ", "0123456789abcdefghijklmnopqrstuvwxyz"))
    return re.sub(r"[\s\-_‐‑–—・./]+", "", text)


def is_enabled(value):
    return str(value or "").strip().lower() not in {"", "0", "false", "off", "no", "無効"}


def priority_bucket(value):
    text = str(value or "").strip().upper()
    if text == "A":
        return "A"
    if text == "B":
        return "B"
    return "OTHER"


def rule_id(rule):
    parts = [rule.get(name, "") for name in ("ブランド", "型番", "キーワード", "除外キーワード")]
    return "|".join(normalize_match_text(part) for part in parts)


def prepare_rules(rows):
    result = []
    for row in rows:
        if not is_enabled(row.get("有効")):
            continue
        if not row.get("型番") and not row.get("キーワード"):
            continue
        copied = dict(row)
        copied["_rule_id"] = rule_id(row)
        copied["_bucket"] = priority_bucket(row.get("優先度"))
        result.append(copied)
    return result


def queries_for_rule(rule):
    values = []
    for field in ("型番", "キーワード"):
        value = str(rule.get(field, "") or "").strip()
        if value and normalize_match_text(value) not in {normalize_match_text(item) for item in values}:
            values.append(value)
    return values


def build_query_groups(rules):
    groups = {}
    rank = {"A": 0, "B": 1, "OTHER": 2}
    for rule in rules:
        for query in queries_for_rule(rule):
            key = normalize_match_text(query)
            group = groups.setdefault(key, {"key": key, "query": query, "rules": [], "bucket": rule["_bucket"]})
            group["rules"].append(rule)
            if rank[rule["_bucket"]] < rank[group["bucket"]]:
                group["bucket"] = rule["_bucket"]
    return list(groups.values())


def _take_round_robin(items, count, cursor):
    if not items or count <= 0:
        return [], cursor
    count = min(count, len(items))
    start = cursor % len(items)
    selected = [items[(start + offset) % len(items)] for offset in range(count)]
    return selected, (start + count) % len(items)


def select_query_groups(groups, limit, cursors=None):
    cursors = dict(cursors or {})
    buckets = {name: sorted([g for g in groups if g["bucket"] == name], key=lambda g: g["key"]) for name in ("A", "B", "OTHER")}
    quotas = {
        "A": math.ceil(limit * 0.60),
        "B": math.floor(limit * 0.25),
        "OTHER": max(0, limit - math.ceil(limit * 0.60) - math.floor(limit * 0.25)),
    }
    selected = []
    selected_keys = set()
    for name in ("A", "B", "OTHER"):
        taken, cursors[name] = _take_round_robin(buckets[name], quotas[name], int(cursors.get(name, 0)))
        selected.extend(taken)
        selected_keys.update(group["key"] for group in taken)
    if len(selected) < limit:
        remaining = [group for name in ("A", "B", "OTHER") for group in buckets[name] if group["key"] not in selected_keys]
        selected.extend(remaining[:limit - len(selected)])
    return selected[:limit], cursors


def split_excludes(value):
    return [normalize_match_text(part) for part in re.split(r"[,、\n\s]+", str(value or "")) if normalize_match_text(part)]


def item_matches_rule(item, rule):
    title = normalize_match_text(item.title)
    candidates = [normalize_match_text(rule.get("型番")), normalize_match_text(rule.get("キーワード"))]
    candidates = [value for value in candidates if value]
    if not candidates or not any(value in title for value in candidates):
        return False
    if any(exclude in title for exclude in split_excludes(rule.get("除外キーワード"))):
        return False
    return True


def best_rule(item, rules):
    matches = [rule for rule in rules if item_matches_rule(item, rule)]
    matches.sort(key=lambda rule: ({"A": 0, "B": 1, "OTHER": 2}[rule["_bucket"]], -len(normalize_match_text(rule.get("型番") or rule.get("キーワード")))))
    item.matched_rule_ids = [rule["_rule_id"] for rule in matches]
    return matches[0] if matches else None


def format_money(value):
    return f"{value:,}円" if value is not None else "不明"


def build_normal_message(item):
    return (
        "【ヤフオク新着】\n\n"
        f"{item.title}\n"
        f"価格：{format_money(item.price)}\n"
        f"出品者：{item.seller or '取得不可'}\n"
        f"状態：{item.store_condition or '記載取得不可'}\n\n"
        f"{item.url}"
    )


def build_priority_message(item, rule):
    values = prices_for_condition(rule, item.status_class)
    price = item.price
    difference = values["limit"] - price if values["limit"] is not None and price is not None else None
    if difference is None:
        decision = "⚠️ 要確認"
    elif difference >= 0:
        decision = "✅ 仕入れ候補"
    else:
        decision = "❌ 上限超過"
    difference_text = f"{difference:+,}円" if difference is not None else "計算不可"
    return (
        "【ヤフオク 優先商品 新着】\n\n"
        f"商品：\n{item.title}\n\n"
        f"ブランド：{rule.get('ブランド', '')}\n"
        f"型番：{rule.get('型番') or rule.get('キーワード', '')}\n"
        f"現在価格：{format_money(price)}\n"
        f"出品者：{item.seller or '取得不可'}\n"
        f"商品状態：{item.store_condition or '記載取得不可'}\n\n"
        f"状態判定：{LABELS[item.status_class]}\n"
        f"判定理由：{item.status_reason}\n\n"
        f"対応相場：{format_money(values['market'])}\n"
        f"対応仕入れ上限：{format_money(values['limit'])}\n"
        f"上限との差：{difference_text}\n"
        f"判定：{decision}\n\n"
        f"URL：\n{item.url}"
    )


def channel_enabled(value, default):
    if str(value or "").strip() == "":
        return default
    return is_enabled(value)


def is_purchase_candidate(item, rule):
    if not rule or item.price is None:
        return False
    limit = prices_for_condition(rule, item.status_class)["limit"]
    return limit is not None and item.price <= limit


def should_notify_item(item, rule, notified_ids):
    return str(item.item_id) not in notified_ids and is_purchase_candidate(item, rule)


def committed_cursors(groups, original_cursors, completed_groups):
    result = dict(original_cursors or {})
    for bucket in ("A", "B", "OTHER"):
        bucket_groups = [group for group in groups if group["bucket"] == bucket]
        completed = sum(1 for group in completed_groups if group["bucket"] == bucket)
        if bucket_groups and completed:
            result[bucket] = (int(result.get(bucket, 0)) + completed) % len(bucket_groups)
    return result


def parse_datetime(value):
    return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)


def wait_between_requests():
    low = min(SEARCH_DELAY_MIN_SECONDS, SEARCH_DELAY_MAX_SECONDS)
    high = max(SEARCH_DELAY_MIN_SECONDS, SEARCH_DELAY_MAX_SECONDS)
    if high > 0:
        time.sleep(random.uniform(low, high))


def main():
    started = datetime.now(JST)
    print(f"RUN_START: {started.strftime('%Y-%m-%d %H:%M:%S')}")
    if not (YAHOO_ACTIVE_START_HOUR <= started.hour < YAHOO_ACTIVE_END_HOUR):
        print("SCHEDULE_SKIP: Yahoo active hours outside sheets_read=0 sheets_write=0")
        return
    sheets.configure_context(SHEETS_MAX_RETRIES, SHEETS_BACKOFF_BASE_SECONDS)
    book = open_book(GOOGLE_CREDENTIALS, SPREADSHEET_ID)
    rules = prepare_rules(get_priority_rows_read_only(book))
    if not rules:
        print("有効なpriority_itemsがありません")
        return

    state_ws = get_or_create_sheet(book, "yahoo_monitor_state", ["キー", "値"])
    items_ws = get_or_create_sheet(book, "yahoo_items", ITEM_HEADERS)
    history_ws = get_or_create_sheet(book, "yahoo_item_history", HISTORY_HEADERS)
    notified_ws = get_or_create_sheet(book, "yahoo_notified_items", NOTIFIED_HEADERS)
    sold_ws = get_or_create_sheet(book, "yahoo_sold_fast_items", SOLD_HEADERS)
    candidates_ws = get_or_create_sheet(book, "yahoo_priority_candidates", CANDIDATE_HEADERS)
    state, state_rows = load_state(state_ws)

    try:
        cursors = json.loads(state.get("search_cursors", "{}"))
    except (TypeError, ValueError):
        cursors = {}
    groups = build_query_groups(rules)
    original_cursors = dict(cursors)
    selected, _proposed_cursors = select_query_groups(groups, YAHOO_BATCH_SIZE, cursors)
    cursor_selection_ids = {id(group) for group in selected}
    try:
        previous_failed = json.loads(state.get("failed_queries", "[]"))
    except (TypeError, ValueError):
        previous_failed = []
    retry_keys = {normalize_match_text(query) for query in previous_failed[:YAHOO_BATCH_SIZE]}
    retry_groups = [group for group in groups if group["key"] in retry_keys]
    selected = (retry_groups + [group for group in selected if group["key"] not in retry_keys])[:YAHOO_BATCH_SIZE]
    item_rows, known_items = records_by(items_ws, "商品ID")
    _, notified_items = records_by(notified_ws, "商品ID")
    found = {}
    failed_queries = []
    rate_limited = False
    completed_groups = []
    http_errors = 0
    for index, group in enumerate(selected):
        if index:
            wait_between_requests()
        try:
            results = yahoo_client.search(
                group["query"], timeout=HTTP_TIMEOUT_SECONDS, retries=HTTP_RETRIES,
                backoff_base_seconds=HTTP_BACKOFF_BASE_SECONDS,
            )
            print(f"SEARCH_OK: {group['query']} {len(results)}件")
            for item in results:
                found.setdefault(item.item_id, item)
            completed_groups.append(group)
        except yahoo_client.RateLimitError as error:
            print(f"SEARCH_429_STOP: {error}")
            failed_queries.extend(item["query"] for item in selected[index:])
            rate_limited = True
            http_errors += 1
            break
        except Exception as error:
            print(f"SEARCH_ERROR: {group['query']}: {error}")
            failed_queries.extend(item["query"] for item in selected[index:])
            http_errors += 1
            break
    discovered_at = now_jst()
    new_item_records = []
    item_updates = []
    history_records = []
    notification_jobs = []
    for item in found.values():
        try:
            status_class, status_reason = classify_item_condition(item.title, item.description, item.store_condition)
            item.status_class = status_class
            item.status_reason = status_reason
            rule = best_rule(item, rules)
            model_key = normalize_match_text((rule or {}).get("型番") or (rule or {}).get("キーワード"))
            existing = known_items.get(item.item_id)
            record = {
                "商品ID": item.item_id, "商品URL": item.url, "商品名": item.title,
                "初回検知日時": existing[1].get("初回検知日時") if existing else discovered_at,
                "最終確認日時": discovered_at,
                "初回価格": existing[1].get("初回価格") if existing else (item.price or ""),
                "最新価格": item.price or "", "出品状態": "active", "状態理由": "検索結果に掲載",
                "出品者": item.seller, "status_class": item.status_class,
                "status_reason": item.status_reason, "型番キー": model_key,
            }
            if existing:
                item_updates.append((existing[0], record))
                continue
            new_item_records.append(record)
            history_records.append({
                "商品ID": item.item_id, "商品URL": item.url, "日時": discovered_at, "イベント": "DISCOVERED",
                "商品名": item.title, "価格": item.price or "", "出品状態": "active", "出品者": item.seller,
                "型番キー": model_key, "優先度": rule.get("優先度", "") if rule else "",
                "status_class": item.status_class, "status_reason": item.status_reason,
            })
            if should_notify_item(item, rule, notified_items):
                notification_jobs.append((item, rule))
        except Exception as error:
            print(f"ITEM_ERROR: {item.item_id}: {error}")

    # Google Sheets APIの呼び出し回数を抑えるため、商品単位ではなくシート単位で一括保存する。
    update_records(items_ws, item_updates)
    append_records(items_ws, new_item_records)
    append_records(history_ws, history_records)

    notified_records = []
    for item, rule in notification_jobs:
        try:
            send_discord(DISCORD_WEBHOOK_URL, build_normal_message(item), DRY_RUN)
            if rule:
                message = build_priority_message(item, rule)
                print(f"[CONDITION] id={item.item_id} status={item.status_class} reason={item.status_reason}")
                values = prices_for_condition(rule, item.status_class)
                difference = values["limit"] - item.price if values["limit"] is not None and item.price is not None else None
                print(f"[PRICE] price={item.price} market={values['market']} buy_limit={values['limit']} difference={difference}")
                if channel_enabled(rule.get("Discord通知"), True):
                    send_discord(DISCORD_PRIORITY_WEBHOOK_URL, message, DRY_RUN)
                if channel_enabled(rule.get("LINE通知"), False):
                    send_line_notifications(
                        LINE_CHANNEL_ACCESS_TOKEN,
                        LINE_USER_ID,
                        LINE_GROUP_ID,
                        LINE_NOTIFY_MODE,
                        message,
                        DRY_RUN,
                    )
            notified_records.append({
                "商品ID": item.item_id, "商品URL": item.url, "通知日時": discovered_at,
                "通知種別": "PRIORITY" if rule else "NORMAL", "商品名": item.title, "価格": item.price or "",
            })
        except Exception as error:
            print(f"NOTIFICATION_ERROR: {item.item_id}: {error}")
    append_records(notified_ws, notified_records)

    active = [(row_number, row) for row_number, row in known_items.values() if row.get("出品状態") == "active"]
    status_cursor = int(state.get("status_cursor", "0") or 0)
    checks, next_status_cursor = _take_round_robin(active, MAX_STATUS_CHECKS_PER_RUN, status_cursor)
    sold_rows, sold_ids = records_by(sold_ws, "商品ID")
    candidate_rows, candidate_keys = records_by(candidates_ws, "型番キー")
    status_item_updates = []
    status_history_records = []
    sold_records = []
    candidate_records = []
    for index, (row_number, row) in enumerate(checks):
        if rate_limited:
            break
        if index or selected:
            wait_between_requests()
        try:
            status, reason = yahoo_client.check_status(row.get("商品URL"), HTTP_TIMEOUT_SECONDS, 0)
        except yahoo_client.RateLimitError:
            print("STATUS_429_STOP")
            http_errors += 1
            break
        except Exception as error:
            print(f"STATUS_ERROR: {row.get('商品ID')}: {error}")
            http_errors += 1
            continue
        if status not in {"sold", "ended"}:
            continue
        updated = dict(row)
        updated["出品状態"] = status
        updated["状態理由"] = reason
        updated["最終確認日時"] = now_jst()
        status_item_updates.append((row_number, updated))
        sold_at = now_jst()
        try:
            minutes = max(0, int((parse_datetime(sold_at) - parse_datetime(row.get("初回検知日時"))).total_seconds() / 60))
        except (TypeError, ValueError):
            minutes = None
        status_history_records.append({
            "商品ID": row.get("商品ID"), "商品URL": row.get("商品URL"), "日時": sold_at,
            "イベント": "SOLD" if status == "sold" else "ENDED_UNKNOWN",
            "商品名": row.get("商品名"), "価格": row.get("最新価格"), "出品状態": status,
            "出品者": row.get("出品者"), "型番キー": row.get("型番キー"),
            "status_class": row.get("status_class"), "status_reason": row.get("status_reason"), "備考": reason,
        })
        if status != "sold" or minutes is None or minutes > FAST_SOLD_MINUTES or str(row.get("商品ID")) in sold_ids:
            continue
        sold_record = {
            "商品ID": row.get("商品ID"), "商品URL": row.get("商品URL"), "商品名": row.get("商品名"),
            "初回検知日時": row.get("初回検知日時"), "SOLD確認日時": sold_at, "SOLDまで分数": minutes,
            "価格": row.get("最新価格"), "出品者": row.get("出品者"), "型番キー": row.get("型番キー"),
            "status_class": row.get("status_class"), "status_reason": row.get("status_reason"),
        }
        sold_records.append(sold_record)
        send_discord(DISCORD_SOLD_WEBHOOK_URL, (
            f"🔥 ヤフオク {FAST_SOLD_MINUTES}分以内SOLD\n{row.get('商品名')}\n"
            f"価格：{format_money(int(row['最新価格'])) if str(row.get('最新価格', '')).isdigit() else row.get('最新価格')}\n"
            f"初回検知から{minutes}分\n{row.get('商品URL')}"
        ), DRY_RUN)
        key = str(row.get("型番キー") or "")
        if key and key not in candidate_keys:
            candidate_records.append({
                "型番キー": key, "初回候補日時": sold_at, "最終候補日時": sold_at, "即売れ件数": 1,
                "直近価格": row.get("最新価格"), "status_class": row.get("status_class"),
                "根拠": f"{FAST_SOLD_MINUTES}分以内SOLD",
            })
            candidate_keys[key] = (0, candidate_records[-1])

    update_records(items_ws, status_item_updates)
    append_records(history_ws, status_history_records)
    append_records(sold_ws, sold_records)
    append_records(candidates_ws, candidate_records)
    cursor_completed = [group for group in completed_groups if id(group) in cursor_selection_ids]
    final_cursors = committed_cursors(groups, original_cursors, cursor_completed)
    save_states(state_ws, state_rows, {
        "failed_queries": json.dumps(failed_queries, ensure_ascii=False),
        "status_cursor": str(next_status_cursor),
        "last_completed_at": now_jst(),
    })
    # 履歴・商品・補助状態の保存がすべて成功した後、最後に検索カーソルをcommitする。
    save_states(state_ws, state_rows, {
        "search_cursors": json.dumps(final_cursors, ensure_ascii=False),
    })

    print(
        f"完了: 有効ルール={len(rules)} 検索語={len(groups)} 今回検索={len(selected)} "
        f"取得商品={len(found)} 失敗検索={len(failed_queries)}"
    )
    print(
        f"CURSOR_COMMIT: start={json.dumps(original_cursors, ensure_ascii=False)} "
        f"end={json.dumps(final_cursors, ensure_ascii=False)} completed={len(completed_groups)}"
    )
    print(
        f"RUN_END: {now_jst()} candidates={len(notification_jobs)} notifications={len(notified_records)} "
        f"http_errors={http_errors} sheets_read={sheets.CONTEXT.read_count} "
        f"sheets_write={sheets.CONTEXT.write_count}"
    )


if __name__ == "__main__":
    try:
        main()
    except SheetsAccessStopped:
        print(
            f"RUN_SAFE_STOP: {now_jst()} cursor_not_committed=1 "
            f"sheets_read={sheets.CONTEXT.read_count} sheets_write={sheets.CONTEXT.write_count}"
        )
