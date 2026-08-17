#!/usr/bin/env python3
"""
biweekly_sample.py

Every two weeks, picks a random 1 primary + up to 3 backup tickets (from
tickets with a public jbell@nextpoint.com comment in the preceding 2-week
window) for manual QA scoring, and appends one row to the "QA Sample" tab
of GOOGLE_SHEET_ID.

GitHub Actions cron has no native "every 2 weeks" schedule, so this runs
every Friday (see .github/workflows/biweekly_sample.yml, same 11pm ET time
as weekly_log.yml) and self-gates: it only actually samples on Fridays that
are an exact multiple of WINDOW_DAYS after ANCHOR_DATE (2026-08-14 -- the
most recent date the user's own every-two-weeks cadence should have landed
on when this was set up). Every other Friday it's a no-op.

"Today" is always computed in US Eastern time, not UTC -- at 11pm EDT the
UTC calendar date has already rolled to Saturday, so a naive UTC .date()
would gate/label every run one day late.

state/last_sample_window.json records the end date of the last window
actually sampled (set by this script AND by backfill_sample.py), so a
manual re-run of the workflow on an already-covered date can't append a
duplicate row.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
from sampling import sample_window  # noqa: E402
from sheets_writer import get_sheets_service, ensure_tab, append_rows  # noqa: E402

TARGET_AGENT_EMAIL = "jbell@nextpoint.com"
TAB_TITLE = "QA Sample"
HEADER = ["Pull Date", "Primary Ticket Link", "Backup 1 Link", "Backup 2 Link", "Backup 3 Link", "Score", "Notes"]

ET = ZoneInfo("America/New_York")
ANCHOR_DATE = datetime(2026, 8, 14, tzinfo=ET).date()
WINDOW_DAYS = 14

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "state", "last_sample_window.json")


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def today_et():
    return datetime.now(ET).date()


def et_window_bounds(window_start_date, window_end_date):
    """UTC (start_inclusive, end_exclusive) datetimes covering every moment
    of window_start_date through window_end_date, treated as US Eastern
    calendar dates (matching how these windows are described, e.g. "EOD
    8-14-2026") -- not UTC calendar dates, which would shift the real-world
    boundary by several hours."""
    start = datetime.combine(window_start_date, datetime.min.time(), tzinfo=ET)
    end = datetime.combine(window_end_date + timedelta(days=1), datetime.min.time(), tzinfo=ET)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def row_for_window(subdomain, email, token, window_end_date, window_start_date):
    """Returns (row, population_size) for the 2-week (or, for the one short
    backfill window, shorter) window ending window_end_date, inclusive of
    both dates."""
    window_start, window_end = et_window_bounds(window_start_date, window_end_date)
    picks = sample_window(subdomain, email, token, TARGET_AGENT_EMAIL, window_start, window_end)

    def link(slot):
        ticket = picks[slot]
        return ticket["link"] if ticket else ""

    row = [
        window_end_date.isoformat(),
        link("primary"),
        link("backup1"),
        link("backup2"),
        link("backup3"),
        "",
        "",
    ]
    return row, picks["_population_size"]


def main():
    today = today_et()
    days_since_anchor = (today - ANCHOR_DATE).days
    if days_since_anchor < 0 or days_since_anchor % WINDOW_DAYS != 0:
        print(
            f"Not a sample date ({today.isoformat()} is not a multiple of {WINDOW_DAYS} days "
            f"after anchor {ANCHOR_DATE.isoformat()}) -- skipping."
        )
        return

    state = load_state()
    last_window_end = state.get("last_window_end")
    if last_window_end and today.isoformat() <= last_window_end:
        print(f"Window ending {today.isoformat()} already sampled (last: {last_window_end}) -- skipping.")
        return

    window_start_date = today - timedelta(days=WINDOW_DAYS - 1)

    subdomain = os.environ["ZENDESK_SUBDOMAIN"]
    email = os.environ["ZENDESK_EMAIL"]
    token = os.environ["ZENDESK_API_TOKEN"]
    sheet_id = os.environ["GOOGLE_SHEET_ID"]

    row, population_size = row_for_window(subdomain, email, token, today, window_start_date)
    if population_size < 4:
        print(
            f"WARNING: only {population_size} qualifying ticket(s) in "
            f"{window_start_date.isoformat()}..{today.isoformat()} -- filled as many slots as available."
        )

    service = get_sheets_service()
    ensure_tab(service, sheet_id, TAB_TITLE, HEADER)
    append_rows(service, sheet_id, TAB_TITLE, [row])
    print(f"Appended QA sample row for window {window_start_date.isoformat()}..{today.isoformat()}.")

    state["last_window_end"] = today.isoformat()
    save_state(state)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
