import json
from pathlib import Path

from typer.testing import CliRunner

from main import app
from review.spreadsheet import load_reviewed

runner = CliRunner()


def write_grade(path: Path, sid: str, total: float = 85.0, items=None) -> None:
    items = items or [
        {"criterion": "P1", "points_awarded": 40.0, "points_max": 50.0, "reasoning": "ok"},
        {"criterion": "P2", "points_awarded": 45.0, "points_max": 50.0, "reasoning": "ok"},
    ]
    path.write_text(json.dumps({
        "student_id": sid, "student_name": "Test", "assignment_id": "462878",
        "total_score": total, "total_max": 100, "confidence": 0.9,
        "breakdown": items, "uncertain_parts": [], "llm_reasoning": "",
    }), encoding="utf-8")


def test_import_grades_writes_spreadsheet(tmp_path):
    grades = tmp_path / "grades"
    grades.mkdir()
    write_grade(grades / "2000012515.json", "2000012515")
    scores = tmp_path / "scores.xlsx"

    result = runner.invoke(app, ["import-grades", str(grades), "--scores", str(scores),
                                 "--column", "462878"])
    assert result.exit_code == 0, result.output
    records = load_reviewed(scores)
    assert len(records) == 1
    assert records[0].result.total_score == 85.0
    assert records[0].result.assignment_id == "462878"


def test_import_grades_rejects_total_mismatch(tmp_path):
    grades = tmp_path / "grades"
    grades.mkdir()
    write_grade(grades / "2000012515.json", "2000012515", total=90.0)
    scores = tmp_path / "scores.xlsx"

    result = runner.invoke(app, ["import-grades", str(grades), "--scores", str(scores)])
    assert result.exit_code == 1
    assert not scores.exists()


def test_import_grades_rejects_duplicate_ids(tmp_path):
    grades = tmp_path / "grades"
    grades.mkdir()
    write_grade(grades / "a.json", "2000012515")
    write_grade(grades / "b.json", "2000012515")
    scores = tmp_path / "scores.xlsx"

    result = runner.invoke(app, ["import-grades", str(grades), "--scores", str(scores)])
    assert result.exit_code == 1
    assert not scores.exists()
