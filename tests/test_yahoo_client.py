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


if __name__ == "__main__":
    unittest.main()
