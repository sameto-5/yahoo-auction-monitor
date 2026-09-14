import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import _stubs  # noqa: F401
import monitor
import yahoo_client
from models import AuctionItem


class Phase8AUnitTests(unittest.TestCase):
    def test_watch_wait_is_only_between_requests(self):
        metrics = monitor.new_run_metrics()
        with patch.object(monitor.random, "uniform", return_value=6.25), patch.object(
            monitor.time, "sleep"
        ) as sleep:
            for index in range(3):
                if index:
                    monitor.wait_between_requests(metrics, "WATCH")
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(metrics["yahoo_watch_wait_ms_total"], 12500)

    def test_watch_merge_updates_reliable_values_and_preserves_flags(self):
        row = {
            "発見時現在価格": "5000", "送料": "1200", "即決価格": "",
            "終了日時": "", "ending_notified": "1", "詳細確認済み": "1",
        }
        item = AuctionItem("a", "new title", 6200, "url", shipping_fee=900,
                           buy_now_price=10000, end_at="2026-09-11 20:00:00")
        updated, changes = monitor.merge_existing_watch(row, item, "now")
        self.assertEqual(updated["発見時現在価格"], 6200)
        self.assertEqual(updated["送料"], 900)
        self.assertEqual(updated["終了日時"], "2026-09-11 20:00:00")
        self.assertEqual(updated["ending_notified"], "1")
        self.assertEqual(updated["詳細確認済み"], "1")
        self.assertTrue(changes["end_time_recovered"])

    def test_watch_merge_does_not_overwrite_with_none(self):
        row = {"発見時現在価格": "5000", "送料": "1200", "即決価格": "8000",
               "終了日時": "2026-09-11 20:00:00"}
        item = AuctionItem("a", "same", None, "url")
        updated, _ = monitor.merge_existing_watch(row, item, "now")
        for key in ("発見時現在価格", "送料", "即決価格", "終了日時"):
            self.assertEqual(updated[key], row[key])

    def test_notification_false_is_counted_as_failure(self):
        metrics = monitor.new_run_metrics()
        self.assertFalse(monitor.record_channel_result(metrics, "discord", False))
        self.assertEqual(metrics["yahoo_notification_success_count"], 0)
        self.assertEqual(metrics["yahoo_notification_failed_count"], 1)

    def test_line_channels_remain_independent(self):
        metrics = monitor.new_run_metrics()
        results = [monitor.record_channel_result(metrics, "line", value)
                   for value in (True, False)]
        self.assertEqual(results, [True, False])
        self.assertEqual(metrics["line_success_count"], 1)
        self.assertEqual(metrics["line_failed_count"], 1)

    def test_request_observer_counts_requests_errors_and_stage(self):
        metrics = monitor.new_run_metrics()
        observer = monitor.request_observer(metrics, "WATCH")
        observer("REQUEST", None)
        observer("HTTP_ERROR", 403)
        observer("REQUEST", None)
        observer("HTTP_ERROR", 429)
        observer("HTTP_ERROR", 503)
        observer("TIMEOUT", None)
        monitor.mark_rate_limit(metrics, "WATCH", yahoo_client.RateLimitError(429))
        self.assertEqual(metrics["yahoo_watch_detail_request_count"], 2)
        self.assertEqual(metrics["yahoo_total_request_count"], 2)
        self.assertEqual(metrics["yahoo_http_403_count"], 1)
        self.assertEqual(metrics["yahoo_http_429_count"], 1)
        self.assertEqual(metrics["yahoo_http_5xx_count"], 1)
        self.assertEqual(metrics["yahoo_timeout_count"], 1)
        self.assertEqual(metrics["yahoo_rate_limit_stage"], "WATCH")

    def test_fetch_403_and_429_are_distinguishable_and_not_retried(self):
        for code in (403, 429):
            response = Mock(status_code=code)
            events = []
            with self.subTest(code=code), patch.object(
                yahoo_client.requests, "get", return_value=response, create=True
            ) as get:
                with self.assertRaises(yahoo_client.RateLimitError) as raised:
                    yahoo_client.fetch("https://example.test", retries=2,
                                       request_observer=lambda *event: events.append(event))
                self.assertEqual(raised.exception.status_code, code)
                self.assertEqual(get.call_count, 1)
                self.assertIn(("HTTP_ERROR", code), events)

    def test_fetch_404_is_distinct_and_not_retried(self):
        response = Mock(status_code=404)
        events = []
        with patch.object(
            yahoo_client.requests, "get", return_value=response, create=True
        ) as get:
            with self.assertRaises(yahoo_client.SearchNotFoundError):
                yahoo_client.fetch(
                    "https://example.test", retries=2,
                    request_observer=lambda *event: events.append(event),
                )
        self.assertEqual(get.call_count, 1)
        self.assertIn(("HTTP_ERROR", 404), events)


class Phase8AMainTests(unittest.TestCase):
    def _run(self, *, search_side_effect=None, watch_rows=None, active_rows=None,
             detail_side_effect=None, status_side_effect=None,
             search_item=None, discord_success=True):
        watch_rows = list(watch_rows or [])
        active_rows = list(active_rows or [])
        sheets_by_name = {}

        def sheet(_book, name, _headers):
            value = SimpleNamespace(title=name)
            sheets_by_name[name] = value
            return value

        def records_by(ws, key):
            rows = {
                "yahoo_items": active_rows,
                "yahoo_notified_items": [],
                "yahoo_auction_watch": watch_rows,
                "yahoo_sold_fast_items": [],
                "yahoo_priority_candidates": [],
            }.get(ws.title, [])
            return rows, {str(row[key]): (index, row) for index, row in enumerate(rows, 2)
                          if row.get(key) not in (None, "")}

        rule = {"有効": "1", "優先度": "A", "ブランド": "", "型番": "MODEL1",
                "キーワード": "", "除外キーワード": "", "仕入れ上限": "20000"}
        rules = [rule]
        batch_size = 1
        if isinstance(search_side_effect, list) and len(search_side_effect) > 1:
            rules.append({**rule, "型番": "MODEL2"})
            batch_size = 2
        default_item = search_item or AuctionItem(
            "new", "MODEL1", 1000, "https://example.test/new",
            listing_type="auction", end_at="2026-09-11 23:59:00"
        )
        search = Mock(side_effect=search_side_effect) if search_side_effect else Mock(return_value=[default_item])
        detail = Mock(side_effect=detail_side_effect) if detail_side_effect else Mock(return_value={
            "status": "active", "current_price": 1000, "shipping_fee": 0,
        })
        status = Mock(side_effect=status_side_effect) if status_side_effect else Mock(return_value=("active", "ok"))
        updates = Mock()
        appends = Mock()
        saves = Mock()
        with patch.multiple(
            monitor,
            open_book=Mock(return_value=object()),
            get_priority_rows_read_only=Mock(return_value=rules),
            get_or_create_sheet=Mock(side_effect=sheet),
            load_state=Mock(return_value=({"status_cursor": "0"}, {})),
            records_by=Mock(side_effect=records_by),
            update_records=updates,
            append_records=appends,
            delete_rows=Mock(),
            save_states=saves,
            wait_between_requests=Mock(),
            send_discord=Mock(return_value=discord_success),
            send_line_notifications=Mock(return_value={"personal": True}),
            YAHOO_BATCH_SIZE=batch_size,
            YAHOO_ENDING_CHECKS_PER_RUN=10,
            MAX_STATUS_CHECKS_PER_RUN=30,
            YAHOO_ACTIVE_START_HOUR=0,
            YAHOO_ACTIVE_END_HOUR=24,
            DRY_RUN=False,
        ), patch.object(monitor.yahoo_client, "search", search), patch.object(
            monitor.yahoo_client, "get_detail", detail
        ), patch.object(monitor.yahoo_client, "check_status", status):
            monitor.main()
        return SimpleNamespace(search=search, detail=detail, status=status,
                               updates=updates, appends=appends, saves=saves)

    @staticmethod
    def due_watch(item_id):
        end_at = (datetime.now(monitor.JST) + timedelta(minutes=20)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        return {"商品ID": item_id, "商品名": "MODEL1", "商品URL": f"https://x/{item_id}",
                "priority_rule_id": "|model1||", "仕入上限": "20000", "送料": "0",
                "終了日時": end_at, "詳細確認済み": "0",
                "ending_notified": "0", "詳細取得失敗回数": "0", "状態": "watching"}

    @staticmethod
    def active(item_id):
        return {"商品ID": item_id, "商品名": "MODEL1", "商品URL": f"https://x/{item_id}",
                "出品状態": "active", "初回検知日時": "2026-09-11 00:00:00",
                "最新価格": "1000", "型番キー": "model1"}

    def test_search_403_or_429_stops_watch_and_status(self):
        for code in (403, 429):
            with self.subTest(code=code):
                run = self._run(
                    search_side_effect=yahoo_client.RateLimitError(code),
                    watch_rows=[self.due_watch("w1")], active_rows=[self.active("a1")],
                )
                self.assertEqual(run.detail.call_count, 0)
                self.assertEqual(run.status.call_count, 0)

    def test_watch_403_or_429_stops_remaining_watch_and_status(self):
        for code in (403, 429):
            with self.subTest(code=code):
                run = self._run(
                    watch_rows=[self.due_watch("w1"), self.due_watch("w2")],
                    active_rows=[self.active("a1")],
                    detail_side_effect=yahoo_client.RateLimitError(code),
                )
                self.assertEqual(run.detail.call_count, 1)
                self.assertEqual(run.status.call_count, 0)

    def test_status_rate_limit_stops_remaining_and_cursor_commits_prefix(self):
        for code in (403, 429):
            with self.subTest(code=code):
                run = self._run(
                    active_rows=[self.active("a1"), self.active("a2"), self.active("a3")],
                    status_side_effect=[("active", "ok"), yahoo_client.RateLimitError(code)],
                )
                self.assertEqual(run.status.call_count, 2)
                status_values = [call.args[2] for call in run.saves.call_args_list
                                 if "status_cursor" in call.args[2]]
                self.assertEqual(status_values[-1]["status_cursor"], "1")

    def test_rate_limit_still_saves_already_discovered_items(self):
        item = AuctionItem("saved", "MODEL1", 1000, "https://x/saved")
        run = self._run(search_side_effect=[ [item], yahoo_client.RateLimitError(403) ])
        appended_batches = [call.args[1] for call in run.appends.call_args_list]
        self.assertTrue(any(batch and batch[0].get("商品ID") == "saved"
                            for batch in appended_batches))

    def test_search_404_skips_only_that_query_and_continues(self):
        second = AuctionItem("second", "MODEL2", 1000, "https://x/second")
        run = self._run(search_side_effect=[
            yahoo_client.SearchNotFoundError("https://x/404"), [second]
        ])
        self.assertEqual(run.search.call_count, 2)
        appended_batches = [call.args[1] for call in run.appends.call_args_list]
        self.assertTrue(any(batch and batch[0].get("商品ID") == "second"
                            for batch in appended_batches))
        cursor_values = [call.args[2] for call in run.saves.call_args_list
                         if "search_cursors" in call.args[2]]
        self.assertTrue(cursor_values)

    def test_existing_watch_is_batch_updated_without_resetting_flags(self):
        active = self.active("same")
        watch = self.due_watch("same")
        watch["終了日時"] = ""
        watch["発見時現在価格"] = "500"
        watch["送料"] = "1200"
        watch["ending_notified"] = "1"
        watch["詳細確認済み"] = "1"
        item = AuctionItem(
            "same", "MODEL1 updated", 6200, "https://x/same",
            shipping_fee=900, end_at="2026-09-11 20:00:00",
            listing_type="auction",
        )
        run = self._run(active_rows=[active], watch_rows=[watch], search_item=item)
        batches = [call.args[1] for call in run.updates.call_args_list if call.args[1]]
        merged = [record for batch in batches for _, record in batch
                  if record.get("商品ID") == "same" and "ending_notified" in record]
        self.assertTrue(merged)
        self.assertEqual(merged[0]["発見時現在価格"], 6200)
        self.assertEqual(merged[0]["送料"], 900)
        self.assertEqual(merged[0]["終了日時"], "2026-09-11 20:00:00")
        self.assertEqual(merged[0]["ending_notified"], "1")
        self.assertEqual(merged[0]["詳細確認済み"], "1")

    def test_discord_false_does_not_create_notified_record(self):
        item = AuctionItem(
            "fixed", "MODEL1", 1000, "https://x/fixed",
            shipping_fee=0, listing_type="fixed",
        )
        run = self._run(search_item=item, discord_success=False)
        notified_batches = [
            call.args[1] for call in run.appends.call_args_list
            if getattr(call.args[0], "title", "") == "yahoo_notified_items"
        ]
        self.assertTrue(notified_batches)
        self.assertTrue(all(not batch for batch in notified_batches))

    def test_ending_notification_failure_remains_retry_eligible(self):
        watch = self.due_watch("ending")
        run = self._run(watch_rows=[watch], discord_success=False)
        watch_batches = [
            call.args[1] for call in run.updates.call_args_list
            if getattr(call.args[0], "title", "") == "yahoo_auction_watch"
            and call.args[1]
        ]
        updated = [record for batch in watch_batches for _, record in batch
                   if record.get("商品ID") == "ending"]
        self.assertTrue(updated)
        self.assertEqual(updated[-1]["ending_notified"], "0")
        self.assertEqual(updated[-1]["詳細確認済み"], "0")


if __name__ == "__main__":
    unittest.main()
