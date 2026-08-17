#!/usr/bin/env python3
"""
sampling.py

Given a date window, finds every ticket with a public comment from the
target agent in that window (via zendesk_client.find_tickets_with_public_comment)
and randomly draws 6 distinct tickets from that same population: one for
John to score, one for Gabby to score, and 4 shared backups. The ticket
selection criteria is the same regardless of who's scoring -- John and
Gabby are the two evaluators, not a filter on who commented.

The 4 backups have no meaning beyond draw order -- they're just distinct
spares in case John's or Gabby's assigned ticket (or an earlier backup)
turns out unsuitable to score, not ranked by anything or reserved for one
evaluator over the other.
"""
import random

from zendesk_client import find_tickets_with_public_comment

SLOTS = ["john", "gabby", "backup1", "backup2", "backup3", "backup4"]


def sample_window(subdomain, email, api_token, agent_email, window_start, window_end):
    """window_start/window_end are tz-aware UTC datetimes, end exclusive.
    Returns {"john": ticket_or_None, "gabby": ticket_or_None, "backup1":
    ..., "backup2": ..., "backup3": ..., "backup4": ...,
    "_population_size": int}. If fewer than 6 tickets qualify, slots are
    filled in SLOTS order and the rest are left None -- this is expected to
    be rare, not silently normal, so callers should log a warning whenever
    _population_size < len(SLOTS)."""
    population = find_tickets_with_public_comment(
        subdomain, email, api_token, agent_email, window_start, window_end
    )
    picks = random.sample(population, k=min(len(SLOTS), len(population)))
    result = {slot: None for slot in SLOTS}
    for slot, ticket in zip(SLOTS, picks):
        result[slot] = ticket
    result["_population_size"] = len(population)
    return result
