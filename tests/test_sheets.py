import unittest

import _stubs  # noqa: F401
import sheets


class FakeWorksheet:
    def __init__(self, headers, rows):
        self.headers = headers
        self.rows = rows
        self.writes = []

    def row_values(self, number):
        return list(self.headers)

    def get_all_records(self):
        return list(self.rows)

    def update(self, **kwargs):
        self.writes.append(kwargs)


class FakeBook:
    def __init__(self, worksheet):
        self._worksheet = worksheet

    def worksheet(self, name):
        self.requested_name = name
        return self._worksheet


class SheetsTests(unittest.TestCase):
    def test_priority_items_is_read_only(self):
        worksheet = FakeWorksheet(
            sheets.PRIORITY_REQUIRED_HEADERS,
            [{"有効": 1, "型番": "HXR-NX70J"}],
        )
        rows = sheets.get_priority_rows_read_only(FakeBook(worksheet))
        self.assertEqual(rows[0]["型番"], "HXR-NX70J")
        self.assertEqual(worksheet.writes, [])

    def test_non_yahoo_sheet_creation_is_rejected(self):
        with self.assertRaises(ValueError):
            sheets.get_or_create_sheet(FakeBook(None), "item_history", ["商品ID"])


if __name__ == "__main__":
    unittest.main()
