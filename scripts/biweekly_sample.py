#!/usr/bin/env python3
"""
biweekly_sample.py

Every two weeks, picks 6 random tickets (from tickets with a public
jbell@nextpoint.com comment in the preceding 2-week window) for manual QA
scoring, and appends one row to the "QA Sample" tab of GOOGLE_SHEET_ID: one
ticket for evaluator John to score, one for evaluator Gabby to score, and
4 shared backups. "John" and "Gabby" are just the two evaluator slots --
think eval1/eval2 -- not a filter on who commented; every one of the 6
tickets is drawn from the exact same jbell-commented population, and
John/Gabby's own email addresses never enter into it.

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

TARGET_AGENT_EMAIL defaults to "jbell@nextpoint.com" but can be overridden
by a repo secret of the same name, so tracking a different agent later
doesn't need a code change. The tab this writes to is named after the
agent's email handle (the part before "@"), so switching agents starts a
fresh tab rather than mixing tickets from two agents into one.

Right after computing this run's row, it writes a live formula (not a
static value) into the QA Sample row's John/Gabby Score and Notes cells,
pointing at exactly where the matching rubric block is about to be
written on "<handle>-john-rubrics" / "<handle>-gabby-rubrics" (see
scripts/rubrics.py's next_block_start_row()/rubric_formula_refs()) --
then writes the QA Sample row, then the rubric blocks themselves. Sheets
keeps that formula reference live from then on: typing a score or note
into the rubric tab shows up on QA Sample immediately, no sync script or
extra scheduled run required.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
from sampling import sample_window, SLOTS  # noqa: E402
from sheets_writer import get_sheets_service, ensure_tab, row_count, set_columns_wrap, write_rows_at  # noqa: E402
from rubrics import append_rubric_block, next_block_start_row, rubric_formula_refs, rubric_tab_title  # noqa: E402

# `or` (not .get(..., default)) because GitHub Actions substitutes an unset
# secret as an empty string, not a missing variable -- .get()'s default
# would never kick in.
TARGET_AGENT_EMAIL = os.environ.get("TARGET_AGENT_EMAIL") or "jbell@nextpoint.com"
AGENT_HANDLE = TARGET_AGENT_EMAIL.split("@")[0]
TAB_TITLE = f"{AGENT_HANDLE} QA Sample"
HEADER = [
    "Pull Date",
    "John's Ticket Link",
    "Gabby's Ticket Link",
    "Backup 1 Link",
    "Backup 2 Link",
    "Backup 3 Link",
    "Backup 4 Link",
    "John Score",
    "John Notes",
    "Gabby Score",
    "Gabby Notes",
]

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
        link("john"),
        link("gabby"),
        link("backup1"),
        link("backup2"),
        link("backup3"),
        link("backup4"),
        "",
        "",
        "",
        "",
    ]
    return row, picks["_population_size"]


# 0-indexed columns: H=7 John Score, I=8 John Notes, J=9 Gabby Score,
# K=10 Gabby Notes. Scores stay unwrapped (short numbers); notes wrap.
QA_SAMPLE_COLUMN_WRAPS = [(7, 8, False), (8, 9, True), (9, 10, False), (10, 11, True)]


def main():
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    service = get_sheets_service()
    ensure_tab(service, sheet_id, TAB_TITLE, HEADER)
    set_columns_wrap(service, sheet_id, TAB_TITLE, QA_SAMPLE_COLUMN_WRAPS)

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

    row, population_size = row_for_window(subdomain, email, token, today, window_start_date)
    if population_size < len(SLOTS):
        print(
            f"WARNING: only {population_size} qualifying ticket(s) in "
            f"{window_start_date.isoformat()}..{today.isoformat()} -- filled as many slots as available."
        )

    pull_date, john_link, gabby_link = row[0], row[1], row[2]

    # Figure out where each evaluator's rubric block is ABOUT to land, so
    # the QA Sample row can carry a live formula pointing at it from the
    # moment it's written -- rather than a blank cell some later sync
    # step would need to fill in.
    john_tab = rubric_tab_title(AGENT_HANDLE, "john")
    gabby_tab = rubric_tab_title(AGENT_HANDLE, "gabby")
    john_start = next_block_start_row(service, sheet_id, john_tab) if john_link else None
    gabby_start = next_block_start_row(service, sheet_id, gabby_tab) if gabby_link else None
    if john_start:
        row[7], row[8] = rubric_formula_refs(AGENT_HANDLE, "john", john_start)
    if gabby_start:
        row[9], row[10] = rubric_formula_refs(AGENT_HANDLE, "gabby", gabby_start)

    qa_row = row_count(service, sheet_id, TAB_TITLE) + 1
    write_rows_at(service, sheet_id, TAB_TITLE, qa_row, [row])
    print(f"Appended QA sample row for window {window_start_date.isoformat()}..{today.isoformat()}.")

    append_rubric_block(service, sheet_id, AGENT_HANDLE, "john", pull_date, john_link, start_row=john_start)
    append_rubric_block(service, sheet_id, AGENT_HANDLE, "gabby", pull_date, gabby_link, start_row=gabby_start)
    print(f"Appended rubric block(s) for {pull_date}.")

    state["last_window_end"] = today.isoformat()
    save_state(state)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
