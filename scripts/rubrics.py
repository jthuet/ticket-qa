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
  7 <Evaluator> Notes | <B+C merged into one free-typing cell>
  (row 8, unwritten: blank gap before the next block)

"Total" is labeled in column B rather than A, so it reads clearly that its
result lands in column C. The Notes row instead keeps its label in column
A, with columns B and C merged into a single cell -- so notes can be typed
anywhere across that merged width and it's all the same cell underneath
(Sheets always stores a merged cell's value in its top-left cell, column
B here).

Blocks are appended in order, oldest pull date first -- both the one-time
historical build (scripts/backfill_rubrics.py) and the recurring per-run
append (scripts/biweekly_sample.py) call append_rubric_block() for this.
Both callers also use next_block_start_row()/rubric_formula_refs() below
*before* calling append_rubric_block(), to write a live formula into the
QA Sample tab's Score/Notes cell (e.g. `='jbell-john-rubrics'!C7`) that
points at exactly where the block they're about to write will land --
Sheets keeps that reference live from then on, so typing a score/note
into the rubric tab shows up on QA Sample immediately, no sync script or
scheduled run required.

Every function here takes a sheets_writer.SheetsClient (`client`) instead
of a raw (service, sheet_id) pair, so tab metadata/row counts get cached
across a whole script run rather than re-fetched from the API on every
call -- see that module's docstring for why that matters."""

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

# 0-indexed offsets, within one block, of each row.
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
        [f"{evaluator_name.capitalize()} Notes", "", ""],
    ]


def format_rubric_block(client, tab_title, start_row):
    """Applies: merges the Notes row's B/C cells into one (so notes can be
    typed anywhere across that width and still land in the same
    underlying cell); a light grey background on the header row; an
    outline border from the header row through the Total row
    (deliberately excluding the Pull Date row and the Notes row); and
    text wrap across the whole block (so resizing a column once keeps
    every block readable).

    This is TWO separate batchUpdate calls, not one, and that's
    deliberate: the merge has to fully commit server-side before wrap is
    applied, or wrap set on the not-yet-merged B/C cells doesn't reliably
    stick to the resulting merged cell -- putting both in one batchUpdate
    (even with the merge request listed first) wasn't enough to guarantee
    that."""
    sheet_id_num = client.get_tab_id(tab_title)
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

    structural_requests = [
        # Merge the Notes row's B/C cells into one -- value lives in B afterward.
        {
            "mergeCells": {
                "range": {
                    "sheetId": sheet_id_num,
                    "startRowIndex": notes_row_0,
                    "endRowIndex": notes_row_0 + 1,
                    "startColumnIndex": 1,
                    "endColumnIndex": 3,
                },
                "mergeType": "MERGE_ALL",
            }
        },
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
    client.batch_update(structural_requests)

    # Wrap the whole block (Pull Date row through Notes row), 3 columns wide --
    # a separate call, run only after the merge above has fully committed.
    wrap_request = repeat_cell(top, top + BLOCK_LENGTH, 0, 3, {"wrapStrategy": "WRAP"}, "userEnteredFormat.wrapStrategy")
    client.batch_update([wrap_request])


def next_block_start_row(client, tab_title):
    """The 1-indexed row the NEXT rubric block on tab_title will start on,
    without writing anything -- lets a caller build a formula pointing at
    that block's cells (e.g. for the QA Sample tab) before the block
    itself is written. Creates tab_title (empty) if it doesn't exist yet,
    so the row count comes back as 0 -> start row 1, consistent with what
    append_rubric_block would do.

    Always leaves one blank row after the previous block before starting
    the new one -- row_count() only reflects rows that actually have
    data, so a fresh block starts 2 rows after the previous one's last
    written row, never 1 (which would collapse the gap), except for the
    very first block in an empty tab, which starts at row 1."""
    client.ensure_tab_exists(tab_title)
    last_row = client.row_count(tab_title)
    return 1 if last_row == 0 else last_row + 2


def rubric_formula_refs(agent_handle, evaluator_name, start_row):
    """Returns (score_formula, notes_formula): live cross-sheet formula
    strings pointing at the Total/Notes cells of the block that will
    start at start_row (see next_block_start_row) on that evaluator's
    rubric tab -- for writing into the QA Sample tab's Score/Notes
    columns so they track the rubric tab automatically from then on."""
    tab_title = rubric_tab_title(agent_handle, evaluator_name)
    total_row = start_row + TOTAL_ROW_OFFSET
    notes_row = start_row + NOTES_ROW_OFFSET
    return f"='{tab_title}'!C{total_row}", f"='{tab_title}'!B{notes_row}"


def append_rubric_block(client, agent_handle, evaluator_name, pull_date, ticket_link, start_row=None):
    """No-op if ticket_link is falsy -- that evaluator's slot was empty for
    this pull (population smaller than the number of sample slots), so
    there's no ticket to build a rubric block for.

    Pass start_row if the caller already computed it via
    next_block_start_row() (e.g. to build a QA Sample formula pointing at
    it beforehand) to avoid recomputing it here; otherwise it's computed
    fresh. Writes to an explicit row range (not append_rows()'s
    auto-detected "find the table" append), so placement is never
    guessed. Returns the start_row used, or None if it was a no-op."""
    if not ticket_link:
        return None
    tab_title = rubric_tab_title(agent_handle, evaluator_name)
    if start_row is None:
        start_row = next_block_start_row(client, tab_title)
    else:
        client.ensure_tab_exists(tab_title)
    block = build_rubric_block(start_row, pull_date, ticket_link, evaluator_name)
    client.write_rows_at(tab_title, start_row, block)
    format_rubric_block(client, tab_title, start_row)
    return start_row
