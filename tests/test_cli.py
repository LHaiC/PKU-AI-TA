import json
from pathlib import Path

from typer.testing import CliRunner

from main import app
from models import CriterionScore, ScoringResult
from review.spreadsheet import export, load_reviewed

runner = CliRunner()


def make_result(
    student_id: str = "2023001001",
    student_name: str = "Alice Zhang",
    total_score: float = 85.0,
    total_max: float = 100.0,
    confidence: float = 0.9,
    needs_review: bool = False,
) -> ScoringResult:
    return ScoringResult(
        student_id=student_id,
        student_name=student_name,
        assignment_id="423829",
        total_score=total_score,
        total_max=total_max,
        confidence=confidence,
        needs_review=needs_review,
        breakdown=[
            CriterionScore(criterion="Problem 1", points_awarded=total_score,
                           points_max=total_max, reasoning="Reason."),
        ],
        llm_reasoning="Summary.",
    )


def write_scores(tmp_path: Path, results: list[ScoringResult]) -> Path:
    path = tmp_path / "scores.xlsx"
    export(results, path)
    return path


class TestStatusCommand:
    def test_json_summary(self, tmp_path):
        scores = write_scores(tmp_path, [
            make_result("2023001001", total_score=95.0),
            make_result("2023001002", total_score=60.0, confidence=0.4, needs_review=True),
        ])
        result = runner.invoke(app, ["status", "--scores", str(scores), "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["total"] == 2
        assert payload["approved"] == 0
        assert payload["pending"] == 2
        assert payload["needs_review"] == 1
        assert len(payload["students"]) == 2

    def test_text_lists_pending(self, tmp_path):
        scores = write_scores(tmp_path, [make_result()])
        result = runner.invoke(app, ["status", "--scores", str(scores)])
        assert result.exit_code == 0
        assert "Pending approval" in result.stdout
        assert "2023001001" in result.stdout

    def test_missing_file_fails(self, tmp_path):
        result = runner.invoke(app, ["status", "--scores", str(tmp_path / "nope.xlsx")])
        assert result.exit_code == 1


class TestShowCommand:
    def test_json_detail(self, tmp_path):
        scores = write_scores(tmp_path, [make_result(needs_review=True)])
        result = runner.invoke(app, ["show", "--student", "2023001001", "--scores", str(scores), "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["records"][0]["student_id"] == "2023001001"
        assert len(payload["records"][0]["breakdown"]) == 1
        assert payload["records"][0]["approved"] is False

    def test_unknown_student_fails(self, tmp_path):
        scores = write_scores(tmp_path, [make_result()])
        result = runner.invoke(app, ["show", "--student", "9999999999", "--scores", str(scores)])
        assert result.exit_code == 1


class TestApproveCommand:
    def test_non_perfect_auto_fills_notes(self, tmp_path):
        """Non-perfect approve without --notes auto-generates notes from the breakdown."""
        scores = write_scores(tmp_path, [make_result(total_score=85.0)])
        result = runner.invoke(app, ["approve", "--student", "2023001001", "--scores", str(scores)])
        assert result.exit_code == 0
        record = load_reviewed(scores)[0]
        assert record.approved is True
        assert record.reviewer_notes and "85" in record.reviewer_notes

    def test_approve_with_notes(self, tmp_path):
        scores = write_scores(tmp_path, [make_result(total_score=85.0)])
        result = runner.invoke(app, [
            "approve", "--student", "2023001001", "--scores", str(scores), "--notes", "Deducted 15.",
        ])
        assert result.exit_code == 0
        record = load_reviewed(scores)[0]
        assert record.approved is True
        assert record.reviewer_notes == "Deducted 15."
        assert record.final_score == 85.0

    def test_approve_with_override(self, tmp_path):
        scores = write_scores(tmp_path, [make_result(total_score=85.0)])
        result = runner.invoke(app, [
            "approve", "--student", "2023001001", "--scores", str(scores),
            "--score", "90", "--notes", "Regraded by hand.",
        ])
        assert result.exit_code == 0
        record = load_reviewed(scores)[0]
        assert record.approved is True
        assert record.reviewer_override_score == 90.0
        assert record.final_score == 90.0

    def test_perfect_score_needs_no_notes(self, tmp_path):
        scores = write_scores(tmp_path, [make_result(total_score=100.0)])
        result = runner.invoke(app, ["approve", "--student", "2023001001", "--scores", str(scores)])
        assert result.exit_code == 0
        assert load_reviewed(scores)[0].approved is True

    def test_force_allows_non_perfect_without_notes(self, tmp_path):
        scores = write_scores(tmp_path, [make_result(total_score=85.0)])
        result = runner.invoke(app, [
            "approve", "--student", "2023001001", "--scores", str(scores), "--force",
        ])
        assert result.exit_code == 0
        assert load_reviewed(scores)[0].approved is True

    def test_json_output(self, tmp_path):
        scores = write_scores(tmp_path, [make_result(total_score=100.0)])
        result = runner.invoke(app, [
            "approve", "--student", "2023001001", "--scores", str(scores), "--json",
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["changes"][0]["ok"] is True
        assert payload["changes"][0]["approved"] is True

    def test_revoke(self, tmp_path):
        scores = write_scores(tmp_path, [make_result(total_score=100.0)])
        runner.invoke(app, ["approve", "--student", "2023001001", "--scores", str(scores)])
        result = runner.invoke(app, [
            "approve", "--student", "2023001001", "--scores", str(scores), "--revoke",
        ])
        assert result.exit_code == 0
        assert load_reviewed(scores)[0].approved is False

    def test_score_with_multiple_students_rejected(self, tmp_path):
        scores = write_scores(tmp_path, [
            make_result("2023001001"),
            make_result("2023001002"),
        ])
        result = runner.invoke(app, [
            "approve", "--student", "2023001001,2023001002",
            "--scores", str(scores), "--score", "90",
        ])
        assert result.exit_code == 1

    def test_auto_perfect(self, tmp_path):
        scores = write_scores(tmp_path, [
            make_result("2023001001", total_score=100.0),
            make_result("2023001002", total_score=70.0),
            make_result("2023001003", total_score=100.0, needs_review=True),
        ])
        result = runner.invoke(app, ["approve", "--auto-perfect", "--scores", str(scores)])
        assert result.exit_code == 0
        records = {r.result.student_id: r for r in load_reviewed(scores)}
        assert records["2023001001"].approved is True
        assert records["2023001002"].approved is False
        # Flagged for review: perfect but must not be auto-approved
        assert records["2023001003"].approved is False
