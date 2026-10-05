from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


CURSOR_HEADERS = [
    "record_type", "rule_id", "last_processed_at", "owner_id",
    "acquired_at", "expires_at", "run_id", "updated_at",
]
MODEL_STATS_HEADERS = [
    "normalized_model", "model_group", "sample_count", "median_price",
    "p25_price", "p75_price", "confidence", "updated_at",
]
RESULT_HEADERS = [
    "auction_id", "rule_id", "title", "item_url", "normalized_model",
    "current_price", "buy_shipping", "buy_shipping_source",
    "estimated_total_cost", "expected_sale_price", "sale_fee",
    "estimated_sale_shipping", "other_cost", "minimum_profit",
    "estimated_profit", "explicit_purchase_limit", "priority_purchase_limit",
    "calculated_purchase_limit", "effective_purchase_limit",
    "minutes_remaining", "end_at", "decision", "reason_code",
    "reason_detail", "first_seen_at", "last_seen_at", "expires_at",
]
NOTIFICATION_HEADERS = [
    "auction_id", "rule_id", "notification_stage", "sent_at", "message_hash",
]
RETRY_HEADERS = [
    "auction_id", "rule_id", "notification_stage", "attempt_count", "next_attempt_at",
    "end_at", "payload_json", "last_error", "updated_at",
]
RUN_STATS_HEADERS = [
    "run_date", "run_count", "completed_count", "skipped_locked_count",
    "duration_ms_total", "enabled_rule_count_last", "searched_rule_count_total",
    "fetched_item_count_total", "ending_item_count_total", "candidate_count_total",
    "shadow_upsert_count_total", "detail_fetch_count_total", "http_error_count_total",
    "sheets_read_calls_total", "sheets_write_calls_total", "projected_cycle_minutes_last",
    "last_status", "last_run_id", "last_started_at", "last_finished_at",
]

SHEET_DEFINITIONS = {
    "yahoo_shadow_cursor": CURSOR_HEADERS,
    "yahoo_model_stats": MODEL_STATS_HEADERS,
    "yahoo_shadow_results": RESULT_HEADERS,
    "yahoo_run_stats": RUN_STATS_HEADERS,
    "yahoo_notification_state": NOTIFICATION_HEADERS,
    "yahoo_notification_retry": RETRY_HEADERS,
}


def _iso(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return "" if value is None else str(value)


def _parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _column_letter(number):
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _rows_to_dicts(values):
    if not values:
        return []
    headers = [str(v) for v in values[0]]
    return [
        {header: row[index] if index < len(row) else "" for index, header in enumerate(headers)}
        for row in values[1:]
        if any(str(cell).strip() for cell in row)
    ]


def _dicts_to_values(headers, rows, minimum_rows=0):
    values = [list(headers)]
    values.extend([[_iso(row.get(header)) for header in headers] for row in rows])
    while len(values) < minimum_rows:
        values.append([""] * len(headers))
    return values


@dataclass
class Lease:
    owner_id: str
    run_id: str
    acquired_at: datetime
    expires_at: datetime


class ShadowSheetsStore:
    """Bounded Sheets persistence for PHASE 8D; never touches non-yahoo tabs."""

    def __init__(self, book, lease_seconds=300):
        self.book = book
        self.lease_seconds = lease_seconds
        self.read_calls = 0
        self.write_calls = 0
        self.rows = {}
        self.original_lengths = {}
        self.lease = None

    def ensure_sheets(self):
        metadata = self.book.fetch_sheet_metadata()
        self.read_calls += 1
        existing = {entry["properties"]["title"] for entry in metadata.get("sheets", [])}
        for name, headers in SHEET_DEFINITIONS.items():
            if name not in existing:
                ws = self.book.add_worksheet(title=name, rows=1000, cols=len(headers))
                ws.update(values=[headers], range_name=f"A1:{_column_letter(len(headers))}1")
                self.write_calls += 2

    def load_all(self, rules_sheet="yahoo_auction_rules"):
        ranges = [
            f"'{rules_sheet}'!A:Z", "'priority_items'!A:Z",
            "'yahoo_shadow_cursor'!A:H", "'yahoo_model_stats'!A:H",
            "'yahoo_shadow_results'!A:AA", "'yahoo_run_stats'!A:T",
            "'yahoo_notification_state'!A:E", "'yahoo_notification_retry'!A:I",
        ]
        result = self.book.values_batch_get(ranges, params={"majorDimension": "ROWS"})
        self.read_calls += 1
        value_ranges = result.get("valueRanges", [])
        while len(value_ranges) < len(ranges):
            value_ranges.append({"values": []})
        names = [rules_sheet, "priority_items", *SHEET_DEFINITIONS]
        for name, payload in zip(names, value_ranges):
            values = payload.get("values", [])
            self.rows[name] = _rows_to_dicts(values)
            self.original_lengths[name] = max(1, len(values))
        self.rule_headers = list(value_ranges[0].get("values", [[]])[0]) if value_ranges[0].get("values") else []
        self.rules_sheet = rules_sheet
        return self.rows[rules_sheet], self.rule_headers, self.rows["priority_items"]

    def scheduler_state(self):
        return {
            row.get("rule_id", ""): row.get("last_processed_at", "")
            for row in self.rows.get("yahoo_shadow_cursor", [])
            if row.get("record_type") == "RULE" and row.get("rule_id")
        }

    def model_stats(self):
        from rule_loader import normalize
        return {
            normalize(row.get("normalized_model", "")): row
            for row in self.rows.get("yahoo_model_stats", [])
            if row.get("normalized_model")
        }

    def acquire_lease(self, owner_id=None, run_id=None, now=None):
        now = now or datetime.now(timezone.utc)
        active = next((r for r in self.rows.get("yahoo_shadow_cursor", []) if r.get("record_type") == "LEASE"), None)
        if active and (_parse_time(active.get("expires_at")) or now) > now:
            return None
        lease = Lease(owner_id or str(uuid.uuid4()), run_id or str(uuid.uuid4()), now, now + timedelta(seconds=self.lease_seconds))
        cursor_rows = [r for r in self.rows.get("yahoo_shadow_cursor", []) if r.get("record_type") != "LEASE"]
        cursor_rows.insert(0, {
            "record_type": "LEASE", "owner_id": lease.owner_id, "run_id": lease.run_id,
            "acquired_at": lease.acquired_at, "expires_at": lease.expires_at, "updated_at": now,
        })
        self.rows["yahoo_shadow_cursor"] = cursor_rows
        self._write_tables(["yahoo_shadow_cursor"])
        verify = self.book.values_get("'yahoo_shadow_cursor'!A2:H2")
        self.read_calls += 1
        values = verify.get("values", [[]])
        row = values[0] if values else []
        verified_run_id = row[6] if len(row) > 6 else ""
        verified_owner = row[3] if len(row) > 3 else ""
        if verified_run_id != lease.run_id or verified_owner != lease.owner_id:
            return None
        self.lease = lease
        return lease

    def apply_state(self, state, now):
        lease_rows = [r for r in self.rows.get("yahoo_shadow_cursor", []) if r.get("record_type") == "LEASE"]
        rule_rows = [
            {"record_type": "RULE", "rule_id": rule_id, "last_processed_at": stamp, "updated_at": now}
            for rule_id, stamp in sorted(state.items())
        ]
        self.rows["yahoo_shadow_cursor"] = lease_rows + rule_rows

    def upsert_results(self, records, now):
        current = {
            (str(r.get("auction_id", "")), str(r.get("rule_id", ""))): dict(r)
            for r in self.rows.get("yahoo_shadow_results", [])
            if r.get("auction_id") and r.get("rule_id")
        }
        for record in records:
            key = (str(record["auction_id"]), str(record["rule_id"]))
            first_seen = current.get(key, {}).get("first_seen_at") or now
            current[key] = {**record, "first_seen_at": first_seen, "last_seen_at": now}
        self.rows["yahoo_shadow_results"] = list(current.values())

    def cleanup(self, now, batch_size=500):
        rows = self.rows.get("yahoo_shadow_results", [])
        expired = [r for r in rows if (_parse_time(r.get("expires_at")) or datetime.max.replace(tzinfo=timezone.utc)) <= now]
        doomed = {id(r) for r in sorted(expired, key=lambda r: str(r.get("expires_at")))[:max(0, batch_size)]}
        self.rows["yahoo_shadow_results"] = [r for r in rows if id(r) not in doomed]
        return len(doomed)

    def sent_notification_keys(self):
        return {
            (str(r.get("auction_id", "")), str(r.get("rule_id", "")), str(r.get("notification_stage", "")))
            for r in self.rows.get("yahoo_notification_state", [])
            if r.get("auction_id") and r.get("rule_id") and r.get("notification_stage")
        }

    def retry_rows(self):
        return self.rows.get("yahoo_notification_retry", [])

    def mark_notification_sent(self, record, stage, sent_at, message_hash):
        key = (str(record["auction_id"]), str(record["rule_id"]), stage)
        rows = {
            (str(r.get("auction_id", "")), str(r.get("rule_id", "")), str(r.get("notification_stage", ""))): dict(r)
            for r in self.rows.get("yahoo_notification_state", [])
            if r.get("auction_id") and r.get("rule_id") and r.get("notification_stage")
        }
        rows[key] = {
            "auction_id": key[0], "rule_id": key[1], "notification_stage": stage,
            "sent_at": sent_at, "message_hash": message_hash,
        }
        self.rows["yahoo_notification_state"] = list(rows.values())
        self.remove_retry(key)

    def queue_retry(self, record, stage, payload, attempt_count, next_attempt_at, error, now):
        key = (str(record["auction_id"]), str(record["rule_id"]), stage)
        rows = {
            (str(r.get("auction_id", "")), str(r.get("rule_id", "")), str(r.get("notification_stage", ""))): dict(r)
            for r in self.rows.get("yahoo_notification_retry", [])
            if r.get("auction_id") and r.get("rule_id") and r.get("notification_stage")
        }
        rows[key] = {
            "auction_id": key[0], "rule_id": key[1], "notification_stage": stage,
            "attempt_count": attempt_count, "next_attempt_at": next_attempt_at,
            "end_at": record.get("end_at"), "payload_json": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            "last_error": str(error)[:100], "updated_at": now,
        }
        self.rows["yahoo_notification_retry"] = list(rows.values())

    def remove_retry(self, key):
        self.rows["yahoo_notification_retry"] = [
            r for r in self.rows.get("yahoo_notification_retry", [])
            if (str(r.get("auction_id", "")), str(r.get("rule_id", "")), str(r.get("notification_stage", ""))) != key
        ]

    def aggregate_run_stats(self, stats):
        key = stats["finished_at"].astimezone(timezone.utc).date().isoformat()
        by_date = {str(r.get("run_date")): dict(r) for r in self.rows.get("yahoo_run_stats", []) if r.get("run_date")}
        row = by_date.get(key, {"run_date": key})
        sums = {
            "run_count": 1,
            "completed_count": int(stats["status"] == "COMPLETED"),
            "skipped_locked_count": int(stats["status"] == "SKIPPED_LOCKED"),
            "duration_ms_total": stats.get("duration_ms", 0),
            "searched_rule_count_total": stats.get("searched_rule_count", 0),
            "fetched_item_count_total": stats.get("fetched_item_count", 0),
            "ending_item_count_total": stats.get("ending_item_count", 0),
            "candidate_count_total": stats.get("candidate_count", 0),
            "shadow_upsert_count_total": stats.get("shadow_upsert_count", 0),
            "detail_fetch_count_total": stats.get("detail_fetch_count", 0),
            "http_error_count_total": stats.get("http_error_count", 0),
            "sheets_read_calls_total": stats.get("sheets_read_calls", 0),
            "sheets_write_calls_total": stats.get("sheets_write_calls", 0),
        }
        for name, value in sums.items():
            row[name] = int(float(row.get(name) or 0)) + int(value)
        row.update({
            "enabled_rule_count_last": stats.get("enabled_rule_count", 0),
            "projected_cycle_minutes_last": stats.get("projected_cycle_minutes", 0),
            "last_status": stats["status"], "last_run_id": stats["run_id"],
            "last_started_at": stats["started_at"], "last_finished_at": stats["finished_at"],
        })
        by_date[key] = row
        self.rows["yahoo_run_stats"] = [by_date[k] for k in sorted(by_date)]

    def release_and_flush(self):
        self.rows["yahoo_shadow_cursor"] = [
            r for r in self.rows.get("yahoo_shadow_cursor", [])
            if r.get("record_type") != "LEASE" or r.get("run_id") != getattr(self.lease, "run_id", None)
        ]
        self._write_tables([
            "yahoo_shadow_cursor", "yahoo_shadow_results", "yahoo_run_stats",
            "yahoo_notification_state", "yahoo_notification_retry",
        ])
        self.lease = None

    def release_best_effort(self):
        if not self.lease:
            return
        try:
            self.rows["yahoo_shadow_cursor"] = [
                r for r in self.rows.get("yahoo_shadow_cursor", [])
                if r.get("record_type") != "LEASE" or r.get("run_id") != self.lease.run_id
            ]
            self._write_tables(["yahoo_shadow_cursor"])
        finally:
            self.lease = None

    def _write_tables(self, names):
        data = []
        for name in names:
            headers = SHEET_DEFINITIONS[name]
            minimum = self.original_lengths.get(name, 1)
            values = _dicts_to_values(headers, self.rows.get(name, []), minimum_rows=minimum)
            data.append({"range": f"'{name}'!A1:{_column_letter(len(headers))}{len(values)}", "values": values})
            self.original_lengths[name] = len(values)
        self.book.values_batch_update({"valueInputOption": "RAW", "data": data})
        self.write_calls += 1
