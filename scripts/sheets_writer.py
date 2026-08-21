#!/usr/bin/env python3
"""
sheets_writer.py

SheetsClient: a thin wrapper around the Sheets API for one target Google
Sheet (GOOGLE_SHEET_ID), used by every script in this repo instead of
passing a raw (service, sheet_id) pair around everywhere. Two things it
buys over that:

1. Caching. Sheets' default quota is a tight 60 read requests/minute/user
   -- a naive implementation that re-fetches a tab's metadata or row
   count every time it's needed (once per rubric block, twice a run per
   evaluator, times however many historical rows a backfill covers) blows
   past that quickly and the run dies with a 429. SheetsClient instead
   fetches spreadsheet metadata (tab titles/IDs/conditional formats) and
   each tab's row count AT MOST ONCE per script run, and updates its own
   cache locally afterward -- e.g. creating a tab or writing rows updates
   the cache from the API response it already got back, no extra read.
2. Retry-with-backoff on every call, for two different failure classes:
   rate-limit HTTP responses (429 / RATE_LIMIT_EXCEEDED / RESOURCE_
   EXHAUSTED / 5xx), same idea as the NotebookLM project's Slack
   rate-limit retry in sync_to_notebooklm.py; and transient network/TLS
   failures below the HTTP layer entirely (a dropped connection, "EOF
   occurred in violation of protocol") that a GitHub Actions runner hits
   occasionally and has no HTTP status code to inspect at all. Caching
   makes hitting the rate limit unlikely, but neither failure class is
   preventable outright, so this is a safety net either way.

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
import http.client
import json
import os
import socket
import ssl
import sys
import time

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

MAX_RATE_LIMIT_RETRIES = 6
RATE_LIMIT_BASE_WAIT = 15  # seconds; Sheets' per-minute quotas are a rolling
                           # window, so a short exponential backoff isn't
                           # enough -- this needs to be patient, not quick.

MAX_NETWORK_RETRIES = 4
NETWORK_BASE_WAIT = 5  # seconds; a dropped TLS connection is usually resolved
                       # by a quick retry, unlike a per-minute quota window.

# Below-the-HTTP-layer failures -- a dropped/reset connection, a TLS
# handshake or read that never completed ("EOF occurred in violation of
# protocol"), a DNS hiccup. These arrive as plain socket/ssl exceptions,
# never as an HttpError, since no HTTP response was ever received to wrap.
_TRANSIENT_NETWORK_ERRORS = (ssl.SSLError, ConnectionError, TimeoutError, http.client.HTTPException, socket.error)


def get_sheets_service():
    creds_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    info = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def _is_retriable_http_error(e):
    return isinstance(e, HttpError) and (
        e.resp.status in (429, 500, 502, 503, 504)
        or "RATE_LIMIT_EXCEEDED" in str(e)
        or "RESOURCE_EXHAUSTED" in str(e)
    )


def _execute_with_retry(request):
    """Two independent retry budgets, not one shared counter -- rate-limit
    and network failures have different limits (MAX_RATE_LIMIT_RETRIES vs
    MAX_NETWORK_RETRIES) and different backoff paces, and giving them
    separate counters means whichever budget is actually exhausted is the
    one that decides when to finally let the exception propagate, rather
    than a single shared attempt count potentially exhausting the wrong
    budget's check first and silently falling out of the loop."""
    rate_limit_attempts = 0
    network_attempts = 0
    while True:
        try:
            return request.execute()
        except HttpError as e:
            if not _is_retriable_http_error(e) or rate_limit_attempts >= MAX_RATE_LIMIT_RETRIES:
                raise
            rate_limit_attempts += 1
            wait = RATE_LIMIT_BASE_WAIT * rate_limit_attempts
            print(
                f"Sheets API rate-limited (attempt {rate_limit_attempts}/{MAX_RATE_LIMIT_RETRIES}), "
                f"retrying in {wait}s...",
                file=sys.stderr,
            )
            time.sleep(wait)
        except _TRANSIENT_NETWORK_ERRORS as e:
            if network_attempts >= MAX_NETWORK_RETRIES:
                raise
            network_attempts += 1
            wait = NETWORK_BASE_WAIT * network_attempts
            print(
                f"Transient network error ({type(e).__name__}: {e}) talking to Sheets "
                f"(attempt {network_attempts}/{MAX_NETWORK_RETRIES}), retrying in {wait}s...",
                file=sys.stderr,
            )
            time.sleep(wait)


class SheetsClient:
    def __init__(self, service, sheet_id):
        self.service = service
        self.sheet_id = sheet_id
        self._sheets_meta = None  # list of "sheets" entries from spreadsheets().get(), lazily loaded
        self._row_counts = {}  # {tab_title: last known row count}

    # -- metadata (tab IDs, conditional formats) --------------------------

    def _load_meta(self):
        if self._sheets_meta is None:
            resp = _execute_with_retry(self.service.spreadsheets().get(spreadsheetId=self.sheet_id))
            self._sheets_meta = resp.get("sheets", [])
        return self._sheets_meta

    def _find(self, tab_title):
        for s in self._load_meta():
            if s["properties"]["title"] == tab_title:
                return s
        return None

    def tab_exists(self, tab_title):
        return self._find(tab_title) is not None

    def get_tab_id(self, tab_title):
        """Numeric sheetId for tab_title -- needed for formatting requests
        (repeatCell, updateBorders), which address tabs by this ID, not by
        title. Raises KeyError if the tab doesn't exist."""
        s = self._find(tab_title)
        if s is None:
            raise KeyError(tab_title)
        return s["properties"]["sheetId"]

    def conditional_format_count(self, tab_title):
        s = self._find(tab_title)
        return len(s.get("conditionalFormats", [])) if s else 0

    def ensure_tab_exists(self, tab_title):
        """Create tab_title (with no header row) if it doesn't already
        exist. For tabs like the rubric tabs that don't have one fixed
        header row -- each rubric block carries its own headers further
        down the tab."""
        if self.tab_exists(tab_title):
            return
        resp = _execute_with_retry(
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.sheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": tab_title}}}]},
            )
        )
        new_props = resp["replies"][0]["addSheet"]["properties"]
        self._sheets_meta.append({"properties": new_props})

    def ensure_tab(self, tab_title, header):
        """Create tab_title with the given header row if it doesn't
        already exist. Safe to call on every run -- a no-op once the tab
        is there, so callers don't need their own first-run bookkeeping
        for this."""
        if self.tab_exists(tab_title):
            return
        self.ensure_tab_exists(tab_title)
        _execute_with_retry(
            self.service.spreadsheets()
            .values()
            .update(
                spreadsheetId=self.sheet_id,
                range=f"'{tab_title}'!A1",
                valueInputOption="RAW",
                body={"values": [header]},
            )
        )
        self._row_counts[tab_title] = 1

    # -- rows/cells --------------------------------------------------------

    def append_rows(self, tab_title, rows):
        """Append rows (a list of lists) after the tab's current last row.
        No-op if rows is empty.

        Caution: this uses the Sheets API's own "find the table and
        append after it" heuristic (values.append), which can misplace
        data if the tab's last row is intentionally blank (a spacer) --
        the heuristic can treat that blank row as the end of the table
        and write new data INTO it rather than after it. Use
        write_rows_at() instead whenever the exact target row matters,
        e.g. content with deliberate blank rows built in (see
        scripts/rubrics.py). Invalidates any cached row count for this
        tab (this call's own row target isn't knowable without another
        read, so the safest thing is to just forget the cached count)."""
        if not rows:
            return
        _execute_with_retry(
            self.service.spreadsheets()
            .values()
            .append(
                spreadsheetId=self.sheet_id,
                range=f"'{tab_title}'!A:A",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": rows},
            )
        )
        self._row_counts.pop(tab_title, None)

    def write_rows_at(self, tab_title, start_row, rows):
        """Write rows (a list of lists) starting at the given 1-indexed
        row, via an explicit range update. Unlike append_rows(), this
        never has to guess where "the table" ends, so it can't misplace
        data into a deliberately blank row. No-op if rows is empty."""
        if not rows:
            return
        end_row = start_row + len(rows) - 1
        end_col = chr(ord("A") + max(len(r) for r in rows) - 1) if rows else "A"
        _execute_with_retry(
            self.service.spreadsheets()
            .values()
            .update(
                spreadsheetId=self.sheet_id,
                range=f"'{tab_title}'!A{start_row}:{end_col}{end_row}",
                valueInputOption="USER_ENTERED",
                body={"values": rows},
            )
        )
        self._row_counts[tab_title] = max(self._row_counts.get(tab_title, 0), end_row)

    def write_cells(self, updates):
        """updates: an iterable of (tab_title, cell_a1, value) triples,
        written in one values().batchUpdate call -- e.g. for scattering a
        few formula strings across a tab that aren't contiguous
        rows/columns. No-op if updates is empty."""
        updates = list(updates)
        if not updates:
            return
        data = [{"range": f"'{tab_title}'!{cell_a1}", "values": [[value]]} for tab_title, cell_a1, value in updates]
        _execute_with_retry(
            self.service.spreadsheets()
            .values()
            .batchUpdate(spreadsheetId=self.sheet_id, body={"valueInputOption": "USER_ENTERED", "data": data})
        )

    def row_count(self, tab_title):
        """Number of rows currently holding data anywhere in columns A-C
        of tab_title -- used to compute where the next write should
        start. Deliberately checks the full A:C span rather than just
        column A: some rows (e.g. a rubric block's Total/Notes rows) only
        have data in columns B/C, and column-A-only counting would
        undercount them. Cached after the first call for this tab --
        every write method above keeps the cache current from then on,
        so this only ever costs a real API call once per tab per run."""
        if tab_title not in self._row_counts:
            resp = _execute_with_retry(
                self.service.spreadsheets().values().get(spreadsheetId=self.sheet_id, range=f"'{tab_title}'!A:C")
            )
            self._row_counts[tab_title] = len(resp.get("values", []))
        return self._row_counts[tab_title]

    def get_values(self, tab_title, a1_range):
        """One-off read of tab_title!a1_range -- e.g. an initial fetch of
        historical rows a backfill script needs the actual values of, not
        just a count. Not cached (unlike row_count()): callers that need
        this are expected to call it once per tab, not once per row."""
        resp = _execute_with_retry(
            self.service.spreadsheets().values().get(spreadsheetId=self.sheet_id, range=f"'{tab_title}'!{a1_range}")
        )
        return resp.get("values", [])

    def seed_row_count(self, tab_title, count):
        """Lets a caller that already fetched a tab's data some other way
        (e.g. get_values()) prime the row_count() cache from it, instead
        of row_count() re-reading the same tab a second time."""
        self._row_counts[tab_title] = count

    # -- formatting ---------------------------------------------------------

    def set_columns_wrap(self, tab_title, column_wraps):
        """Sets wrapStrategy for one or more 0-indexed column ranges in
        one batchUpdate, spanning every row (row bounds are omitted, so
        this covers rows written in the future too). column_wraps is an
        iterable of (start_col_idx, end_col_idx_excl, wrap) tuples, wrap a
        bool (True -> WRAP, False -> OVERFLOW_CELL/no wrap). Safe to call
        on every run -- idempotent."""
        sheet_id_num = self.get_tab_id(tab_title)
        requests = [
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id_num,
                        "startColumnIndex": start_col_idx,
                        "endColumnIndex": end_col_idx_excl,
                    },
                    "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP" if wrap else "OVERFLOW_CELL"}},
                    "fields": "userEnteredFormat.wrapStrategy",
                }
            }
            for start_col_idx, end_col_idx_excl, wrap in column_wraps
        ]
        _execute_with_retry(
            self.service.spreadsheets().batchUpdate(spreadsheetId=self.sheet_id, body={"requests": requests})
        )

    def hide_columns(self, tab_title, start_col_idx, end_col_idx_excl):
        """Hides the given 0-indexed column range (e.g. helper columns
        not meant for a human to look at). Safe to call on every run --
        idempotent."""
        sheet_id_num = self.get_tab_id(tab_title)
        _execute_with_retry(
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.sheet_id,
                body={
                    "requests": [
                        {
                            "updateDimensionProperties": {
                                "range": {
                                    "sheetId": sheet_id_num,
                                    "dimension": "COLUMNS",
                                    "startIndex": start_col_idx,
                                    "endIndex": end_col_idx_excl,
                                },
                                "properties": {"hiddenByUser": True},
                                "fields": "hiddenByUser",
                            }
                        }
                    ]
                },
            )
        )

    def batch_update(self, requests):
        """Escape hatch for callers (rubrics.py, qa_sample_highlight.py)
        that need to build their own request list -- still goes through
        the shared retry wrapper."""
        if not requests:
            return
        return _execute_with_retry(
            self.service.spreadsheets().batchUpdate(spreadsheetId=self.sheet_id, body={"requests": requests})
        )
