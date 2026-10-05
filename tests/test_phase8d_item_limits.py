import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import _stubs  # noqa: F401
import rule_loader
import shadow_monitor
from datetime import datetime, timedelta, timezone


def row(rule_id):
    return {
        "rule_id": rule_id, "enabled": "1", "priority": "A",
        "group_name": "camera", "query": rule_id, "model": rule_id,
        "include_words": "", "exclude_words": "",
        "condition_policy": "working_only", "expected_sale_price": "50000",
        "max_purchase_price": "45000", "estimated_buy_shipping": "1000",
        "group_default_buy_shipping": "1500", "estimated_sale_shipping": "1000",
        "sale_fee_rate": "0.1", "other_cost": "0", "minimum_profit": "5000",
        "notify_minutes": "60", "priority_lookup_model": rule_id, "notes": "",
    }


class ShadowItemLimitTests(unittest.TestCase):
    def test_global_budget_is_shared_without_starving_the_third_query(self):
        self.assertEqual(shadow_monitor.distribute_query_limits(3, 30, 15), [10, 10, 10])
        self.assertEqual(shadow_monitor.distribute_query_limits(3, 50, 15), [15, 15, 15])

    def test_three_rules_each_process_at_most_fifteen_results(self):
        search = SimpleNamespace(
            search=lambda query, **kwargs: [
                SimpleNamespace(
                    item_id=f"{query}-{index}", title=query, url="https://example.test/item",
                    price=1000, shipping_fee=1000, end_at="2026-09-16 12:00:00",
                    description="", store_condition="",
                )
                for index in range(50)
            ],
            RateLimitError=RuntimeError,
        )
        class FakeStore:
            rules_sheet = "yahoo_auction_rules"
            rule_headers = rule_loader.RULE_HEADERS
            rows = {
                "yahoo_auction_rules": [row("A1"), row("B1"), row("C1")],
                "priority_items": [], "yahoo_model_stats": [],
            }
            lease = SimpleNamespace(run_id="run-1")
            read_calls = 3
            write_calls = 1
            saved_state = None
            stats = None

            def scheduler_state(self): return {}
            def model_stats(self): return {}
            def upsert_results(self, records, now): self.records = records
            def apply_state(self, state, now): self.saved_state = state
            def cleanup(self, now, batch_size): return 0
            def aggregate_run_stats(self, stats): self.stats = stats
            def release_and_flush(self): self.write_calls += 1

        store = FakeStore()
        env = {
            "YAHOO_AUCTION_MAX_RULES_PER_RUN": "3",
            "YAHOO_AUCTION_MAX_ITEMS_PER_RULE": "15",
            "YAHOO_AUCTION_MAX_ITEMS_PER_RUN": "50",
            "YAHOO_AUCTION_REQUEST_INTERVAL_MS": "0",
        }
        with patch.dict(os.environ, env, clear=True), \
             patch.object(shadow_monitor, "yahoo_client", search), \
             patch.object(shadow_monitor, "start_clock", 0), \
             patch.object(shadow_monitor.time, "monotonic", return_value=0):
            shadow_monitor.run(store)
        stats = store.stats
        self.assertEqual(stats["searched_rule_count"], 3)
        self.assertEqual(stats["fetched_item_count"], 45)
        self.assertEqual(len(store.saved_state), 3)

    def test_missing_model_stats_downgrades_candidate(self):
        rule = rule_loader.parse_rules([row("A1")], rule_loader.RULE_HEADERS)[0]
        decision = shadow_monitor.model_data_decision(rule, {}, datetime.now(timezone.utc))
        self.assertEqual(decision, ("DATA_INSUFFICIENT", "MODEL_STATS_MISSING"))

    def test_fresh_model_stats_are_accepted(self):
        rule = rule_loader.parse_rules([row("A1")], rule_loader.RULE_HEADERS)[0]
        now = datetime.now(timezone.utc)
        stats = {rule_loader.normalize(rule.model): {
            "sample_count": "10", "confidence": "0.8", "updated_at": (now - timedelta(days=1)).isoformat(),
        }}
        self.assertEqual(shadow_monitor.model_data_decision(rule, stats, now), (None, None))

    def test_deleted_rule_is_ignored(self):
        deleted = row("A1")
        deleted["deleted"] = "1"
        headers = rule_loader.RULE_HEADERS + ["deleted"]
        self.assertEqual(rule_loader.parse_rules([deleted], headers), [])


if __name__ == "__main__":
    unittest.main()
