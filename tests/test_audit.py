import openpyxl
from typer.testing import CliRunner

from main import app
from models import CriterionScore, ScoringResult
from review.spreadsheet import export

runner = CliRunner()


def _result(sid="2000012515", total=85.0):
    return ScoringResult(
        student_id=sid, student_name="T", assignment_id="462878",
        total_score=total, total_max=100, confidence=0.9,
        breakdown=[CriterionScore(criterion="P1", points_awarded=40,
                                  points_max=50, reasoning="ok"),
                   CriterionScore(criterion="P2", points_awarded=45,
                                  points_max=50, reasoning="ok")],
    )


def _set(path, column, value):
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    headers = [c.value for c in ws[1]]
    ws.cell(row=2, column=headers.index(column) + 1, value=value)
    wb.save(path)


def test_audit_clean(tmp_path):
    path = tmp_path / "scores.xlsx"
    export([_result()], path)
    result = runner.invoke(app, ["audit", "--scores", str(path)])
    assert result.exit_code == 0, result.output


def test_audit_detects_total_mismatch(tmp_path):
    path = tmp_path / "scores.xlsx"
    export([_result()], path)
    _set(path, "total_score", 90.0)
    result = runner.invoke(app, ["audit", "--scores", str(path)])
    assert result.exit_code == 1
    assert "total" in result.output


def test_audit_detects_stale_approval(tmp_path):
    path = tmp_path / "scores.xlsx"
    export([_result()], path)
    _set(path, "approved", "YES")
    _set(path, "score_changed", "YES")
    result = runner.invoke(app, ["audit", "--scores", str(path)])
    assert result.exit_code == 1
    assert "stale approval" in result.output
