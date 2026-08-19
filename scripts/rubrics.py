#!/usr/bin/env python3
"""
rubrics.py

Shared helper for building and appending one QA rubric-scoring block per
pull date onto an evaluator's own "<agent handle>-<evaluator>-rubrics" tab
-- one block scoring John's assigned ticket (on the john tab), a separate
block scoring Gabby's (on the gabby tab), each against the same 4-metric
rubric.

Each block is 8 written rows, plus one blank row left as a gap before the
next block (never written to -- see append_rubric_block):
  0 Pull Date | Ticket Link
  1 Metric | Description | Score (1 Major Miss - 4 Excellent)
  2-5 <4 metric rows, Score column (C) left blank for the evaluator>
  6         | Total       | =SUM(...)   (live formula over rows 2-5's C cells)
  7         | <Evaluator> Notes | <blank, for the evaluator to fill in>
  (row 8, unwritten: blank gap before the next block)

"Total" and "<Evaluator> Notes" are labeled in column B rather than A, so
it reads clearly that both results (the sum, and the notes text) land in
column C.

Blocks are appended in order, oldest pull date first -- both the one-time
historical build (scripts/backfill_rubrics.py, working off whatever's
already in the QA Sample tab) and the recurring per-run append
(scripts/biweekly_sample.py, using the row it just wrote) call
append_rubric_block() for this. scripts/rubric_sync.py reads blocks back
out (via TOTAL_ROW_OFFSET/NOTES_ROW_OFFSET/BLOCK_STRIDE below) to copy
completed scores/notes into the QA Sample tab.
"""
from sheets_writer import ensure_tab_exists, get_tab_id, row_count, write_rows_at

RUBRIC_METRICS = [
    (
        "Accuracy of information",
        "Did the response correctly describe how the feature/workflow actually works in the software? "
        "Were any steps, field names, or settings misstated? If a claim was uncertain, did the rep verify "
        "it (checking documentation, testing in the platform, asking a colleague) rather than guessing?",
    ),
    (
        "Completeness of the answer",
        "Did the response address everything the client actually asked, including any sub-questions "
        "buried in the ticket? Did it anticipate an obvious follow-up (e.g., \"and here's how to also do "
        "X, which you'll likely need next\")? Response contains proper greeting and signatures. "
        "Appropriate screenshots and links to articles are included when helpful.",
    ),
    (
        "Evidence of investigation",
        "Is there a visible sign the response invloved investigation — checked the account/case settings, "
        "reproduced the issue, looked at logs — rather than being a generic or templated answer that "
        "happened to be in the ballpark?",
    ),
    (
        "Appropriate Escalation",
        "No unverified claims included in the response — uncertain answers were escalated internally "
        "rather than guessed at in the response.",
    ),
]

RUBRIC_HEADER = ["Metric", "Description", "Score (1 Major Miss - 4 Excellent)"]

# 0-indexed offsets, within one block, of each row -- shared with
# rubric_sync.py so both sides agree on the layout without duplicating it.
PULL_DATE_ROW_OFFSET = 0
HEADER_ROW_OFFSET = 1
FIRST_METRIC_ROW_OFFSET = 2
LAST_METRIC_ROW_OFFSET = FIRST_METRIC_ROW_OFFSET + len(RUBRIC_METRICS) - 1
TOTAL_ROW_OFFSET = LAST_METRIC_ROW_OFFSET + 1
NOTES_ROW_OFFSET = TOTAL_ROW_OFFSET + 1
BLOCK_LENGTH = NOTES_ROW_OFFSET + 1  # rows actually written (8)
BLOCK_STRIDE = BLOCK_LENGTH + 1  # + 1 blank gap row before the next block (9)

_GREY = {"red": 0.93, "green": 0.93, "blue": 0.93}
_BORDER_STYLE = {"style": "SOLID", "width": 1, "color": {"red": 0, "green": 0, "blue": 0}}


def rubric_tab_title(agent_handle, evaluator_name):
    return f"{agent_handle}-{evaluator_name}-rubrics"


def build_rubric_block(start_row, pull_date, ticket_link, evaluator_name):
    """Returns the list-of-lists for one BLOCK_LENGTH-row block, given the
    1-indexed row it will start on -- needed so the Total row's SUM
    formula points at the right cells."""
    metric_rows = [[name, desc, ""] for name, desc in RUBRIC_METRICS]
    first_score_row = start_row + FIRST_METRIC_ROW_OFFSET
    last_score_row = start_row + LAST_METRIC_ROW_OFFSET
    return [
        [pull_date, ticket_link],
        RUBRIC_HEADER,
        *metric_rows,
        ["", "Total", f"=SUM(C{first_score_row}:C{last_score_row})"],
        ["", f"{evaluator_name.capitalize()} Notes", ""],
    ]


def format_rubric_block(service, sheet_id, tab_title, start_row):
    """Applies, in one batchUpdate: text wrap across the whole block (so
    resizing columns once keeps every block readable) except the Notes
    value cell, which stays unwrapped; a light grey background on the
    header row; and an outline border from the header row through the
    Total row (deliberately excluding the Pull Date row and the Notes
    row)."""
    sheet_id_num = get_tab_id(service, sheet_id, tab_title)
    top = start_row - 1  # 0-indexed
    header_row_0 = top + HEADER_ROW_OFFSET
    total_row_0 = top + TOTAL_ROW_OFFSET
    notes_row_0 = top + NOTES_ROW_OFFSET

    def repeat_cell(start_row_0, end_row_0_excl, start_col_0, end_col_0_excl, cell_fmt, fields):
        return {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id_num,
                    "startRowIndex": start_row_0,
                    "endRowIndex": end_row_0_excl,
                    "startColumnIndex": start_col_0,
                    "endColumnIndex": end_col_0_excl,
                },
                "cell": {"userEnteredFormat": cell_fmt},
                "fields": fields,
            }
        }

    requests = [
        # Wrap the whole block (Pull Date row through Notes row), 3 columns wide.
        repeat_cell(
            top, top + BLOCK_LENGTH, 0, 3, {"wrapStrategy": "WRAP"}, "userEnteredFormat.wrapStrategy"
        ),
        # ...except the Notes value cell (column C), which stays unwrapped.
        repeat_cell(
            notes_row_0, notes_row_0 + 1, 2, 3, {"wrapStrategy": "OVERFLOW_CELL"}, "userEnteredFormat.wrapStrategy"
        ),
        # Light grey background on the header row only.
        repeat_cell(
            header_row_0, header_row_0 + 1, 0, 3, {"backgroundColor": _GREY}, "userEnteredFormat.backgroundColor"
        ),
        # Outline border from the header row through the Total row.
        {
            "updateBorders": {
                "range": {
                    "sheetId": sheet_id_num,
                    "startRowIndex": header_row_0,
                    "endRowIndex": total_row_0 + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 3,
                },
                "top": _BORDER_STYLE,
                "bottom": _BORDER_STYLE,
                "left": _BORDER_STYLE,
                "right": _BORDER_STYLE,
            }
        },
    ]
    service.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": requests}).execute()


def append_rubric_block(service, sheet_id, agent_handle, evaluator_name, pull_date, ticket_link):
    """No-op if ticket_link is falsy -- that evaluator's slot was empty for
    this pull (population smaller than the number of sample slots), so
    there's no ticket to build a rubric block for.

    Writes to an explicit row range (not append_rows()'s auto-detected
    "find the table" append) and always leaves one blank row after the
    previous block before starting the new one -- row_count() only
    reflects rows that actually have data, so a fresh block always starts
    2 rows after the previous one's last written row, never 1 (which
    would collapse the gap), except for the very first block in an empty
    tab, which starts at row 1."""
    if not ticket_link:
        return
    tab_title = rubric_tab_title(agent_handle, evaluator_name)
    ensure_tab_exists(service, sheet_id, tab_title)
    last_row = row_count(service, sheet_id, tab_title)
    start_row = 1 if last_row == 0 else last_row + 2
    block = build_rubric_block(start_row, pull_date, ticket_link, evaluator_name)
    write_rows_at(service, sheet_id, tab_title, start_row, block)
    format_rubric_block(service, sheet_id, tab_title, start_row)
