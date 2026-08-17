#!/usr/bin/env python3
"""
zendesk_client.py

Shared Zendesk helper: find every ticket that has at least one *public*
comment from a specific agent (matched by email) within a given UTC
datetime window [start_dt, end_dt).

Same two-step pattern as NXP_notebook's scripts/sync_to_notebooklm.py (same
Zendesk account, same credentials): Search API narrows down candidates,
then a per-ticket comments.json fetch confirms the exact qualifying
comment(s) -- Search's `commenter:`/`updated:` operators only have
day-level precision and don't tell you whether a specific comment was
public, so the real filter always happens against the comment thread
itself, with Search's date bounds padded a day on each side as a
generous net.

Required environment variables (read by callers, not this module):
  ZENDESK_SUBDOMAIN   e.g. "nextpoint" for nextpoint.zendesk.com
  ZENDESK_EMAIL       the email address tied to the API token
  ZENDESK_API_TOKEN   Zendesk API token
"""
from datetime import datetime, timedelta

import requests


def _auth(email, api_token):
    return (f"{email}/token", api_token)


def parse_zendesk_ts(ts):
    # Zendesk timestamps are ISO 8601 with a trailing "Z".
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _search_candidate_tickets(subdomain, email, api_token, agent_email, start_dt, end_dt):
    """Search for tickets with agent_email as a commenter, updated anywhere
    within [start_dt - 1 day, end_dt + 1 day] -- a deliberately generous net
    since Search's date operators are day-granular. Side-loads `users` so
    the caller can resolve each ticket's requester without a second
    lookup."""
    auth = _auth(email, api_token)
    query = (
        f"type:ticket commenter:{agent_email} "
        f'updated>={(start_dt - timedelta(days=1)).strftime("%Y-%m-%d")} '
        f'updated<={(end_dt + timedelta(days=1)).strftime("%Y-%m-%d")}'
    )
    url = f"https://{subdomain}.zendesk.com/api/v2/search.json"
    params = {"query": query, "sort_by": "updated_at", "sort_order": "asc", "include": "users"}
    tickets = []
    users = {}
    while url:
        resp = requests.get(url, auth=auth, params=params).json()
        if "error" in resp:
            raise RuntimeError(f"Zendesk search failed: {resp}")
        tickets.extend(t for t in resp.get("results", []) if t.get("result_type") == "ticket")
        for u in resp.get("users", []):
            users[u["id"]] = u.get("email") or u.get("name") or "unknown"
        url = resp.get("next_page")
        params = None  # next_page already includes query params
    return tickets, users


def _fetch_ticket_comments(subdomain, email, api_token, ticket_id):
    """Return (comments, author_emails) for a ticket: the full comment
    thread plus a {user_id: email} map side-loaded via include=users, so
    matching a specific agent by email doesn't need a separate user
    lookup per comment."""
    url = f"https://{subdomain}.zendesk.com/api/v2/tickets/{ticket_id}/comments.json"
    auth = _auth(email, api_token)
    params = {"include": "users"}
    comments = []
    author_emails = {}
    while url:
        resp = requests.get(url, auth=auth, params=params).json()
        if "error" in resp:
            raise RuntimeError(f"Zendesk comments fetch failed for ticket {ticket_id}: {resp}")
        comments.extend(resp.get("comments", []))
        for u in resp.get("users", []):
            if u.get("email"):
                author_emails[u["id"]] = u["email"].lower()
        url = resp.get("next_page")
        params = None  # next_page already includes query params
    return comments, author_emails


def find_tickets_with_public_comment(subdomain, email, api_token, agent_email, start_dt, end_dt):
    """Return one dict per ticket that has at least one public comment from
    agent_email with created_at in [start_dt, end_dt) (both tz-aware UTC
    datetimes, end exclusive). Each dict: id, link, subject, requester,
    status, comment_date (the LATEST qualifying comment's created_at, as an
    ISO string -- a ticket can have more than one matching comment in a
    window and only one column is available to record it in the Sheet)."""
    agent_email = agent_email.lower()
    candidates, search_users = _search_candidate_tickets(
        subdomain, email, api_token, agent_email, start_dt, end_dt
    )

    matched = []
    for t in candidates:
        comments, author_emails = _fetch_ticket_comments(subdomain, email, api_token, t["id"])
        qualifying = [
            c
            for c in comments
            if c.get("public", True)
            and author_emails.get(c.get("author_id")) == agent_email
            and start_dt <= parse_zendesk_ts(c["created_at"]) < end_dt
        ]
        if not qualifying:
            continue
        latest = max(qualifying, key=lambda c: parse_zendesk_ts(c["created_at"]))
        matched.append(
            {
                "id": t["id"],
                "link": f"https://{subdomain}.zendesk.com/agent/tickets/{t['id']}",
                "subject": t.get("subject") or "(no subject)",
                "requester": search_users.get(t.get("requester_id"), "unknown"),
                "status": t.get("status", "unknown"),
                "comment_date": latest["created_at"],
            }
        )
    return matched
