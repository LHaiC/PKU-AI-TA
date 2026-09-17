from models import ReviewRecord, ScoringResult


def make_result(**kwargs) -> ScoringResult:
    defaults = dict(
        student_id="2100012345",
        student_name="Test Student",
        assignment_id="_1_1",
        total_score=85.0,
        total_max=100.0,
        confidence=0.9,
    )
    defaults.update(kwargs)
    return ScoringResult(**defaults)


class TestScoringResultPct:
    def test_pct_normal(self):
        r = make_result(total_score=75.0, total_max=100.0)
        assert r.pct == 75.0

    def test_pct_zero_max(self):
        r = make_result(total_score=0.0, total_max=0.0)
        assert r.pct == 0.0

    def test_pct_rounds_to_one_decimal(self):
        r = make_result(total_score=1.0, total_max=3.0)
        assert r.pct == 33.3


class TestReviewRecordFinalScore:
    def test_uses_llm_score_when_no_override(self):
        record = ReviewRecord(result=make_result(total_score=80.0), approved=True)
        assert record.final_score == 80.0

    def test_uses_override_when_set(self):
        record = ReviewRecord(result=make_result(total_score=80.0), reviewer_override_score=90.0, approved=True)
        assert record.final_score == 90.0

    def test_override_zero_is_respected(self):
        # 0 is a valid override (not the same as "no override")
        record = ReviewRecord(result=make_result(total_score=80.0), reviewer_override_score=0.0, approved=True)
        assert record.final_score == 0.0
