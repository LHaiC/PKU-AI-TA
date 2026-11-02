from models import ReviewRecord, ScoringResult
from submitter.blackboard import split_submittable


def record(sid, approved=True, changed=False):
    return ReviewRecord(
        result=ScoringResult(student_id=sid, student_name="T", assignment_id="1",
                             total_score=90, total_max=100, confidence=0.9),
        approved=approved, score_changed=changed,
    )


def test_split_submittable_skips_stale_approvals():
    ready, changed, unapproved = split_submittable([
        record("1"), record("2", changed=True), record("3", approved=False),
    ])
    assert [r.result.student_id for r in ready] == ["1"]
    assert [r.result.student_id for r in changed] == ["2"]
    assert [r.result.student_id for r in unapproved] == ["3"]
