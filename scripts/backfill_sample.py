#!/usr/bin/env python3
"""
backfill_sample.py

One-time historical pull: builds the same "QA Sample" rows
biweekly_sample.py produces going forward, but for every 2-week window
between RANGE_START (2026-05-01) and ANCHOR_END (2026-08-14 -- the most
recent date the every-two-weeks cadence should have landed on when this
was set up). Windows are counted backward from the anchor (8/1-8/14,
7/18-7/31, ... down to 5/9-5/22); the leftover 5/1-5/8 stretch (8 days,
short of a full 2-week window) is included as its own short row rather
than dropped.

Run manually once via .github/workflows/backfill_sample.yml
(workflow_dispatch) -- never on a schedule. It always appends and has no
cursor guarding it (unlike the daily NotebookLM sync's own backfill.py,
which is designed to be safely re-run), since it's meant to run exactly
once: re-running it would duplicate every historical row. If it ever does
need to be re-run, clear the "QA Sample" tab first.

After it finishes, state/last_sample_window.json is seeded with
ANCHOR_END so the very next scheduled biweekly_sample.py run (2026-08-28)
doesn't try to re-sample the 2026-08-14 window this script already wrote.
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from biweekly_sample import row_for_window, HEADER, TAB_TITLE, ANCHOR_DATE, save_state  # noqa: E402
from sheets_writer import get_sheets_service, ensure_tab, append_rows  # noqa: E402

RANGE_START = date(2026, 5, 1)
ANCHOR_END = ANCHOR_DATE  # 2026-08-14
WINDOW_DAYS = 14


def build_windows():
    """Full 2-week windows counting back from ANCHOR_END, plus one final
    short window covering whatever's left before RANGE_START. Returns a
    list of (window_start, window_end) date pairs, oldest first."""
    windows = []
    window_end = ANCHOR_END
    while window_end - timedelta(days=WINDOW_DAYS - 1) >= RANGE_START:
        window_start = window_end - timedelta(days=WINDOW_DAYS - 1)
        windows.append((window_start, window_end))
        window_end = window_start - timedelta(days=1)
    if window_end >= RANGE_START:
        windows.append((RANGE_START, window_end))  # short leftover stretch, e.g. 5/1-5/8
    return list(reversed(windows))


def main():
    subdomain = os.environ["ZENDESK_SUBDOMAIN"]
    email = os.environ["ZENDESK_EMAIL"]
    token = os.environ["ZENDESK_API_TOKEN"]
    sheet_id = os.environ["GOOGLE_SHEET_ID"]

    windows = build_windows()
    print(f"Backfilling {len(windows)} window(s) from {windows[0][0]} to {windows[-1][1]}...")

    service = get_sheets_service()
    ensure_tab(service, sheet_id, TAB_TITLE, HEADER)

    rows = []
    for window_start, window_end in windows:
        row, population_size = row_for_window(subdomain, email, token, window_end, window_start)
        if population_size < 4:
            print(f"  WARNING: only {population_size} qualifying ticket(s) in {window_start}..{window_end}.")
        else:
            print(f"  {window_start}..{window_end}: {population_size} qualifying ticket(s).")
        rows.append(row)

    append_rows(service, sheet_id, TAB_TITLE, rows)
    print(f"Appended {len(rows)} historical row(s) to '{TAB_TITLE}'.")

    save_state({"last_window_end": ANCHOR_END.isoformat()})
    print(
        f"Seeded state/last_sample_window.json with last_window_end={ANCHOR_END.isoformat()} "
        f"so the next scheduled biweekly run (2026-08-28) doesn't re-sample it."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
