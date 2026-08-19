#!/usr/bin/env python3
"""
rubrics.py

Shared helper for building and appending one QA rubric-scoring block per
pull date onto an evaluator's own "<agent handle>-<evaluator>-rubrics" tab
-- one block scoring John's assigned ticket (on the john tab), a separate
block scoring Gabby's (on the gabby tab), each against the same 4-metric
rubric.

Each block, 9 rows:
  Pull Date | Ticket Link
  Metric | Description | Score (1 Major Miss - 4 Excellent)
  <4 metric rows, Score column (C) left blank for the evaluator to fill in>
  Total |  | =SUM(...)              (live formula over the 4 score cells above)
  <Evaluator> Notes | <blank, for the evaluator to fill in>
  <blank spacer row, separating this block from the next>

Blocks are appended in order, oldest pull date first -- both the one-time
historical build (scripts/backfill_rubrics.py, working off whatever's
already in the QA Sample tab) and the recurring per-run append
(scripts/biweekly_sample.py, using the row it just wrote) call
append_rubric_block() for this.
"""
from sheets_writer import ensure_tab_exists, append_rows, row_count

RUBRIC_METRICS = [
    (
        "Accuracy of information",
        "Did the response correctly describe how the feature/workflow actually works in the software? "
        "Were any steps, field names, or settings misstated? If a claim was uncertain, did the rep verify "
        "it (checking documentation, testing in the platform, asking a colleague) rather than guessing?",
    ),
    (
        "Completeness of the answer",
        "Did the response address everything the client actually asked, including any sub-questions "
        "buried in the ticket? Did it anticipate an obvious follow-up (e.g., \"and here's how to also do "
        "X, which you'll likely need next\")? Response contains proper greeting and signatures. "
        "Appropriate screenshots and links to articles are included when helpful.",
    ),
    (
        "Evidence of investigation",
        "Is there a visible sign the response invloved investigation — checked the account/case settings, "
        "reproduced the issue, looked at logs — rather than being a generic or templated answer that "
        "happened to be in the ballpark? If the question was beyond the",
    ),
    (
        "Appropriate Escalation",
        "No unverified claims included in the response — uncertain answers were escalated internally "
        "rather than guessed at in the response.",
    ),
]

RUBRIC_HEADER = ["Metric", "Description", "Score (1 Major Miss - 4 Excellent)"]


def rubric_tab_title(agent_handle, evaluator_name):
    return f"{agent_handle}-{evaluator_name}-rubrics"


def build_rubric_block(start_row, pull_date, ticket_link, evaluator_name):
    """Returns the list-of-lists for one full 9-row block, given the
    1-indexed row it will start on -- needed so the Total row's SUM
    formula points at the right cells."""
    metric_rows = [[name, desc, ""] for name, desc in RUBRIC_METRICS]
    first_score_row = start_row + 2  # +0 pull date row, +1 header row, +2 first metric row
    last_score_row = first_score_row + len(RUBRIC_METRICS) - 1
    return [
        [pull_date, ticket_link],
        RUBRIC_HEADER,
        *metric_rows,
        ["Total", "", f"=SUM(C{first_score_row}:C{last_score_row})"],
        [f"{evaluator_name.capitalize()} Notes", ""],
        [],  # blank spacer before the next block
    ]


def append_rubric_block(service, sheet_id, agent_handle, evaluator_name, pull_date, ticket_link):
    """No-op if ticket_link is falsy -- that evaluator's slot was empty for
    this pull (population smaller than the number of sample slots), so
    there's no ticket to build a rubric block for."""
    if not ticket_link:
        return
    tab_title = rubric_tab_title(agent_handle, evaluator_name)
    ensure_tab_exists(service, sheet_id, tab_title)
    start_row = row_count(service, sheet_id, tab_title) + 1
    block = build_rubric_block(start_row, pull_date, ticket_link, evaluator_name)
    append_rows(service, sheet_id, tab_title, block)
