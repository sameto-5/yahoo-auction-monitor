import json

import gspread
from google.oauth2.service_account import Credentials
from sheets_access import SheetsRunContext


PRIORITY_REQUIRED_HEADERS = ["有効", "優先度", "ブランド", "型番", "キーワード", "除外キーワード"]
CONTEXT = SheetsRunContext()


def configure_context(max_retries=3, backoff_base_seconds=3, sleep=None):
    global CONTEXT
    kwargs = {"max_retries": max_retries, "backoff_base_seconds": backoff_base_seconds}
    if sleep is not None:
        kwargs["sleep"] = sleep
    CONTEXT = SheetsRunContext(**kwargs)
    return CONTEXT


def _key(worksheet):
    return getattr(worksheet, "title", None) or id(worksheet)


def _read(operation, action):
    return CONTEXT.request("read", operation, action)


def _write(operation, action):
    return CONTEXT.request("write", operation, action)


def cached_headers(worksheet):
    key = ("headers", _key(worksheet))
    if key not in CONTEXT.cache:
        records_key = ("records", _key(worksheet))
        if records_key not in CONTEXT.cache:
            rows = _read(f"{_key(worksheet)}.get_all_records", worksheet.get_all_records)
            CONTEXT.cache[records_key] = [dict(row) for row in rows]
        if CONTEXT.cache[records_key]:
            CONTEXT.cache[key] = list(CONTEXT.cache[records_key][0].keys())
        else:
            CONTEXT.cache[key] = list(_read(f"{_key(worksheet)}.row_values", lambda: worksheet.row_values(1)))
    return list(CONTEXT.cache[key])


def cached_records(worksheet):
    key = ("records", _key(worksheet))
    if key not in CONTEXT.cache:
        rows = _read(f"{_key(worksheet)}.get_all_records", worksheet.get_all_records)
        CONTEXT.cache[key] = [dict(row) for row in rows]
    return [dict(row) for row in CONTEXT.cache[key]]


def open_book(credentials_json, spreadsheet_id):
    if not credentials_json or not spreadsheet_id:
        raise RuntimeError("GOOGLE_CREDENTIALS または SPREADSHEET_ID が未設定です")
    info = json.loads(credentials_json)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    credentials = Credentials.from_service_account_info(info, scopes=scopes)
    client = gspread.authorize(credentials)
    return _read("spreadsheets.open_by_key", lambda: client.open_by_key(spreadsheet_id))


def get_priority_rows_read_only(book):
    worksheet = book.worksheet("priority_items")
    headers = cached_headers(worksheet)
    missing = [name for name in PRIORITY_REQUIRED_HEADERS if name not in headers]
    if missing:
        raise RuntimeError(f"priority_items 必須列不足: {', '.join(missing)}")
    return cached_records(worksheet)


def get_or_create_sheet(book, name, headers, rows=1000):
    if not name.startswith("yahoo_"):
        raise ValueError("ヤフオク側が作成できるのは yahoo_ シートだけです")
    try:
        worksheet = book.worksheet(name)
    except gspread.WorksheetNotFound:
        worksheet = _write(
            f"{name}.add_worksheet",
            lambda: book.add_worksheet(title=name, rows=rows, cols=len(headers)),
        )
    worksheet._yahoo_headers = ensure_headers_append_only(worksheet, headers)
    return worksheet


def ensure_headers_append_only(worksheet, required_headers):
    current = cached_headers(worksheet)
    if not current:
        _write(f"{_key(worksheet)}.update_headers", lambda: worksheet.update(values=[required_headers], range_name="A1"))
        CONTEXT.cache[("headers", _key(worksheet))] = list(required_headers)
        return list(required_headers)
    missing = [header for header in required_headers if header not in current]
    if not missing:
        return current
    final = current + missing
    if worksheet.col_count < len(final):
        _write(f"{_key(worksheet)}.resize", lambda: worksheet.resize(cols=len(final)))
    _write(f"{_key(worksheet)}.update_headers", lambda: worksheet.update(values=[final], range_name=f"A1:{column_letter(len(final))}1"))
    CONTEXT.cache[("headers", _key(worksheet))] = list(final)
    return final


def column_letter(number):
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def append_record(worksheet, record):
    headers = getattr(worksheet, "_yahoo_headers", None) or worksheet.row_values(1)
    worksheet._yahoo_headers = headers
    _write(f"{_key(worksheet)}.append_row", lambda: worksheet.append_row([record.get(header, "") for header in headers], value_input_option="RAW"))


def append_records(worksheet, records):
    if not records:
        return
    headers = getattr(worksheet, "_yahoo_headers", None) or worksheet.row_values(1)
    worksheet._yahoo_headers = headers
    rows = [[record.get(header, "") for header in headers] for record in records]
    _write(f"{_key(worksheet)}.append_rows", lambda: worksheet.append_rows(rows, value_input_option="RAW"))
    cached = CONTEXT.cache.get(("records", _key(worksheet)))
    if cached is not None:
        cached.extend(dict(record) for record in records)


def update_record(worksheet, row_number, record):
    headers = getattr(worksheet, "_yahoo_headers", None) or worksheet.row_values(1)
    worksheet._yahoo_headers = headers
    values = [record.get(header, "") for header in headers]
    _write(f"{_key(worksheet)}.update", lambda: worksheet.update(
        values=[values],
        range_name=f"A{row_number}:{column_letter(len(headers))}{row_number}",
    ))


def update_records(worksheet, updates):
    if not updates:
        return
    headers = getattr(worksheet, "_yahoo_headers", None) or worksheet.row_values(1)
    worksheet._yahoo_headers = headers
    last_column = column_letter(len(headers))
    payload = []
    for row_number, record in updates:
        payload.append({
            "range": f"A{row_number}:{last_column}{row_number}",
            "values": [[record.get(header, "") for header in headers]],
        })
    _write(f"{_key(worksheet)}.batch_update", lambda: worksheet.batch_update(payload))


def records_by(worksheet, key):
    rows = cached_records(worksheet)
    return rows, {
        str(row.get(key)): (index, row)
        for index, row in enumerate(rows, start=2)
        if row.get(key) not in (None, "")
    }


def load_state(worksheet):
    rows = cached_records(worksheet)
    values = {}
    row_numbers = {}
    for index, row in enumerate(rows, start=2):
        key = str(row.get("キー", "") or "")
        if key:
            values[key] = str(row.get("値", "") or "")
            row_numbers[key] = index
    return values, row_numbers


def save_state(worksheet, row_numbers, key, value):
    if key in row_numbers:
        _write(f"{_key(worksheet)}.update_state", lambda: worksheet.update(values=[[key, value]], range_name=f"A{row_numbers[key]}:B{row_numbers[key]}"))
    else:
        _write(f"{_key(worksheet)}.append_state", lambda: worksheet.append_row([key, value], value_input_option="RAW"))
        row_numbers[key] = len(row_numbers) + 2


def save_states(worksheet, row_numbers, values):
    """複数の状態値を、既存行の一括更新＋新規行の一括追加で保存する。"""
    updates = []
    appends = []
    for key, value in values.items():
        if key in row_numbers:
            row_number = row_numbers[key]
            updates.append({"range": f"A{row_number}:B{row_number}", "values": [[key, value]]})
        else:
            appends.append([key, value])
    if updates:
        _write(f"{_key(worksheet)}.batch_update_state", lambda: worksheet.batch_update(updates))
    if appends:
        _write(f"{_key(worksheet)}.append_states", lambda: worksheet.append_rows(appends, value_input_option="RAW"))
        first_row = len(row_numbers) + 2
        for offset, (key, _) in enumerate(appends):
            row_numbers[key] = first_row + offset
