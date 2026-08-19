#!/usr/bin/env python3
"""
rubric_sync.py

Reads every rubric block off "<handle>-john-rubrics" and
"<handle>-gabby-rubrics" and copies each one's Total (rubric column C,
"Total" row) into the matching "John Score"/"Gabby Score" cell of the
"<handle> QA Sample" tab, and each one's Notes (rubric column B, the
"<Evaluator> Notes" row -- B/C are merged there, and a merged cell's
value always lives in its top-left cell) into "John Notes"/"Gabby Notes"
-- matched by Pull Date, the only key both tabs share. A score only
copies over if it's a real number; notes only copy over if non-empty
("if complete").

Called from scripts/biweekly_sample.py on every run (not just an actual
sampling week), so a score/notes an evaluator finishes gets synced back
promptly rather than waiting for the next 2-week sampling cycle. Safe to
call repeatedly -- it just re-copies whatever's currently in the rubric
tab, so a still-blank rubric cell copies nothing, and an already-synced
one is just overwritten with the same value.

Caveat worth knowing: a block's Total is a live =SUM() formula over 4
cells, and SUM() treats blank cells as 0 -- so an evaluator who has only
scored 1 of the 4 metrics so far already has a numeric (non-blank) Total,
and it WILL get synced over as if scoring were complete. There's
currently no way to tell "genuinely scored 0" apart from "not scored
yet" from the Total alone.
"""
from rubrics import BLOCK_STRIDE, NOTES_ROW_OFFSET, TOTAL_ROW_OFFSET, rubric_tab_title
from sheets_writer import tab_exists

QA_SAMPLE_SCORE_COLUMN = {"john": "H", "gabby": "J"}
QA_SAMPLE_NOTES_COLUMN = {"john": "I", "gabby": "K"}


def _is_number(value):
    if value in (None, ""):
        return False
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _read_blocks(service, sheet_id, tab_title):
    """Yields (pull_date, total_value, notes_value) for every block in
    tab_title. Reads columns A-C in one call and slices every
    BLOCK_STRIDE rows to line back up with how rubrics.py writes them,
    including the unwritten blank gap row between blocks."""
    resp = service.spreadsheets().values().get(spreadsheetId=sheet_id, range=f"'{tab_title}'!A:C").execute()
    rows = resp.get("values", [])
    for i in range(0, len(rows), BLOCK_STRIDE):
        block = rows[i : i + BLOCK_STRIDE]
        pull_date_row = block[0] if block else []
        pull_date = pull_date_row[0] if pull_date_row else ""
        if not pull_date:
            continue
        total_row = block[TOTAL_ROW_OFFSET] if len(block) > TOTAL_ROW_OFFSET else []
        notes_row = block[NOTES_ROW_OFFSET] if len(block) > NOTES_ROW_OFFSET else []
        total_value = total_row[2] if len(total_row) > 2 else ""
        # Column B, not C -- B/C are merged on the Notes row, and a merged
        # cell's value always lives in its top-left cell.
        notes_value = notes_row[1] if len(notes_row) > 1 else ""
        yield pull_date, total_value, notes_value


def _qa_sample_row_map(service, sheet_id, qa_sample_tab):
    """{pull_date: 1-indexed row number} for every data row in the QA
    Sample tab, so a rubric block can be matched back to the row it
    came from."""
    resp = service.spreadsheets().values().get(spreadsheetId=sheet_id, range=f"'{qa_sample_tab}'!A:A").execute()
    return {row[0]: idx + 1 for idx, row in enumerate(resp.get("values", [])) if row}


def sync_completed_scores(service, sheet_id, agent_handle, qa_sample_tab):
    """Returns the number of cells updated."""
    if not tab_exists(service, sheet_id, qa_sample_tab):
        return 0  # nothing sampled yet -- nothing to sync into

    row_map = _qa_sample_row_map(service, sheet_id, qa_sample_tab)
    data = []
    for evaluator in ("john", "gabby"):
        tab_title = rubric_tab_title(agent_handle, evaluator)
        if not tab_exists(service, sheet_id, tab_title):
            continue
        score_col = QA_SAMPLE_SCORE_COLUMN[evaluator]
        notes_col = QA_SAMPLE_NOTES_COLUMN[evaluator]
        for pull_date, total_value, notes_value in _read_blocks(service, sheet_id, tab_title):
            row = row_map.get(pull_date)
            if not row:
                continue  # a rubric block with no matching QA Sample row -- shouldn't happen
            if _is_number(total_value):
                data.append({"range": f"'{qa_sample_tab}'!{score_col}{row}", "values": [[float(total_value)]]})
            if notes_value:
                data.append({"range": f"'{qa_sample_tab}'!{notes_col}{row}", "values": [[notes_value]]})

    if data:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=sheet_id, body={"valueInputOption": "USER_ENTERED", "data": data}
        ).execute()
    return len(data)
