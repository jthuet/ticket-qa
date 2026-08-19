#!/usr/bin/env python3
"""
backfill_rubrics.py

One-time historical build: reads every existing row already written to the
"<agent handle> QA Sample" tab and appends one rubric-scoring block per
row onto each evaluator's own "<agent handle>-<evaluator>-rubrics" tab
(see scripts/rubrics.py) -- one block for John's ticket, one for Gabby's.

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
from rubrics import append_rubric_block  # noqa: E402
from sheets_writer import get_sheets_service  # noqa: E402

# `or` (not .get(..., default)) because GitHub Actions substitutes an unset
# secret as an empty string, not a missing variable -- .get()'s default
# would never kick in.
TARGET_AGENT_EMAIL = os.environ.get("TARGET_AGENT_EMAIL") or "jbell@nextpoint.com"
AGENT_HANDLE = TARGET_AGENT_EMAIL.split("@")[0]
QA_SAMPLE_TAB = f"{AGENT_HANDLE} QA Sample"


def main():
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    service = get_sheets_service()

    # A2:C skips the header row and only needs Pull Date/John's Ticket
    # Link/Gabby's Ticket Link -- the backup and score/notes columns don't
    # matter here.
    resp = service.spreadsheets().values().get(spreadsheetId=sheet_id, range=f"'{QA_SAMPLE_TAB}'!A2:C").execute()
    rows = resp.get("values", [])
    if not rows:
        print(f"No data rows found in '{QA_SAMPLE_TAB}' -- nothing to build rubrics from.")
        return

    built = 0
    for row in rows:
        pull_date = row[0] if len(row) > 0 else ""
        john_link = row[1] if len(row) > 1 else ""
        gabby_link = row[2] if len(row) > 2 else ""
        if not pull_date:
            continue
        append_rubric_block(service, sheet_id, AGENT_HANDLE, "john", pull_date, john_link)
        append_rubric_block(service, sheet_id, AGENT_HANDLE, "gabby", pull_date, gabby_link)
        built += 1

    print(f"Built rubric blocks for {built} pull date(s) from '{QA_SAMPLE_TAB}'.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
