#!/usr/bin/env python3
"""
weekly_log.py

For every agent in TARGET_AGENT_EMAILS, appends one row per ticket with a
new public comment from that agent since the last run, to that agent's
own "<handle> Ticket Log" tab. Runs weekly via GitHub Actions (see
.github/workflows/weekly_log.yml, Friday 11pm ET); the workflow commits
state/last_weekly_sync.json back to the repo after each run so the next
run only covers what's new since then, per agent -- the same cursor
pattern the NotebookLM sync project uses for state/last_sync.json.

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
  TARGET_AGENT_EMAIL  -- comma-separated list of agent emails, e.g.
    "jbell@nextpoint.com,gsperling@nextpoint.com". Defaults to
    "jbell@nextpoint.com" if unset. Add a new agent to this list to start
    tracking them going forward -- their own tab (named after their email
    handle, the part before "@") is created automatically on first run.
    See the README's "Adding another agent" for the one-time historical
    backfill a newly added agent still needs.
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
TARGET_AGENT_EMAILS = [
    e.strip() for e in (os.environ.get("TARGET_AGENT_EMAIL") or "jbell@nextpoint.com").split(",") if e.strip()
]

ET = ZoneInfo("America/New_York")
STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "state", "last_weekly_sync.json")
DEFAULT_LOOKBACK = timedelta(days=7)


def agent_handle(agent_email):
    return agent_email.split("@")[0]


def tab_title_for(handle):
    return f"{handle} Ticket Log"


def header_for(handle):
    return ["Week Ending", "Ticket ID", "Ticket Link", "Subject", "Requester", "Status", f"{handle} Comment Date"]


def load_state():
    """{agent_email: {"last_synced_at": iso timestamp}}. Migrates the old
    single-agent, flat {"last_synced_at": ...} shape (from before multiple
    agents were supported) into the first configured agent's entry, so an
    existing cursor isn't lost/reset just because this ran."""
    if not os.path.exists(STATE_PATH):
        return {}
    with open(STATE_PATH) as f:
        state = json.load(f)
    if "last_synced_at" in state:
        old_cursor = state.pop("last_synced_at")
        state.setdefault(TARGET_AGENT_EMAILS[0], {})["last_synced_at"] = old_cursor
    return state


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def main():
    state = load_state()
    window_end = datetime.now(timezone.utc)

    subdomain = os.environ["ZENDESK_SUBDOMAIN"]
    email = os.environ["ZENDESK_EMAIL"]
    token = os.environ["ZENDESK_API_TOKEN"]
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    client = SheetsClient(get_sheets_service(), sheet_id)

    for agent_email in TARGET_AGENT_EMAILS:
        handle = agent_handle(agent_email)
        agent_state = state.setdefault(agent_email, {})
        window_start = (
            datetime.fromisoformat(agent_state["last_synced_at"])
            if "last_synced_at" in agent_state
            else window_end - DEFAULT_LOOKBACK
        )

        tickets = find_tickets_with_public_comment(subdomain, email, token, agent_email, window_start, window_end)

        if tickets:
            # Labeled with the ET calendar date, matching how the QA Sample
            # tab's "Pull Date" is dated -- both jobs fire at the same
            # Friday-11pm-ET moment, which is already the next day in UTC.
            week_ending = window_end.astimezone(ET).date().isoformat()
            rows = [
                [week_ending, t["id"], t["link"], t["subject"], t["requester"], t["status"], t["comment_date"]]
                for t in sorted(tickets, key=lambda t: t["comment_date"])
            ]
            tab_title = tab_title_for(handle)
            client.ensure_tab(tab_title, header_for(handle))
            # write_rows_at (not append_rows) at a precomputed row, so a
            # retry after a dropped connection can't duplicate rows if
            # the original request actually landed server-side -- see
            # backfill_sample.py's write for the same reasoning.
            start_row = client.row_count(tab_title) + 1
            client.write_rows_at(tab_title, start_row, rows)
            print(f"Appended {len(rows)} ticket(s) to '{tab_title}'.")
        else:
            print(f"No tickets with a new public {handle} comment since last sync.")

        agent_state["last_synced_at"] = window_end.isoformat()

    save_state(state)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
