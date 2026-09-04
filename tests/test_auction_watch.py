import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from auction_watch import discovery_action, due_for_check, ending_result, should_cleanup


JST = timezone(timedelta(hours=9))


def item(kind="auction", current=1, buy_now=None, shipping=0):
    return SimpleNamespace(listing_type=kind, price=current, buy_now_price=buy_now, shipping_fee=shipping)


class AuctionWatchTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 4, 12, 0, tzinfo=JST)

    def test_case1_one_yen_five_days_is_watched_not_notified(self):
        self.assertEqual(discovery_action(item(), 50000), "watch")

    def test_case2_auction_40_minutes_not_due_but_30_minutes_is_notified(self):
        row = {"終了日時": (self.now + timedelta(minutes=40)).strftime("%Y-%m-%d %H:%M:%S"), "詳細確認済み": "0"}
        self.assertFalse(due_for_check(row, self.now, 30))
        row["終了日時"] = (self.now + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
        self.assertTrue(due_for_check(row, self.now, 30))
        self.assertTrue(ending_result(42000, 1000, 50000)["notify"])

    def test_case3_over_limit_is_not_notified(self):
        self.assertFalse(ending_result(50000, 1000, 50000)["notify"])
        self.assertFalse(ending_result(40000, None, 50000)["notify"])

    def test_case4_low_buy_now_is_immediate(self):
        self.assertEqual(discovery_action(item(buy_now=40000), 50000), "immediate")

    def test_case5_high_buy_now_is_watched(self):
        self.assertEqual(discovery_action(item(buy_now=70000), 50000), "watch")

    def test_case6_checked_item_is_not_rechecked(self):
        row = {"終了日時": (self.now + timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S"), "詳細確認済み": "1"}
        self.assertFalse(due_for_check(row, self.now, 30))

    def test_case7_ended_item_is_cleanup_target(self):
        self.assertTrue(should_cleanup("ended"))

    def test_case8_precious_metal_uses_same_limit_logic(self):
        result = ending_result(120000, 1000, 150000)
        self.assertTrue(result["notify"])
        self.assertEqual(result["level"], "🔥 有力候補")


if __name__ == "__main__":
    unittest.main()
