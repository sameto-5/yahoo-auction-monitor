import json

import gspread
from google.oauth2.service_account import Credentials


PRIORITY_REQUIRED_HEADERS = ["有効", "優先度", "ブランド", "型番", "キーワード", "除外キーワード"]


def open_book(credentials_json, spreadsheet_id):
    if not credentials_json or not spreadsheet_id:
        raise RuntimeError("GOOGLE_CREDENTIALS または SPREADSHEET_ID が未設定です")
    info = json.loads(credentials_json)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    credentials = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(credentials).open_by_key(spreadsheet_id)


def get_priority_rows_read_only(book):
    worksheet = book.worksheet("priority_items")
    headers = worksheet.row_values(1)
    missing = [name for name in PRIORITY_REQUIRED_HEADERS if name not in headers]
    if missing:
        raise RuntimeError(f"priority_items 必須列不足: {', '.join(missing)}")
    return worksheet.get_all_records()


def get_or_create_sheet(book, name, headers, rows=1000):
    if not name.startswith("yahoo_"):
        raise ValueError("ヤフオク側が作成できるのは yahoo_ シートだけです")
    try:
        worksheet = book.worksheet(name)
    except gspread.WorksheetNotFound:
        worksheet = book.add_worksheet(title=name, rows=rows, cols=len(headers))
    ensure_headers_append_only(worksheet, headers)
    return worksheet


def ensure_headers_append_only(worksheet, required_headers):
    current = worksheet.row_values(1)
    if not current:
        worksheet.update(values=[required_headers], range_name="A1")
        return list(required_headers)
    missing = [header for header in required_headers if header not in current]
    if not missing:
        return current
    final = current + missing
    if worksheet.col_count < len(final):
        worksheet.resize(cols=len(final))
    worksheet.update(values=[final], range_name=f"A1:{column_letter(len(final))}1")
    return final


def column_letter(number):
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def append_record(worksheet, record):
    headers = worksheet.row_values(1)
    worksheet.append_row([record.get(header, "") for header in headers], value_input_option="RAW")


def update_record(worksheet, row_number, record):
    headers = worksheet.row_values(1)
    values = [record.get(header, "") for header in headers]
    worksheet.update(
        values=[values],
        range_name=f"A{row_number}:{column_letter(len(headers))}{row_number}",
    )


def records_by(worksheet, key):
    rows = worksheet.get_all_records()
    return rows, {
        str(row.get(key)): (index, row)
        for index, row in enumerate(rows, start=2)
        if row.get(key) not in (None, "")
    }


def load_state(worksheet):
    rows = worksheet.get_all_records()
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
        worksheet.update(values=[[key, value]], range_name=f"A{row_numbers[key]}:B{row_numbers[key]}")
    else:
        worksheet.append_row([key, value], value_input_option="RAW")
        row_numbers[key] = len(row_numbers) + 2
