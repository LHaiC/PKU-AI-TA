import json
import pytest
from unittest.mock import MagicMock, patch

from models import Attachment, Submission
from scorer.llm import _parse_json, _extract_text, score_submission


VALID_LLM_RESPONSE = {
    "total_score": 95.0,
    "total_max": 100.0,
    "confidence": 0.9,
    "breakdown": [
        {"criterion": "Correctness", "points_awarded": 45.0, "points_max": 50.0, "reasoning": "Mostly correct."},
        {"criterion": "Style", "points_awarded": 50.0, "points_max": 50.0, "reasoning": "Clean code."},
    ],
    "uncertain_parts": [],
    "llm_reasoning": "Good overall submission.",
}


class TestParseJson:
    def test_clean_json(self):
        raw = json.dumps(VALID_LLM_RESPONSE)
        result = _parse_json(raw)
        assert result["total_score"] == 95.0

    def test_strips_markdown_fences(self):
        raw = f"```json\n{json.dumps(VALID_LLM_RESPONSE)}\n```"
        result = _parse_json(raw)
        assert result["confidence"] == 0.9

    def test_strips_plain_fences(self):
        raw = f"```\n{json.dumps(VALID_LLM_RESPONSE)}\n```"
        result = _parse_json(raw)
        assert result["total_max"] == 100.0

    def test_extracts_json_from_surrounding_text(self):
        raw = f"Here is the score:\n{json.dumps(VALID_LLM_RESPONSE)}\nEnd."
        result = _parse_json(raw)
        assert result["total_score"] == 95.0

    def test_raises_on_invalid_json(self):
        with pytest.raises((ValueError, Exception)):
            _parse_json("This is not JSON at all.")


class TestExtractText:
    def test_returns_string(self):
        att = Attachment(filename="report.pdf", data=b"%PDF-fake")
        result = _extract_text(att)
        assert isinstance(result, str)

    def test_handles_unreadable_pdf(self):
        att = Attachment(filename="bad.pdf", data=b"not a real pdf")
        result = _extract_text(att)
        # Should not raise; returns an error message instead
        assert isinstance(result, str)


class TestScoreSubmission:
    def _make_submission(self, text="My answer.", attachments=None):
        return Submission(
            student_id="2100012345",
            student_name="Test Student",
            assignment_id="_1_1",
            assignment_title="HW1",
            text_content=text,
            attachments=attachments or [],
        )

    def _mock_response(self, content: str):
        choice = MagicMock()
        choice.message.content = content
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    @patch("scorer.llm._get_client")
    def test_returns_scoring_result(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_response(
            json.dumps(VALID_LLM_RESPONSE)
        )
        mock_get_client.return_value = mock_client

        result = score_submission(self._make_submission(), rubric="Grade on correctness (50pts) and style (50pts).")

        assert result.student_id == "2100012345"
        assert result.total_score == 95.0
        assert result.total_max == 100.0
        assert len(result.breakdown) == 2
        assert result.needs_review is False  # confidence=0.9 > threshold=0.75

    @patch("scorer.llm._get_client")
    def test_flags_low_confidence_for_review(self, mock_get_client):
        low_conf = {**VALID_LLM_RESPONSE, "confidence": 0.5}
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_response(json.dumps(low_conf))
        mock_get_client.return_value = mock_client

        result = score_submission(self._make_submission(), rubric="Rubric.")
        assert result.needs_review is True

    @patch("scorer.llm._get_client")
    def test_flags_uncertain_parts_for_review(self, mock_get_client):
        with_uncertain = {
            **VALID_LLM_RESPONSE,
            "confidence": 0.9,
            "uncertain_parts": [{"description": "Unclear argument", "suggested_score": 5.0, "suggested_max": 10.0}],
        }
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_response(json.dumps(with_uncertain))
        mock_get_client.return_value = mock_client

        result = score_submission(self._make_submission(), rubric="Rubric.")
        assert result.needs_review is True
        assert len(result.uncertain_parts) == 1

    @patch("scorer.llm._get_client")
    def test_includes_attachment_in_message(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_response(json.dumps(VALID_LLM_RESPONSE))
        mock_get_client.return_value = mock_client

        att = Attachment(filename="essay.pdf", data=b"%PDF")
        sub = self._make_submission(attachments=[att])
        score_submission(sub, rubric="Rubric.")

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        user_content = messages[1]["content"]

        # Attachment text is merged into a "text" content part
        types = [p["type"] for p in user_content]
        assert "text" in types
        # The combined text part should mention the filename
        texts = [p["text"] for p in user_content if p["type"] == "text"]
        assert any("essay.pdf" in t for t in texts)
