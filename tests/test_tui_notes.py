"""Regression tests for the reviewer-notes prompt prefill."""
import json

from review import tui_components


class _Console:
    def print(self, *args, **kwargs):
        pass


_CONSOLE = _Console()


class _WS:
    def cell(self, **kwargs):
        return None


class _Session:
    def __init__(self):
        self.ws = _WS()
        self.idx = {"reviewer_notes": 0}
        self.updated = None

    def update_row(self, row_idx, row_data):
        self.updated = (row_idx, row_data)


def _row(total_score, total_max=100, notes="", override=None):
    return {
        "student_id": "2000012515",
        "student_name": "Test",
        "assignment_id": "1",
        "total_score": total_score,
        "total_max": total_max,
        "confidence": 0.9,
        "reviewer_notes": notes,
        "reviewer_override_score": override,
        "breakdown_json": json.dumps([
            {"criterion": "P2 slack", "points_awarded": 0, "points_max": 3,
             "reasoning": "扣3分：九个节点的 slack 全部错误。"},
        ]),
    }


def _capture_default(monkeypatch):
    seen = {}

    def fake_prompt(label, default="", **kwargs):
        seen["default"] = default
        return default

    monkeypatch.setattr(tui_components, "prompt_text", fake_prompt)
    return seen


def test_notes_prefill_deduction_summary(monkeypatch):
    seen = _capture_default(monkeypatch)
    row = _row(60)
    tui_components.handle_notes(_Session(), 2, row, console=_CONSOLE)
    assert "得分 60/100。" in seen["default"]
    assert "扣3分：九个节点的 slack 全部错误。" in seen["default"]
    assert row["reviewer_notes"] == seen["default"]


def test_notes_prefill_prefers_existing(monkeypatch):
    seen = _capture_default(monkeypatch)
    tui_components.handle_notes(_Session(), 2, _row(60, notes="已有评语"), console=_CONSOLE)
    assert seen["default"] == "已有评语"


def test_notes_prefill_uses_override_score(monkeypatch):
    seen = _capture_default(monkeypatch)
    row = _row(60, override=85)
    tui_components.handle_notes(_Session(), 2, row, console=_CONSOLE)
    assert seen["default"].startswith("得分 85/100。")


def test_notes_prefill_empty_for_perfect(monkeypatch):
    seen = _capture_default(monkeypatch)
    tui_components.handle_notes(_Session(), 2, _row(100), console=_CONSOLE)
    assert seen["default"] == ""


def test_approve_can_be_cancelled_from_notes(monkeypatch):
    class _WS:
        def cell(self, **kwargs):
            return None

    class _Session:
        def __init__(self):
            self.ws = _WS()
            self.idx = {"reviewer_notes": 0, "approved": 1}
            self.updated = None

        def update_row(self, row_idx, row_data):
            self.updated = (row_idx, row_data)

    def raise_interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(tui_components, "prompt_text", raise_interrupt)
    session = _Session()
    row = _row(60)
    assert tui_components.handle_approve(session, 2, row, console=_CONSOLE) is False
    assert session.updated is None
    assert "approved" not in row


def test_override_can_be_cancelled(monkeypatch):
    import review.tui_components as tc

    class _WS:
        def cell(self, **kwargs):
            return None

    class _Session:
        def __init__(self):
            self.ws = _WS()
            self.idx = {"reviewer_override_score": 0}
            self.updated = None

        def update_row(self, row_idx, row_data):
            self.updated = (row_idx, row_data)

    def raise_interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(tc, "Prompt", type("P", (), {"ask": staticmethod(raise_interrupt)}))
    session = _Session()
    row = _row(60)
    tc.handle_override(session, 2, row, console=_CONSOLE)
    assert session.updated is None
    assert row["reviewer_override_score"] is None


def test_notes_key_bindings_submit_newline_cancel():
    import pytest
    pt = pytest.importorskip("prompt_toolkit")
    from prompt_toolkit import PromptSession
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    def run(payload):
        with create_pipe_input() as inp:
            inp.send_text(payload)
            session = PromptSession(
                input=inp, output=DummyOutput(),
                key_bindings=tui_components._notes_key_bindings())
            return session.prompt(multiline=True)

    assert run("abc\r") == "abc"
    assert run("a\x0ab\r") == "a\nb"
    try:
        run("a\x1b")
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("Esc should cancel")


def test_notes_header_uses_decayed_score():
    from models import CriterionScore, ScoringResult

    result = ScoringResult(
        student_id="2000012515", student_name="Test", assignment_id="1",
        total_score=100, total_max=100, confidence=0.9,
        breakdown=[CriterionScore(criterion="P1", points_awarded=50,
                                  points_max=50, reasoning="ok.")],
    )
    assert result.deduction_summary(decay_factor=0.6).startswith(
        "得分 60/100（原始分 100 × 迟交系数 0.6）。")
    assert result.deduction_summary().startswith("得分 100/100。")


def test_approve_prompts_notes_for_decayed_perfect(monkeypatch):
    seen = {}

    def fake_prompt(label, default="", **kwargs):
        seen["default"] = default
        return default

    monkeypatch.setattr(tui_components, "prompt_text", fake_prompt)

    class _WS:
        def cell(self, **kwargs):
            return None

    class _Session:
        def __init__(self):
            self.ws = _WS()
            self.idx = {"reviewer_notes": 0, "approved": 1}
            self.updated = None

        def update_row(self, row_idx, row_data):
            self.updated = (row_idx, row_data)

    row = _row(100)
    row["decay_factor"] = 0.6
    assert tui_components.handle_approve(_Session(), 2, row, console=_CONSOLE) is True
    assert seen["default"].startswith("得分 60/100（原始分 100 × 迟交系数 0.6）。")
