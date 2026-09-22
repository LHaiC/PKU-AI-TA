import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from models import Attachment, Submission
from scorer.cli_scorer import _build_prompt, _engine_argv, make_cli_scorer

VALID_RESPONSE = {
    "total_score": 90.0,
    "total_max": 100.0,
    "confidence": 0.9,
    "breakdown": [
        {"criterion": "Q1", "points_awarded": 10.0, "points_max": 10.0, "reasoning": "OK"},
        {"criterion": "Q2", "points_awarded": 80.0, "points_max": 90.0, "reasoning": "扣10分：缺 DRC"},
    ],
    "uncertain_parts": [],
    "llm_reasoning": "Good.",
}


def _submission() -> Submission:
    return Submission(
        student_id="2100012345",
        student_name="张三",
        assignment_id="423829",
        text_content="Q1: synthesis, floorplan, ...",
        attachments=[Attachment(filename="report.pdf", data=b"%PDF-fake")],
    )


class TestBuildPrompt:
    def test_includes_rubric_files_and_text(self, tmp_path):
        files = [tmp_path / "2100012345_张三.pdf", tmp_path / "2100012345_张三_text.txt"]
        prompt = _build_prompt(_submission(), "RUBRIC TEXT", "SYS PROMPT", files)
        assert "SYS PROMPT" in prompt
        assert "RUBRIC TEXT" in prompt
        assert "2100012345" in prompt and "张三" in prompt
        assert "2100012345_张三.pdf" in prompt
        assert "synthesis, floorplan" in prompt
        assert "JSON" in prompt

    def test_omits_empty_sections(self):
        sub = _submission()
        sub.text_content = ""
        prompt = _build_prompt(sub, "R", "S", [])
        assert "### 文本回答" not in prompt
        assert "### 提交文件" not in prompt


class TestEngineArgv:
    def test_devin_uses_prompt_file(self, monkeypatch):
        monkeypatch.setattr("scorer.cli_scorer.settings.grader_cmd", "")
        monkeypatch.setattr("scorer.cli_scorer.settings.ta_cli_model", "swe-2-medium")
        argv = _engine_argv("devin", Path("/tmp/p.md"), "prompt text")
        # --model must come before -p: -p's optional arg swallows positionals
        assert argv[:4] == ["devin", "--model", "swe-2-medium", "-p"]
        assert "--prompt-file" in argv
        assert "/tmp/p.md" in argv

    def test_claude_passes_prompt_inline(self, monkeypatch):
        monkeypatch.setattr("scorer.cli_scorer.settings.grader_cmd", "")
        argv = _engine_argv("claude", Path("/tmp/p.md"), "prompt text")
        assert "prompt text" in argv

    def test_model_and_effort_flags(self, monkeypatch):
        monkeypatch.setattr("scorer.cli_scorer.settings.grader_cmd", "")
        monkeypatch.setattr("scorer.cli_scorer.settings.ta_cli_model", "m1")
        monkeypatch.setattr("scorer.cli_scorer.settings.ta_cli_effort", "high")
        try:
            assert "--model" in _engine_argv("claude", Path("/tmp/p.md"), "x")
            assert "high" in _engine_argv("claude", Path("/tmp/p.md"), "x")
            codex = _engine_argv("codex", Path("/tmp/p.md"), "x")
            assert "-m" in codex and "model_reasoning_effort=high" in codex
            oc = _engine_argv("opencode", Path("/tmp/p.md"), "x")
            assert oc[0] == "opencode" and "--variant" in oc
            cmdc = _engine_argv("cmdc", Path("/tmp/p.md"), "x")
            assert cmdc[0] == "cmdc" and "-p" in cmdc and "--effort" in cmdc
        finally:
            monkeypatch.setattr("scorer.cli_scorer.settings.ta_cli_model", "")
            monkeypatch.setattr("scorer.cli_scorer.settings.ta_cli_effort", "")

    def test_unknown_engine_raises(self, monkeypatch):
        monkeypatch.setattr("scorer.cli_scorer.settings.grader_cmd", "")
        with pytest.raises(ValueError):
            _engine_argv("nonexistent", Path("/tmp/p.md"), "x")

    def test_grader_cmd_override(self, monkeypatch):
        monkeypatch.setattr(
            "scorer.cli_scorer.settings.grader_cmd",
            "mygrader --file {prompt_file} --fast",
        )
        argv = _engine_argv("devin", Path("/tmp/p.md"), "x")
        assert argv[:2] == ["bash", "-c"]
        assert "mygrader --file /tmp/p.md --fast" in argv[2]


class TestCliScorer:
    def _mock_proc(self, stdout: str, returncode: int = 0):
        proc = MagicMock()
        proc.stdout = stdout
        proc.stderr = ""
        proc.returncode = returncode
        return proc

    def test_parses_stdout_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scorer.cli_scorer.settings.grader_cmd", "")
        monkeypatch.setattr(
            "scorer.cli_scorer.settings.review_threshold", 0.75,
        )
        with patch("scorer.cli_scorer.subprocess.run") as mock_run:
            mock_run.return_value = self._mock_proc(json.dumps(VALID_RESPONSE))
            scorer = make_cli_scorer(
                "devin", "RUBRIC", Path("prompts/system_zh.md"),
                {"2100012345": [tmp_path / "f.pdf"]}, tmp_path / "prompts",
            )
            result = scorer(_submission())

        assert result.total_score == 90.0
        assert result.student_id == "2100012345"
        assert len(result.breakdown) == 2
        assert result.needs_review is False
        # prompt file persisted for debugging
        assert (tmp_path / "prompts" / "2100012345.md").exists()

    def test_flags_low_confidence(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scorer.cli_scorer.settings.grader_cmd", "")
        resp = {**VALID_RESPONSE, "confidence": 0.3}
        with patch("scorer.cli_scorer.subprocess.run") as mock_run:
            mock_run.return_value = self._mock_proc(json.dumps(resp))
            scorer = make_cli_scorer(
                "devin", "R", Path("prompts/system_zh.md"), {}, tmp_path,
            )
            assert scorer(_submission()).needs_review is True

    def test_flags_low_score_for_review(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scorer.cli_scorer.settings.grader_cmd", "")
        monkeypatch.setattr("scorer.cli_scorer.settings.review_below", 90.0)
        resp = {**VALID_RESPONSE, "total_score": 40.0, "confidence": 1.0}
        with patch("scorer.cli_scorer.subprocess.run") as mock_run:
            mock_run.return_value = self._mock_proc(json.dumps(resp))
            scorer = make_cli_scorer(
                "devin", "R", Path("prompts/system_zh.md"), {}, tmp_path,
            )
            assert scorer(_submission()).needs_review is True

    def test_nonzero_exit_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scorer.cli_scorer.settings.grader_cmd", "")
        with patch("scorer.cli_scorer.subprocess.run") as mock_run:
            mock_run.return_value = self._mock_proc("boom", returncode=2)
            mock_run.return_value.stderr = "engine exploded"
            scorer = make_cli_scorer(
                "devin", "R", Path("prompts/system_zh.md"), {}, tmp_path,
            )
            with pytest.raises(RuntimeError, match="exited 2"):
                scorer(_submission())

    def test_timeout_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scorer.cli_scorer.settings.grader_cmd", "")
        with patch("scorer.cli_scorer.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="devin", timeout=1)
            scorer = make_cli_scorer(
                "devin", "R", Path("prompts/system_zh.md"), {}, tmp_path,
            )
            with pytest.raises(RuntimeError, match="timed out"):
                scorer(_submission())
