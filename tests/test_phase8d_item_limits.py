import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import _stubs  # noqa: F401
import rule_loader
import shadow_monitor


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
        fake_sheets = SimpleNamespace(
            open_book=lambda *args: object(),
            get_yahoo_rule_rows_read_only=lambda *args: (
                [row("A1"), row("B1"), row("C1")], rule_loader.RULE_HEADERS
            ),
            get_priority_rows_read_only=lambda *args: [],
        )
        connection = SimpleNamespace(commit=lambda: None)
        env = {
            "YAHOO_AUCTION_MAX_RULES_PER_RUN": "3",
            "YAHOO_AUCTION_MAX_ITEMS_PER_RULE": "15",
            "YAHOO_AUCTION_MAX_ITEMS_PER_RUN": "50",
            "YAHOO_AUCTION_REQUEST_INTERVAL_MS": "0",
        }
        with patch.dict(os.environ, env, clear=True), \
             patch.object(shadow_monitor, "sheets", fake_sheets), \
             patch.object(shadow_monitor, "yahoo_client", search), \
             patch.object(shadow_monitor, "start_clock", 0), \
             patch.object(shadow_monitor.time, "monotonic", return_value=0), \
             patch.object(shadow_monitor.database, "load_scheduler_state", return_value={}), \
             patch.object(shadow_monitor.database, "upsert_shadow_records"), \
             patch.object(shadow_monitor.database, "save_scheduler_state") as save_state, \
             patch.object(shadow_monitor.database, "cleanup", return_value=(0, 0)), \
             patch.object(shadow_monitor.database, "save_run_stats") as save_stats:
            shadow_monitor.run(connection)
        stats = save_stats.call_args.args[1]
        self.assertEqual(stats["searched_rule_count"], 3)
        self.assertEqual(stats["fetched_item_count"], 45)
        self.assertEqual(len(save_state.call_args.args[1]), 3)


if __name__ == "__main__":
    unittest.main()
