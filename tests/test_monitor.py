import unittest

import _stubs  # noqa: F401
import monitor
from models import AuctionItem


def make_rule(priority="A", model="HXR-NX70J", keyword="HXR-NX70J"):
    return {
        "有効": "1", "優先度": priority, "ブランド": "SONY", "型番": model,
        "キーワード": keyword, "除外キーワード": "ジャンク ロック不明",
        "予想相場": "30000", "仕入れ上限": "20000",
    }


class MonitorTests(unittest.TestCase):
    def test_query_deduplication(self):
        rules = monitor.prepare_rules([make_rule(), make_rule(priority="B")])
        groups = monitor.build_query_groups(rules)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["rules"]), 2)
        self.assertEqual(groups[0]["bucket"], "A")

    def test_distinct_model_and_keyword_make_two_queries(self):
        rules = monitor.prepare_rules([make_rule(keyword="SONY 業務用カメラ")])
        self.assertEqual(len(monitor.build_query_groups(rules)), 2)

    def test_priority_round_robin_reserves_lower_groups(self):
        rows = []
        for priority, count in (("A", 30), ("B", 10), ("C", 10)):
            for number in range(count):
                rows.append(make_rule(priority=priority, model=f"MODEL-{priority}-{number}", keyword=""))
        groups = monitor.build_query_groups(monitor.prepare_rules(rows))
        selected, cursors = monitor.select_query_groups(groups, 20, {})
        counts = {name: len([group for group in selected if group["bucket"] == name]) for name in ("A", "B", "OTHER")}
        self.assertEqual(counts, {"A": 12, "B": 5, "OTHER": 3})
        selected_again, _ = monitor.select_query_groups(groups, 20, cursors)
        self.assertNotEqual([g["key"] for g in selected], [g["key"] for g in selected_again])

    def test_match_and_exclusion(self):
        rule = monitor.prepare_rules([make_rule()])[0]
        item = AuctionItem("1", "SONY ビデオカメラ HXR NX70J", 10000, "https://example.test")
        self.assertTrue(monitor.item_matches_rule(item, rule))
        item.title += " ジャンク"
        self.assertFalse(monitor.item_matches_rule(item, rule))

    def test_priority_message_price_decision(self):
        rule = monitor.prepare_rules([make_rule()])[0]
        rule["未確認/現状品相場"] = "22000"
        rule["未確認/現状品仕入れ上限"] = "15000"
        item = AuctionItem("1", "HXR-NX70J 通電のみ", 12800, "https://example.test")
        item.status_class = "unchecked"
        item.status_reason = "タイトルに『通電のみ』を検出"
        message = monitor.build_priority_message(item, rule)
        self.assertIn("+2,200円", message)
        self.assertIn("✅ 仕入れ候補", message)

    def test_failed_query_can_be_mapped_back_to_group(self):
        groups = monitor.build_query_groups(monitor.prepare_rules([make_rule()]))
        failed_keys = {monitor.normalize_match_text("HXR-NX70J")}
        retries = [group for group in groups if group["key"] in failed_keys]
        self.assertEqual(len(retries), 1)


if __name__ == "__main__":
    unittest.main()
