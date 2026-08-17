#!/usr/bin/env python3
"""
sampling.py

Given a date window, finds every ticket with a public comment from the
target agent in that window (via zendesk_client.find_tickets_with_public_comment)
and randomly draws 1 primary + up to 3 backup tickets for manual QA
scoring.

"Backup 1/2/3" have no meaning beyond draw order -- they're just distinct
spares in case the primary (or an earlier backup) turns out unsuitable to
score, not ranked by anything.
"""
import random

from zendesk_client import find_tickets_with_public_comment

SLOTS = ["primary", "backup1", "backup2", "backup3"]


def sample_window(subdomain, email, api_token, agent_email, window_start, window_end):
    """window_start/window_end are tz-aware UTC datetimes, end exclusive.
    Returns {"primary": ticket_or_None, "backup1": ..., "backup2": ...,
    "backup3": ..., "_population_size": int}. If fewer than 4 tickets
    qualify, slots are filled in SLOTS order and the rest are left None --
    this is expected to be rare, not silently normal, so callers should log
    a warning whenever _population_size < 4."""
    population = find_tickets_with_public_comment(
        subdomain, email, api_token, agent_email, window_start, window_end
    )
    picks = random.sample(population, k=min(len(SLOTS), len(population)))
    result = {slot: None for slot in SLOTS}
    for slot, ticket in zip(SLOTS, picks):
        result[slot] = ticket
    result["_population_size"] = len(population)
    return result
