import unittest
from datetime import datetime, timedelta, timezone

from sheets_shadow_store import ShadowSheetsStore


class FakeBook:
    def __init__(self):
        self.writes = []
        self.verify = None

    def values_batch_update(self, body):
        self.writes.append(body)

    def values_get(self, _range):
        return {"values": [self.verify]}


class SheetsStoreTests(unittest.TestCase):
    def test_lease_requires_readback_match(self):
        book = FakeBook()
        store = ShadowSheetsStore(book, lease_seconds=60)
        store.rows["yahoo_shadow_cursor"] = []
        book.verify = ["LEASE", "", "", "owner", "", "", "run", ""]
        lease = store.acquire_lease(owner_id="owner", run_id="run", now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertIsNotNone(lease)
        self.assertEqual(store.write_calls, 1)
        self.assertEqual(store.read_calls, 1)

    def test_active_lease_is_not_replaced(self):
        book = FakeBook()
        store = ShadowSheetsStore(book, lease_seconds=60)
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        store.rows["yahoo_shadow_cursor"] = [{
            "record_type": "LEASE", "expires_at": (now + timedelta(seconds=30)).isoformat(),
        }]
        self.assertIsNone(store.acquire_lease(now=now))
        self.assertEqual(book.writes, [])

    def test_upsert_deduplicates_auction_and_rule(self):
        store = ShadowSheetsStore(FakeBook())
        store.rows["yahoo_shadow_results"] = [{"auction_id": "a", "rule_id": "r", "decision": "OLD"}]
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        store.upsert_results([{"auction_id": "a", "rule_id": "r", "decision": "NEW"}], now)
        self.assertEqual(len(store.rows["yahoo_shadow_results"]), 1)
        self.assertEqual(store.rows["yahoo_shadow_results"][0]["decision"], "NEW")

    def test_cleanup_is_capped_at_500(self):
        store = ShadowSheetsStore(FakeBook())
        now = datetime(2026, 1, 2, tzinfo=timezone.utc)
        store.rows["yahoo_shadow_results"] = [
            {"auction_id": str(i), "rule_id": "r", "expires_at": (now - timedelta(days=1)).isoformat()}
            for i in range(600)
        ]
        self.assertEqual(store.cleanup(now, 500), 500)
        self.assertEqual(len(store.rows["yahoo_shadow_results"]), 100)


if __name__ == "__main__":
    unittest.main()
