import unittest

from condition import classify_item_condition, prices_for_condition


class ConditionTests(unittest.TestCase):
    def test_required_condition_cases(self):
        cases = [
            ("動作確認済み", "working"),
            ("正常動作品", "working"),
            ("通電確認済み", "unchecked"),
            ("通電のみ", "unchecked"),
            ("動作未確認", "unchecked"),
            ("現状品", "unchecked"),
            ("ジャンク", "junk"),
            ("故障品", "junk"),
            ("通電確認済みですが動作未確認", "unchecked"),
            ("動作確認済みですが一部故障のためジャンク", "junk"),
            ("状態記載なし", "unknown"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                actual, reason = classify_item_condition(text)
                self.assertEqual(actual, expected)
                self.assertTrue(reason)

    def test_prices_and_legacy_fallback(self):
        rule = {
            "予想相場": "20,000", "仕入れ上限": "12,000",
            "動作品相場": "30,000", "動作品仕入れ上限": "18,000",
            "未確認/現状品相場": "22,000", "未確認/現状品仕入れ上限": "15,000",
            "ジャンク相場": "10,000", "ジャンク仕入れ上限": "",
        }
        self.assertEqual(prices_for_condition(rule, "working")["limit"], 18000)
        self.assertEqual(prices_for_condition(rule, "unchecked")["limit"], 15000)
        self.assertEqual(prices_for_condition(rule, "junk")["market"], 10000)
        self.assertEqual(prices_for_condition(rule, "junk")["limit"], 12000)
        self.assertEqual(prices_for_condition(rule, "unknown")["limit"], 12000)


if __name__ == "__main__":
    unittest.main()
