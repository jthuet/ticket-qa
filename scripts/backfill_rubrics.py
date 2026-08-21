#!/usr/bin/env python3
"""
backfill_rubrics.py

For every agent in TARGET_AGENT_EMAILS, reads every existing row already
written to that agent's "<handle> QA Sample" tab and, for each one,
appends a rubric-scoring block for John's ticket and one for Gabby's onto
their own "<handle>-<evaluator>-rubrics" tab (see scripts/rubrics.py) --
and writes a live formula into that QA Sample row's Score/Notes cells
pointing at the block it just built, exactly like scripts/biweekly_sample.py
does for new rows going forward. From then on, typing a score or note into
a rubric tab shows up on QA Sample immediately -- no sync script involved.

Also writes each row's rubric-tab row number into the hidden John/Gabby
Rubric Row helper columns (L/M) and (re)creates the highlight rules on
each agent's QA Sample tab (see scripts/qa_sample_highlight.py) that mark
whichever ticket-link cell is the one currently being scored.

Doesn't touch Zendesk at all -- it works entirely off what's already in
each agent's QA Sample tab (Pull Date, John's Ticket Link, Gabby's Ticket
Link columns), so it reflects whatever's actually there, including the
results of the historical backfill_sample.py run.

Run manually once via .github/workflows/backfill_rubrics.yml
(workflow_dispatch). There's no cursor tracking what this has already
built (nothing to dedupe against, unlike the daily/biweekly sync scripts'
state files), so running it twice for an agent without clearing that
agent's rubric tabs first would duplicate every block -- run it once per
agent, right after backfill_sample.py has populated that agent's QA
Sample tab. See the README's "Adding another agent" for backfilling just
a newly added one without re-touching agents already done (temporarily
narrow TARGET_AGENT_EMAIL to just the new agent for this run).

Required environment variables:
  GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_SHEET_ID  -- same as the other scripts
Optional:
  TARGET_AGENT_EMAIL  -- same default/override as the other scripts
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from biweekly_sample import TARGET_AGENT_EMAILS, agent_handle, qa_sample_tab_title  # noqa: E402
from rubrics import append_rubric_block, next_block_start_row, rubric_formula_refs, rubric_tab_title  # noqa: E402
from sheets_writer import get_sheets_service, SheetsClient  # noqa: E402
from qa_sample_highlight import setup_highlight_rules  # noqa: E402

# 0-indexed columns: H=7 John Score, I=8 John Notes, J=9 Gabby Score,
# K=10 Gabby Notes. Scores stay unwrapped (short numbers); notes wrap.
QA_SAMPLE_COLUMN_WRAPS = [(7, 8, False), (8, 9, True), (9, 10, False), (10, 11, True)]


def build_rubrics_for_agent(client, agent_email):
    handle = agent_handle(agent_email)
    qa_sample_tab = qa_sample_tab_title(handle)

    # A2:C skips the header row and only needs Pull Date/John's Ticket
    # Link/Gabby's Ticket Link -- the backup and score/notes columns don't
    # matter here. This is also the only read of qa_sample_tab's row
    # count this script needs -- seed the client's cache from it instead
    # of letting a later row_count() call re-read the same tab.
    rows = client.get_values(qa_sample_tab, "A2:C")
    client.seed_row_count(qa_sample_tab, len(rows) + 1)  # +1 for the header row this range skipped
    if not rows:
        print(f"No data rows found in '{qa_sample_tab}' -- nothing to build rubrics from.")
        return 0

    john_tab = rubric_tab_title(handle, "john")
    gabby_tab = rubric_tab_title(handle, "gabby")

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
            score_formula, notes_formula = rubric_formula_refs(handle, "john", john_start)
            all_cell_updates += [
                (qa_sample_tab, f"H{qa_row}", score_formula),
                (qa_sample_tab, f"I{qa_row}", notes_formula),
                (qa_sample_tab, f"L{qa_row}", john_start),
            ]
            append_rubric_block(client, handle, "john", pull_date, john_link, start_row=john_start)
        if gabby_link:
            gabby_start = next_block_start_row(client, gabby_tab)
            score_formula, notes_formula = rubric_formula_refs(handle, "gabby", gabby_start)
            all_cell_updates += [
                (qa_sample_tab, f"J{qa_row}", score_formula),
                (qa_sample_tab, f"K{qa_row}", notes_formula),
                (qa_sample_tab, f"M{qa_row}", gabby_start),
            ]
            append_rubric_block(client, handle, "gabby", pull_date, gabby_link, start_row=gabby_start)

        built += 1

    # One batchUpdate for every row's formula/helper cells, not one per row.
    client.write_cells(all_cell_updates)

    client.set_columns_wrap(qa_sample_tab, QA_SAMPLE_COLUMN_WRAPS)
    client.hide_columns(qa_sample_tab, 11, 13)  # L:M, the rubric-row helper columns
    setup_highlight_rules(client, qa_sample_tab, john_tab, gabby_tab)
    print(f"Built rubric blocks for {built} pull date(s) from '{qa_sample_tab}'.")
    return built


def main():
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    client = SheetsClient(get_sheets_service(), sheet_id)
    for agent_email in TARGET_AGENT_EMAILS:
        build_rubrics_for_agent(client, agent_email)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
