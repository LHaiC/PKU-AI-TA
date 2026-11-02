"""Review queue must use the decayed score, not the raw one."""
import openpyxl

from models import CriterionScore, ScoringResult
from review.spreadsheet import export
from review.tui_components import load_review_data


def _perfect(sid):
    return ScoringResult(
        student_id=sid, student_name="Late Student", assignment_id="462878",
        total_score=100, total_max=100, confidence=0.9,
        breakdown=[CriterionScore(criterion="P1", points_awarded=10,
                                  points_max=10, reasoning="ok.")],
    )


def test_decayed_unapproved_row_is_in_needs_review(tmp_path):
    path = tmp_path / "scores.xlsx"
    export([_perfect("2300012450")], path)
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    idx = {c.value: i for i, c in enumerate(ws[1])}
    col = ws.max_column + 1
    ws.cell(row=1, column=col, value="decay_factor")
    ws.cell(row=2, column=col, value=0.6)
    wb.save(path)

    _ws, _idx, rows = load_review_data(path, True, False)
    assert [r[1]["student_id"] for r in rows] == ["2300012450"]
