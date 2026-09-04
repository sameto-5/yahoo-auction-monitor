import unittest
from pathlib import Path

import _stubs  # noqa: F401
import yahoo_client


class YahooClientTests(unittest.TestCase):
    def test_search_parser(self):
        html = (Path(__file__).parent / "fixtures" / "yahoo_search_sample.html").read_text()
        items = yahoo_client.parse_search_results(html)
        self.assertEqual([item.item_id for item in items], ["x123456789", "z987654321"])
        self.assertEqual(items[0].price, 12800)
        self.assertEqual(items[1].price, 15000)

    def test_detail_status_is_conservative(self):
        self.assertEqual(yahoo_client.parse_detail_status("<p>この商品は落札されました</p>")[0], "sold")
        self.assertEqual(yahoo_client.parse_detail_status("<p>オークションは終了しました</p>")[0], "ended")
        self.assertEqual(yahoo_client.parse_detail_status("<button>入札する</button>")[0], "active")

    def test_search_url(self):
        url = yahoo_client.build_search_url("HXR-NX70J")
        self.assertIn("HXR-NX70J", url)
        self.assertIn("s1=new", url)

    def test_current_price_has_priority_over_one_yen_title(self):
        text = "1円〜 ジュエリー 現在 10,451円"
        self.assertEqual(yahoo_client.parse_price(text), 10451)

    def test_search_metadata_supplies_end_and_buy_now(self):
        html = '''<li class="Product">
          <div data-auction-endtime="1789052730" data-auction-buynowprice="40000"></div>
          <a href="https://auctions.yahoo.co.jp/jp/auction/x1">HXR-NX70J 現在 1円 即決 40,000円</a>
        </li>'''
        parsed = yahoo_client.parse_search_results(html)[0]
        self.assertEqual(parsed.buy_now_price, 40000)
        self.assertTrue(parsed.end_at)

    def test_detail_uses_json_ld_price_shipping_and_end(self):
        html = '''<script type="application/ld+json">{
          "offers":{"price":"10451","priceValidUntil":"2026-09-07T22:14:21+09:00",
          "availability":"https://schema.org/InStock",
          "shippingDetails":{"shippingRate":{"value":"0","currency":"JPY"}}}
        }</script><script>var pageData={"winPrice":"0","bids":"28"};</script>'''
        original = yahoo_client.fetch
        yahoo_client.fetch = lambda *args, **kwargs: html
        try:
            detail = yahoo_client.get_detail("https://example.test")
        finally:
            yahoo_client.fetch = original
        self.assertEqual(detail["current_price"], 10451)
        self.assertEqual(detail["shipping_fee"], 0)
        self.assertEqual(detail["end_at"], "2026-09-07 22:14:21")
        self.assertEqual(detail["listing_type"], "auction")


if __name__ == "__main__":
    unittest.main()
