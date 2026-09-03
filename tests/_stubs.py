import sys
import types


if "curl_cffi" not in sys.modules:
    curl_cffi = types.ModuleType("curl_cffi")
    curl_cffi.requests = types.SimpleNamespace()
    sys.modules["curl_cffi"] = curl_cffi

if "gspread" not in sys.modules:
    gspread = types.ModuleType("gspread")
    gspread.WorksheetNotFound = type("WorksheetNotFound", (Exception,), {})
    sys.modules["gspread"] = gspread

if "google.oauth2.service_account" not in sys.modules:
    google = types.ModuleType("google")
    oauth2 = types.ModuleType("google.oauth2")
    service_account = types.ModuleType("google.oauth2.service_account")
    service_account.Credentials = object
    sys.modules["google"] = google
    sys.modules["google.oauth2"] = oauth2
    sys.modules["google.oauth2.service_account"] = service_account
