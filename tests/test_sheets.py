import unittest

import _stubs  # noqa: F401
import sheets


class FakeWorksheet:
    def __init__(self, headers, rows):
        self.headers = headers
        self.rows = rows
        self.writes = []
        self.header_reads = 0

    def row_values(self, number):
        self.header_reads += 1
        return list(self.headers)

    def get_all_records(self):
        return list(self.rows)

    def update(self, **kwargs):
        self.writes.append(kwargs)

    def append_rows(self, rows, **kwargs):
        self.writes.append({"rows": rows, **kwargs})


class FakeBook:
    def __init__(self, worksheet):
        self._worksheet = worksheet

    def worksheet(self, name):
        self.requested_name = name
        return self._worksheet


class SheetsTests(unittest.TestCase):
    def test_priority_items_is_read_only(self):
        sheets.configure_context(max_retries=0, backoff_base_seconds=0)
        worksheet = FakeWorksheet(
            sheets.PRIORITY_REQUIRED_HEADERS,
            [{header: ("HXR-NX70J" if header == "型番" else (1 if header == "有効" else ""))
             for header in sheets.PRIORITY_REQUIRED_HEADERS}],
        )
        rows = sheets.get_priority_rows_read_only(FakeBook(worksheet))
        self.assertEqual(rows[0]["型番"], "HXR-NX70J")
        self.assertEqual(worksheet.writes, [])
        self.assertEqual(sheets.CONTEXT.read_count, 1)

    def test_non_yahoo_sheet_creation_is_rejected(self):
        with self.assertRaises(ValueError):
            sheets.get_or_create_sheet(FakeBook(None), "item_history", ["商品ID"])

    def test_append_records_uses_one_request_and_cached_headers(self):
        worksheet = FakeWorksheet(["商品ID", "商品名"], [])
        worksheet._yahoo_headers = ["商品ID", "商品名"]
        sheets.append_records(worksheet, [
            {"商品ID": "1", "商品名": "A"},
            {"商品ID": "2", "商品名": "B"},
        ])
        self.assertEqual(worksheet.header_reads, 0)
        self.assertEqual(len(worksheet.writes), 1)
        self.assertEqual(worksheet.writes[0]["rows"], [["1", "A"], ["2", "B"]])


if __name__ == "__main__":
    unittest.main()
