import unittest

from sheets_access import SheetsAccessStopped, SheetsRunContext


class SheetsAccessTests(unittest.TestCase):
    def test_retry_count_includes_real_attempts(self):
        waits = []
        context = SheetsRunContext(max_retries=2, backoff_base_seconds=1, sleep=waits.append)
        with self.assertRaises(SheetsAccessStopped):
            context.request("read", "priority_items", lambda: (_ for _ in ()).throw(RuntimeError("429 quota exceeded")))
        self.assertEqual(context.read_count, 3)
        self.assertEqual(len(waits), 2)


if __name__ == "__main__":
    unittest.main()
