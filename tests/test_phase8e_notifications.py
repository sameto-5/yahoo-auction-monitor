import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import _stubs  # noqa: F401
import shadow_monitor
import yahoo_phase8e_notifier as phase8e
from sheets_shadow_store import ShadowSheetsStore


def candidate(**overrides):
    row = {
        "auction_id": "a1", "rule_id": "r1", "decision": "CANDIDATE",
        "title": "Camera X100", "item_url": "https://example.test/a1",
        "normalized_model": "x100", "current_price": 10000, "buy_shipping": 1000,
        "buy_shipping_source": "ACTUAL", "estimated_total_cost": 11000,
        "expected_sale_price": 25000, "effective_purchase_limit": 18000,
        "minimum_profit": 5000, "estimated_profit": 9000,
        "estimated_margin_rate": 0.36, "headroom_yen": 7000,
        "condition_class": "working", "minutes_remaining": 10,
        "end_at": datetime.now(timezone.utc) + timedelta(minutes=10),
        "reason_code": "PROFIT_AND_LIMIT_OK",
    }
    row.update(overrides)
    return row


class FakeBook:
    def __init__(self): self.writes = []
    def values_batch_update(self, body): self.writes.append(body)


class FakeStore(ShadowSheetsStore):
    def __init__(self):
        super().__init__(FakeBook())
        self.rows = {"yahoo_notification_state": [], "yahoo_notification_retry": []}


class Phase8ENotificationTests(unittest.TestCase):
    def setUp(self):
        self.stat = {"sample_count": "20", "confidence": "0.9"}

    def test_candidate_and_strong_classification(self):
        self.assertEqual(shadow_monitor.notification_stage(candidate(minutes_remaining=20), self.stat), "CANDIDATE")
        self.assertEqual(shadow_monitor.notification_stage(candidate(minutes_remaining=10), self.stat), "STRONG_CANDIDATE")

    def test_unsafe_records_never_notify(self):
        cases = [
            candidate(normalized_model=""), candidate(buy_shipping=None),
            candidate(condition_class="junk"), candidate(estimated_total_cost=19000),
            candidate(estimated_profit=4999), candidate(estimated_margin_rate=0.01),
            candidate(minutes_remaining=31),
        ]
        self.assertTrue(all(shadow_monitor.notification_stage(row, self.stat) is None for row in cases))
        self.assertIsNone(shadow_monitor.notification_stage(candidate(), {"sample_count": 2, "confidence": .9}))

    def test_same_stage_is_sent_only_once(self):
        store = FakeStore()
        sender = SimpleNamespace(
            format_notification=lambda *args: "message",
            send_discord=lambda *args: phase8e.DeliveryResult(True, 204),
        )
        with patch.dict(os.environ, {"YAHOO_DISCORD_WEBHOOK_URL": "https://discord.test/hook"}, clear=True), \
             patch.object(shadow_monitor, "notifier", sender):
            first = shadow_monitor.deliver_notifications(store, [candidate(minutes_remaining=20)], {"x100": self.stat}, datetime.now(timezone.utc))
            second = shadow_monitor.deliver_notifications(store, [candidate(minutes_remaining=20)], {"x100": self.stat}, datetime.now(timezone.utc))
        self.assertEqual(first["sent"], 1)
        self.assertEqual(second["sent"], 0)
        self.assertEqual(len(store.rows["yahoo_notification_state"]), 1)

    def test_stage_escalation_is_allowed(self):
        store = FakeStore()
        sender = SimpleNamespace(
            format_notification=lambda _r, stage, *_: stage,
            send_discord=lambda *args: phase8e.DeliveryResult(True, 204),
        )
        with patch.dict(os.environ, {"YAHOO_DISCORD_WEBHOOK_URL": "https://discord.test/hook"}, clear=True), \
             patch.object(shadow_monitor, "notifier", sender):
            shadow_monitor.deliver_notifications(store, [candidate(minutes_remaining=20)], {"x100": self.stat}, datetime.now(timezone.utc))
            result = shadow_monitor.deliver_notifications(store, [candidate(minutes_remaining=10)], {"x100": self.stat}, datetime.now(timezone.utc))
        self.assertEqual(result["sent"], 1)
        self.assertEqual({r["notification_stage"] for r in store.rows["yahoo_notification_state"]}, {"CANDIDATE", "STRONG_CANDIDATE"})

    def test_run_limit_preserves_overflow_for_next_evaluation(self):
        store = FakeStore()
        sender = SimpleNamespace(
            format_notification=lambda r, *_: r["auction_id"],
            send_discord=lambda *args: phase8e.DeliveryResult(True, 204),
        )
        rows = [candidate(auction_id=str(i), rule_id=str(i), minutes_remaining=20) for i in range(12)]
        stats = {"x100": self.stat}
        with patch.dict(os.environ, {"YAHOO_DISCORD_WEBHOOK_URL": "x", "YAHOO_AUCTION_MAX_NOTIFICATIONS_PER_RUN": "10"}, clear=True), \
             patch.object(shadow_monitor, "notifier", sender):
            result = shadow_monitor.deliver_notifications(store, rows, stats, datetime.now(timezone.utc))
        self.assertEqual(result["sent"], 10)
        self.assertEqual(len(store.rows["yahoo_notification_state"]), 10)

    def test_failed_delivery_queues_secret_free_retry_and_honors_retry_after(self):
        store = FakeStore()
        sender = SimpleNamespace(
            format_notification=lambda *args: "safe-message",
            send_discord=lambda *args: phase8e.DeliveryResult(False, 429, 120, "HTTP_429"),
        )
        now = datetime.now(timezone.utc)
        with patch.dict(os.environ, {"YAHOO_DISCORD_WEBHOOK_URL": "secret-url"}, clear=True), \
             patch.object(shadow_monitor, "notifier", sender):
            result = shadow_monitor.deliver_notifications(store, [candidate()], {"x100": self.stat}, now)
        self.assertEqual(result["rate_limits"], 1)
        self.assertEqual(store.rows["yahoo_notification_state"], [])
        retry = store.rows["yahoo_notification_retry"][0]
        self.assertNotIn("secret-url", retry["payload_json"])
        self.assertGreaterEqual(datetime.fromisoformat(str(retry["next_attempt_at"])), now + timedelta(seconds=120))

    def test_message_contains_required_fields_and_currency(self):
        message = phase8e.format_notification(candidate(), "STRONG_CANDIDATE", self.stat, datetime.now(timezone.utc))
        self.assertIn("【Yahoo仕入れ候補】", message)
        self.assertIn("現在価格：10,000円", message)
        self.assertIn("想定利益率：36.0%", message)
        self.assertIn("confidence：0.90", message)


if __name__ == "__main__":
    unittest.main()
