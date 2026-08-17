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


def _existing_tab_titles(service, sheet_id):
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    return {s["properties"]["title"] for s in meta.get("sheets", [])}


def ensure_tab(service, sheet_id, tab_title, header):
    """Create tab_title with the given header row if it doesn't already
    exist. Safe to call on every run -- a no-op once the tab is there, so
    callers don't need their own first-run bookkeeping for this."""
    if tab_title in _existing_tab_titles(service, sheet_id):
        return
    service.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": tab_title}}}]},
    ).execute()
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{tab_title}'!A1",
        valueInputOption="RAW",
        body={"values": [header]},
    ).execute()


def append_rows(service, sheet_id, tab_title, rows):
    """Append rows (a list of lists) after the tab's current last row.
    No-op if rows is empty."""
    if not rows:
        return
    service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"'{tab_title}'!A:A",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()
