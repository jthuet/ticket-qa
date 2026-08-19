#!/usr/bin/env python3
"""
sheets_writer.py

Shared helper for appending rows to a named tab of a single target Google
Sheet (GOOGLE_SHEET_ID), creating the tab with a header row on first use if
it doesn't exist yet.

Unlike the NotebookLM project's Google Docs pool (MultiDocWriter, needed
because a single Doc caps out around ~1,024,000 characters), this targets
one plain spreadsheet -- Sheets' limit (10 million cells total) is far
beyond anything a weekly/biweekly ticket log will approach, so no
multi-file pool/rollover is needed here.

Required environment variable:
  GOOGLE_SERVICE_ACCOUNT_JSON   raw JSON contents of a Google service
    account key with the Sheets API enabled and this Sheet shared with it
    (Editor access)
"""
import json
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def get_sheets_service():
    creds_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    info = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def _tab_metadata(service, sheet_id):
    """{title: numeric sheetId} for every tab in the spreadsheet."""
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    return {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}


def _existing_tab_titles(service, sheet_id):
    return set(_tab_metadata(service, sheet_id))


def tab_exists(service, sheet_id, tab_title):
    return tab_title in _existing_tab_titles(service, sheet_id)


def get_tab_id(service, sheet_id, tab_title):
    """Numeric sheetId for tab_title -- needed for formatting requests
    (repeatCell, updateBorders), which address tabs by this ID, not by
    title. Raises KeyError if the tab doesn't exist."""
    return _tab_metadata(service, sheet_id)[tab_title]


def ensure_tab_exists(service, sheet_id, tab_title):
    """Create tab_title (with no header row) if it doesn't already exist.
    For tabs like the rubric tabs that don't have one fixed header row --
    each rubric block carries its own headers further down the tab."""
    if tab_title in _existing_tab_titles(service, sheet_id):
        return
    service.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": tab_title}}}]},
    ).execute()


def ensure_tab(service, sheet_id, tab_title, header):
    """Create tab_title with the given header row if it doesn't already
    exist. Safe to call on every run -- a no-op once the tab is there, so
    callers don't need their own first-run bookkeeping for this."""
    if tab_title in _existing_tab_titles(service, sheet_id):
        return
    ensure_tab_exists(service, sheet_id, tab_title)
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{tab_title}'!A1",
        valueInputOption="RAW",
        body={"values": [header]},
    ).execute()


def append_rows(service, sheet_id, tab_title, rows):
    """Append rows (a list of lists) after the tab's current last row.
    No-op if rows is empty.

    Caution: this uses the Sheets API's own "find the table and append
    after it" heuristic (values.append), which can misplace data if the
    tab's last row is intentionally blank (a spacer) -- the heuristic can
    treat that blank row as the end of the table and write new data
    INTO it rather than after it. Use write_rows_at() instead whenever
    the exact target row matters, e.g. content with deliberate blank
    rows built in (see scripts/rubrics.py)."""
    if not rows:
        return
    service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"'{tab_title}'!A:A",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()


def write_rows_at(service, sheet_id, tab_title, start_row, rows):
    """Write rows (a list of lists) starting at the given 1-indexed row,
    via an explicit range update. Unlike append_rows(), this never has to
    guess where "the table" ends, so it can't misplace data into a
    deliberately blank row. No-op if rows is empty."""
    if not rows:
        return
    end_row = start_row + len(rows) - 1
    end_col = chr(ord("A") + max(len(r) for r in rows) - 1) if rows else "A"
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{tab_title}'!A{start_row}:{end_col}{end_row}",
        valueInputOption="USER_ENTERED",
        body={"values": rows},
    ).execute()


def row_count(service, sheet_id, tab_title):
    """Number of rows currently holding data anywhere in columns A-C of
    tab_title -- used to compute where the next write should start.
    Deliberately checks the full A:C span rather than just column A: some
    rows (e.g. a rubric block's Total/Notes rows) only have data in
    columns B/C, and column-A-only counting would undercount them."""
    resp = service.spreadsheets().values().get(spreadsheetId=sheet_id, range=f"'{tab_title}'!A:C").execute()
    return len(resp.get("values", []))


def set_columns_no_wrap(service, sheet_id, tab_title, start_col_idx, end_col_idx_excl):
    """Sets wrapStrategy=OVERFLOW_CELL (no wrapping) for the given
    0-indexed column range, spanning every row (row bounds are omitted,
    so this covers rows written in the future too). Safe to call on every
    run -- idempotent."""
    sheet_id_num = get_tab_id(service, sheet_id, tab_title)
    service.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={
            "requests": [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id_num,
                            "startColumnIndex": start_col_idx,
                            "endColumnIndex": end_col_idx_excl,
                        },
                        "cell": {"userEnteredFormat": {"wrapStrategy": "OVERFLOW_CELL"}},
                        "fields": "userEnteredFormat.wrapStrategy",
                    }
                }
            ]
        },
    ).execute()
