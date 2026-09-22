"""Tests for the late-submission decay rule."""
import tempfile
from datetime import datetime
from pathlib import Path

from review.decay import parse_rule, parse_time, decay_factor, compute_decay
from models import ScoringResult, ReviewRecord


RULE_TXT = """# late-penalty rule
24   0.8
48   60%
72   0.4
"""


def _write_rule(tmp: Path) -> Path:
    p = tmp / "ddl_rule"
    p.write_text(RULE_TXT)
    return p


def _record(sid: str, score: float, override: float | None = None) -> ReviewRecord:
    res = ScoringResult(
        student_id=sid, student_name="S", assignment_id="1",
        total_score=score, total_max=100.0, confidence=0.9,
    )
    return ReviewRecord(result=res, reviewer_override_score=override)


def test_parse_rule_buckets():
    with tempfile.TemporaryDirectory() as td:
        buckets = parse_rule(_write_rule(Path(td)))
    assert len(buckets) == 4
    assert buckets[0].lo == 0 and buckets[0].hi == 24 and buckets[0].factor == 0.8
    assert buckets[-1].hi is None and buckets[-1].factor == 0.0


def test_decay_factor_edges():
    with tempfile.TemporaryDirectory() as td:
        buckets = parse_rule(_write_rule(Path(td)))
    assert decay_factor(0.0, buckets) is None       # on time
    assert decay_factor(-2.0, buckets) is None      # early
    assert decay_factor(0.5, buckets).factor == 0.8
    assert decay_factor(24.0, buckets).factor == 0.8   # boundary inclusive
    assert decay_factor(24.5, buckets).factor == 0.6
    assert decay_factor(72.0, buckets).factor == 0.4
    assert decay_factor(72.1, buckets).factor == 0.0


def test_compute_decay_uses_override_as_raw():
    with tempfile.TemporaryDirectory() as td:
        buckets = parse_rule(_write_rule(Path(td)))
    due = datetime(2026, 9, 16, 23, 59)
    times = {"1": {"newest": "2026-09-17 12:00:00", "all": ["2026-09-17 12:00:00"]}}
    rec = _record("1", 80.0, override=90.0)
    rows = compute_decay([rec], times, due, buckets)
    assert rows[0].factor == 0.8
    assert rows[0].raw_score == 90.0      # decay applies to the override
    assert rows[0].final_score == 72.0


def test_compute_decay_missing_time_defaults_on_time():
    with tempfile.TemporaryDirectory() as td:
        buckets = parse_rule(_write_rule(Path(td)))
    due = datetime(2026, 9, 16, 23, 59)
    rows = compute_decay([_record("9", 88.0)], {}, due, buckets)
    assert rows[0].factor == 1.0
    assert "缺失" in rows[0].reason


def test_parse_time_formats():
    assert parse_time("2026-09-12 22:32:57") == datetime(2026, 9, 12, 22, 32, 57)
    assert parse_time("2026-09-12 22:32") == datetime(2026, 9, 12, 22, 32)
    assert parse_time("garbage") is None
