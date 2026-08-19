#!/usr/bin/env python3
"""
qa_sample_highlight.py

Sets up conditional formatting on the QA Sample tab that highlights
whichever ticket-link cell is CURRENTLY being scored by each evaluator.

An evaluator can replace the ticket link in their rubric tab's row 1
(column B) with one of the 4 shared backup links, if the originally
assigned ticket turns out unsuitable to score. So "the ticket John/Gabby
actually used" isn't always the John's/Gabby's Ticket Link column -- it
could be any of Backup 1-4 -- and this needs to update automatically
whenever an evaluator makes that swap, not just once.

Two hidden helper columns (John Rubric Row / Gabby Rubric Row, written
alongside the Score/Notes formulas by biweekly_sample.py and
backfill_rubrics.py) record the exact rubric-tab row of each pull's
Ticket Link cell. Conditional format rules use INDIRECT + those helper
columns to look up "what's the ticket link scored right now" for each
row, rather than recomputing rubric row numbers by counting -- which
would break for any pull where an evaluator's slot was empty (population
smaller than the number of sample slots -- see sampling.py; expected to
be rare, but this keeps the highlighting correct even then).

Highlight colors: John's matching cell -> yellow. Gabby's -> light
purple. If the same backup ticket were ever used by both evaluators (an
edge case that shouldn't normally happen), only one color wins for that
cell -- see JOHN_PRIORITY/GABBY_PRIORITY below.
"""
from sheets_writer import get_tab_id

# 0-indexed helper columns -- keep in sync with biweekly_sample.py /
# backfill_rubrics.py, which are what actually write these.
JOHN_RUBRIC_ROW_COL_A1 = "L"
GABBY_RUBRIC_ROW_COL_A1 = "M"
JOHN_RUBRIC_ROW_COL_IDX = 11
GABBY_RUBRIC_ROW_COL_IDX = 12

MAX_ROWS = 2000  # generous ceiling so rows added long after this runs are still covered

_YELLOW = {"red": 1, "green": 0.95, "blue": 0.4}
_LIGHT_PURPLE = {"red": 0.85, "green": 0.78, "blue": 0.95}


def _grid_range(sheet_id_num, start_col_0, end_col_0_excl):
    # Row 1 (index 0) is the header -- rules only ever need to cover data rows.
    return {
        "sheetId": sheet_id_num,
        "startRowIndex": 1,
        "endRowIndex": MAX_ROWS,
        "startColumnIndex": start_col_0,
        "endColumnIndex": end_col_0_excl,
    }


def _rule(index, ranges, formula, color):
    return {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": ranges,
                "booleanRule": {
                    "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": formula}]},
                    "format": {"backgroundColor": color},
                },
            },
            "index": index,
        }
    }


def setup_highlight_rules(service, sheet_id, qa_sample_tab, john_tab, gabby_tab):
    """Deletes any conditional format rules already on the tab and
    recreates these 4 -- simplest way to stay idempotent (calling this
    repeatedly doesn't pile up duplicate rules). Safe/cheap to call on
    every run."""
    sheet_id_num = get_tab_id(service, sheet_id, qa_sample_tab)

    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    existing_count = 0
    for s in meta.get("sheets", []):
        if s["properties"]["sheetId"] == sheet_id_num:
            existing_count = len(s.get("conditionalFormats", []))
            break
    # Delete highest index first -- deleting index 0 first would shift
    # every later rule's index down by one, deleting the wrong ones.
    delete_requests = [
        {"deleteConditionalFormatRule": {"sheetId": sheet_id_num, "index": i}}
        for i in reversed(range(existing_count))
    ]

    john_col = _grid_range(sheet_id_num, 1, 2)  # B
    gabby_col = _grid_range(sheet_id_num, 2, 3)  # C
    backups_cols = _grid_range(sheet_id_num, 3, 7)  # D:G

    def lookup(tab, helper_col):
        return f"INDIRECT(\"'{tab}'!B\"&${helper_col}2)"

    john_lookup = lookup(john_tab, JOHN_RUBRIC_ROW_COL_A1)
    gabby_lookup = lookup(gabby_tab, GABBY_RUBRIC_ROW_COL_A1)

    # JOHN_PRIORITY/GABBY_PRIORITY: index order below (0 = highest
    # priority) only matters for the one D:G cell both John's and Gabby's
    # rules could both match -- John's rule wins that tie-break.
    add_requests = [
        _rule(0, [john_col], f"=IFERROR(B2={john_lookup}, FALSE)", _YELLOW),
        _rule(1, [backups_cols], f"=IFERROR(D2={john_lookup}, FALSE)", _YELLOW),
        _rule(2, [gabby_col], f"=IFERROR(C2={gabby_lookup}, FALSE)", _LIGHT_PURPLE),
        _rule(3, [backups_cols], f"=IFERROR(D2={gabby_lookup}, FALSE)", _LIGHT_PURPLE),
    ]

    service.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id, body={"requests": delete_requests + add_requests}
    ).execute()
