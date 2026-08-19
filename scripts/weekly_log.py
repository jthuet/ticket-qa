#!/usr/bin/env python3
"""
weekly_log.py

Appends one row per ticket with a new public comment from TARGET_AGENT_EMAIL
since the last run to the "Ticket Log" tab of GOOGLE_SHEET_ID. Runs weekly
via GitHub Actions (see .github/workflows/weekly_log.yml, Friday 11pm ET);
the workflow commits state/last_weekly_sync.json back to the repo after
each run so the next run only covers what's new since then, the same
cursor pattern the NotebookLM sync project uses for state/last_sync.json.

Required environment variables:
  ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, ZENDESK_API_TOKEN  -- same Zendesk
    credentials as the NotebookLM sync project (read-only ticket/comment
    access)
  GOOGLE_SERVICE_ACCOUNT_JSON  -- same Google service account as the
    NotebookLM project can be reused, but this Sheet must ALSO be shared
    with it (Editor) separately, and the Sheets API must be enabled for
    that Cloud project -- Docs/Drive being enabled there already doesn't
    cover Sheets
  GOOGLE_SHEET_ID  -- the target spreadsheet's ID (from its URL)

Optional environment variable:
  TARGET_AGENT_EMAIL  -- defaults to "jbell@nextpoint.com" if unset. Set
    this repo secret to track a different agent later without a code
    change. The tab this writes to is named after the agent's email
    handle (the part before "@"), so switching agents starts a fresh tab
    rather than mixing tickets from two agents into one.
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
from zendesk_client import find_tickets_with_public_comment  # noqa: E402
from sheets_writer import get_sheets_service, SheetsClient  # noqa: E402

# `or` (not .get(..., default)) because GitHub Actions substitutes an unset
# secret as an empty string, not a missing variable -- .get()'s default
# would never kick in.
TARGET_AGENT_EMAIL = os.environ.get("TARGET_AGENT_EMAIL") or "jbell@nextpoint.com"
AGENT_HANDLE = TARGET_AGENT_EMAIL.split("@")[0]
TAB_TITLE = f"{AGENT_HANDLE} Ticket Log"
HEADER = ["Week Ending", "Ticket ID", "Ticket Link", "Subject", "Requester", "Status", f"{AGENT_HANDLE} Comment Date"]

ET = ZoneInfo("America/New_York")
STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "state", "last_weekly_sync.json")
DEFAULT_LOOKBACK = timedelta(days=7)


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def main():
    state = load_state()
    window_end = datetime.now(timezone.utc)
    window_start = (
        datetime.fromisoformat(state["last_synced_at"])
        if "last_synced_at" in state
        else window_end - DEFAULT_LOOKBACK
    )

    subdomain = os.environ["ZENDESK_SUBDOMAIN"]
    email = os.environ["ZENDESK_EMAIL"]
    token = os.environ["ZENDESK_API_TOKEN"]
    sheet_id = os.environ["GOOGLE_SHEET_ID"]

    tickets = find_tickets_with_public_comment(subdomain, email, token, TARGET_AGENT_EMAIL, window_start, window_end)

    if tickets:
        # Labeled with the ET calendar date, matching how the QA Sample
        # tab's "Pull Date" is dated -- both jobs fire at the same
        # Friday-11pm-ET moment, which is already the next day in UTC.
        week_ending = window_end.astimezone(ET).date().isoformat()
        rows = [
            [week_ending, t["id"], t["link"], t["subject"], t["requester"], t["status"], t["comment_date"]]
            for t in sorted(tickets, key=lambda t: t["comment_date"])
        ]
        client = SheetsClient(get_sheets_service(), sheet_id)
        client.ensure_tab(TAB_TITLE, HEADER)
        client.append_rows(TAB_TITLE, rows)
        print(f"Appended {len(rows)} ticket(s) to '{TAB_TITLE}'.")
    else:
        print(f"No tickets with a new public {AGENT_HANDLE} comment since last sync.")

    state["last_synced_at"] = window_end.isoformat()
    save_state(state)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
