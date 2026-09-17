from review.demo_data import create_demo_workspace
from review.spreadsheet import load_reviewed
from review.tui_components import find_submission_file, load_review_data


class TestDemoWorkspace:
    def test_creates_files(self, tmp_path):
        scores, submissions, rubric = create_demo_workspace(tmp_path)
        assert scores.exists()
        assert rubric.exists()
        assert submissions.is_dir()

    def test_scores_load(self, tmp_path):
        scores, _submissions, _rubric = create_demo_workspace(tmp_path)
        records = load_reviewed(scores)
        assert len(records) == 4
        by_id = {r.result.student_id: r for r in records}
        # Bob Li is pre-approved, Carol Wang is flagged for review.
        assert by_id["2023001002"].approved is True
        assert by_id["2023001003"].result.needs_review is True
        assert by_id["2023001003"].result.uncertain_parts

    def test_submission_files_found(self, tmp_path):
        scores, submissions, _rubric = create_demo_workspace(tmp_path)
        records = load_reviewed(scores)
        for record in records:
            path = find_submission_file(
                submissions, record.result.student_id, record.result.student_name
            )
            assert path is not None, record.result.student_id

    def test_review_data_loads_all_rows(self, tmp_path):
        scores, _submissions, _rubric = create_demo_workspace(tmp_path)
        _wb, _idx, rows = load_review_data(scores, needs_review_only=False, all_students=True)
        assert len(rows) == 4
