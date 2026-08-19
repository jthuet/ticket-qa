#!/usr/bin/env python3
"""
backfill_rubrics.py

One-time historical build: reads every existing row already written to the
"<agent handle> QA Sample" tab and, for each one, appends a rubric-scoring
block for John's ticket and one for Gabby's onto their own
"<agent handle>-<evaluator>-rubrics" tab (see scripts/rubrics.py) -- and
writes a live formula into that QA Sample row's Score/Notes cells pointing
at the block it just built, exactly like scripts/biweekly_sample.py does
for new rows going forward. From then on, typing a score or note into a
rubric tab shows up on QA Sample immediately -- no sync script involved.

Also writes each row's rubric-tab row number into the hidden John/Gabby
Rubric Row helper columns (L/M) and (re)creates the highlight rules on
QA Sample (see scripts/qa_sample_highlight.py) that mark whichever
ticket-link cell is the one currently being scored.

Doesn't touch Zendesk at all -- it works entirely off what's already in
the QA Sample tab (Pull Date, John's Ticket Link, Gabby's Ticket Link
columns), so it reflects whatever's actually there, including the results
of the historical backfill_sample.py run.

Run manually once via .github/workflows/backfill_rubrics.yml
(workflow_dispatch). There's no cursor tracking what this has already
built (nothing to dedupe against, unlike the daily/biweekly sync scripts'
state files), so running it twice without clearing the rubric tabs first
would duplicate every block -- run it once after backfill_sample.py has
already populated the QA Sample tab, and let the ongoing
scripts/biweekly_sample.py handle new rows from then on.

Required environment variables:
  GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_SHEET_ID  -- same as the other scripts
Optional:
  TARGET_AGENT_EMAIL  -- same default/override as the other scripts
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from rubrics import append_rubric_block, next_block_start_row, rubric_formula_refs, rubric_tab_title  # noqa: E402
from sheets_writer import get_sheets_service, SheetsClient  # noqa: E402
from qa_sample_highlight import setup_highlight_rules  # noqa: E402

# 0-indexed columns: H=7 John Score, I=8 John Notes, J=9 Gabby Score,
# K=10 Gabby Notes. Scores stay unwrapped (short numbers); notes wrap.
QA_SAMPLE_COLUMN_WRAPS = [(7, 8, False), (8, 9, True), (9, 10, False), (10, 11, True)]

# `or` (not .get(..., default)) because GitHub Actions substitutes an unset
# secret as an empty string, not a missing variable -- .get()'s default
# would never kick in.
TARGET_AGENT_EMAIL = os.environ.get("TARGET_AGENT_EMAIL") or "jbell@nextpoint.com"
AGENT_HANDLE = TARGET_AGENT_EMAIL.split("@")[0]
QA_SAMPLE_TAB = f"{AGENT_HANDLE} QA Sample"


def main():
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    client = SheetsClient(get_sheets_service(), sheet_id)

    # A2:C skips the header row and only needs Pull Date/John's Ticket
    # Link/Gabby's Ticket Link -- the backup and score/notes columns don't
    # matter here. This is also the only read of QA_SAMPLE_TAB's row
    # count this script needs -- seed the client's cache from it instead
    # of letting a later row_count() call re-read the same tab.
    rows = client.get_values(QA_SAMPLE_TAB, "A2:C")
    client.seed_row_count(QA_SAMPLE_TAB, len(rows) + 1)  # +1 for the header row this range skipped
    if not rows:
        print(f"No data rows found in '{QA_SAMPLE_TAB}' -- nothing to build rubrics from.")
        return

    john_tab = rubric_tab_title(AGENT_HANDLE, "john")
    gabby_tab = rubric_tab_title(AGENT_HANDLE, "gabby")

    built = 0
    all_cell_updates = []
    for idx, row in enumerate(rows):
        qa_row = idx + 2  # +2: 1-indexed, plus the header row this range skipped
        pull_date = row[0] if len(row) > 0 else ""
        john_link = row[1] if len(row) > 1 else ""
        gabby_link = row[2] if len(row) > 2 else ""
        if not pull_date:
            continue

        if john_link:
            john_start = next_block_start_row(client, john_tab)
            score_formula, notes_formula = rubric_formula_refs(AGENT_HANDLE, "john", john_start)
            all_cell_updates += [
                (QA_SAMPLE_TAB, f"H{qa_row}", score_formula),
                (QA_SAMPLE_TAB, f"I{qa_row}", notes_formula),
                (QA_SAMPLE_TAB, f"L{qa_row}", john_start),
            ]
            append_rubric_block(client, AGENT_HANDLE, "john", pull_date, john_link, start_row=john_start)
        if gabby_link:
            gabby_start = next_block_start_row(client, gabby_tab)
            score_formula, notes_formula = rubric_formula_refs(AGENT_HANDLE, "gabby", gabby_start)
            all_cell_updates += [
                (QA_SAMPLE_TAB, f"J{qa_row}", score_formula),
                (QA_SAMPLE_TAB, f"K{qa_row}", notes_formula),
                (QA_SAMPLE_TAB, f"M{qa_row}", gabby_start),
            ]
            append_rubric_block(client, AGENT_HANDLE, "gabby", pull_date, gabby_link, start_row=gabby_start)

        built += 1

    # One batchUpdate for every row's formula/helper cells, not one per row.
    client.write_cells(all_cell_updates)

    client.set_columns_wrap(QA_SAMPLE_TAB, QA_SAMPLE_COLUMN_WRAPS)
    client.hide_columns(QA_SAMPLE_TAB, 11, 13)  # L:M, the rubric-row helper columns
    setup_highlight_rules(client, QA_SAMPLE_TAB, john_tab, gabby_tab)
    print(f"Built rubric blocks for {built} pull date(s) from '{QA_SAMPLE_TAB}'.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
