"""Grade submissions with an agentic CLI (devin/claude/codex) instead of an HTTP LLM API.

For each student we spawn one non-interactive CLI call (e.g. `devin -p`). The
prompt file points at the submission files already saved on disk by
`_save_submissions`; the agent reads them with its own file tools (PDF, images,
logs all supported) and must print a single ScoringResult-shaped JSON object on
stdout, which we parse exactly like the API response in scorer.llm.
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from config import settings
from models import CriterionScore, ScoringResult, Submission, UncertainPart
from scorer.llm import _parse_json, get_system_prompt

_TIMEOUT_S = 900  # per-student CLI call

_ENGINES = ("devin", "claude", "codex", "opencode", "cmdc")


def cli_engines_on_path() -> dict[str, str | None]:
    """Which agentic CLIs are installed — engine name -> binary path or None."""
    from shutil import which
    return {name: which(name) for name in _ENGINES}


def _engine_argv(engine: str, prompt_file: Path, prompt_text: str) -> list[str]:
    """Build the argv for one grading call. A custom TA_GRADER_CMD template
    ({prompt_file} placeholder) overrides all presets.

    TA_CLI_MODEL and TA_CLI_EFFORT are mapped onto each CLI's own flags:
    devin --model (effort unsupported), claude --model/--effort,
    codex -m/-c model_reasoning_effort, opencode -m/--variant,
    cmdc -m/--effort.
    """
    if settings.grader_cmd:
        cmd = settings.grader_cmd.format(prompt_file=shlex.quote(str(prompt_file)))
        return ["bash", "-c", cmd]
    model = settings.ta_cli_model
    effort = settings.ta_cli_effort
    if engine == "devin":
        # --model must precede -p: -p takes an optional inline prompt and would
        # otherwise swallow trailing positionals.
        return [
            "devin", "--model", model or "swe-2-medium",
            "-p", "--prompt-file", str(prompt_file),
            "--permission-mode", "accept-edits",
            "--respect-workspace-trust", "false",
        ]
    if engine == "claude":
        argv = ["claude", "-p", prompt_text, "--allowedTools", "Read Glob Grep"]
        if model:
            argv += ["--model", model]
        if effort:
            argv += ["--effort", effort]
        return argv
    if engine == "codex":
        argv = ["codex", "exec", "--sandbox", "read-only"]
        if model:
            argv += ["-m", model]
        if effort:
            argv += ["-c", f"model_reasoning_effort={effort}"]
        argv.append(prompt_text)
        return argv
    if engine == "opencode":
        argv = ["opencode", "run"]
        if model:
            argv += ["-m", model]
        if effort:
            argv += ["--variant", effort]
        argv.append(prompt_text)
        return argv
    if engine == "cmdc":
        argv = ["cmdc"]
        if model:
            argv += ["-m", model]
        if effort:
            argv += ["--effort", effort]
        argv += ["-p", prompt_text]
        return argv
    raise ValueError(
        f"Unknown scoring engine: {engine!r} "
        f"(expected one of: api, {', '.join(_ENGINES)}; or set TA_GRADER_CMD)"
    )


def _build_prompt(submission: Submission, rubric: str, system_prompt: str,
                  files: list[Path]) -> str:
    parts = [
        system_prompt,
        "",
        "---",
        "",
        "# 本次评分任务 / Grading task",
        "",
        "## 评分标准（Rubric）",
        "",
        rubric,
        "",
        "## 学生提交 / Submission",
        "",
        f"- 学号 Student ID: {submission.student_id}",
        f"- 姓名 Name: {submission.student_name}",
    ]
    if files:
        parts += [
            "",
            "### 提交文件（用你的文件读取工具逐个打开阅读，含 PDF/图片/日志）",
            "### Submission files (open each with your file-reading tools)",
        ]
        parts += [f"- {p}" for p in files]
    if submission.text_content.strip():
        parts += ["", "### 文本回答 / Text answer", "", submission.text_content]
    parts += [
        "",
        "## 要求 / Instructions",
        "",
        "1. 先用文件读取工具读完上面列出的每个提交文件，再开始评分。",
        "   压缩包（zip/7z/tar.gz）已提前解压成普通文件并全部列在上方，无需自己解压。",
        "   PDF/DOCX 的正文在同名 .txt 纯文本副本中（你的读取工具打不开二进制原文件）；",
        "   PDF 还按页渲染成了 `<文件名>_pages/page-NN.png`，DOCX 的内嵌图片在 `_media/` 目录——",
        "   截图、版图、波形等图像证据请打开这些 PNG 用图像能力核实后再给分。",
        "   Open PDFs via their .txt sidecar AND the rendered page-NN.png images; verify figures visually.",
        "2. 绝对不要运行 shell 命令——本会话已禁用 shell，任何 shell 调用都会被拒绝并中断评分。",
        "   Do NOT run shell commands — they are disabled and will abort this grading session.",
        "3. 只输出一个 JSON 对象作为最终回答：不要 markdown 代码块，不要任何其他文字。",
        "   Reply with ONLY the JSON object — no markdown fences, no extra text.",
    ]
    return "\n".join(parts)


def _to_result(submission: Submission, data: dict) -> ScoringResult:
    """Same JSON -> ScoringResult mapping as scorer.llm.score_submission."""

    def _f(val, default: float = 0.0) -> float:
        return float(val) if val is not None else default

    uncertain = [
        UncertainPart(
            description=u.get("description") or u.get("criterion") or u.get("part") or str(u),
            suggested_score=_f(u.get("suggested_score")),
            suggested_max=_f(u.get("suggested_max")),
        )
        for u in data.get("uncertain_parts", [])
    ]
    breakdown = [
        CriterionScore(
            criterion=b.get("criterion", ""),
            points_awarded=_f(b.get("points_awarded")),
            points_max=_f(b.get("points_max")),
            reasoning=b.get("reasoning", ""),
        )
        for b in data.get("breakdown", [])
    ]
    confidence = _f(data.get("confidence"), 1.0)
    total = _f(data.get("total_score"))
    total_max = _f(data.get("total_max"))
    pct = total / total_max * 100 if total_max else 0.0
    return ScoringResult(
        student_id=submission.student_id,
        student_name=submission.student_name,
        assignment_id=submission.assignment_id,
        total_score=total,
        total_max=total_max,
        breakdown=breakdown,
        uncertain_parts=uncertain,
        confidence=confidence,
        llm_reasoning=data.get("llm_reasoning", ""),
        needs_review=(
            confidence < settings.review_threshold
            or len(uncertain) > 0
            or pct < settings.review_below
        ),
    )


def make_cli_scorer(
    engine: str,
    rubric_text: str,
    prompt_path: Path,
    files_map: dict[str, list[Path]],
    prompts_dir: Path,
):
    """Return a score_fn(submission) -> ScoringResult that grades via a CLI.

    files_map maps student_id -> saved submission files (from _save_submissions).
    Per-student prompts are kept under prompts_dir for debugging.
    """
    system_prompt = get_system_prompt(prompt_path)
    prompts_dir.mkdir(parents=True, exist_ok=True)

    def score(sub: Submission) -> ScoringResult:
        files = files_map.get(sub.student_id, [])
        prompt_text = _build_prompt(sub, rubric_text, system_prompt, files)
        prompt_file = prompts_dir / f"{sub.student_id}.md"
        prompt_file.write_text(prompt_text, encoding="utf-8")

        argv = _engine_argv(engine, prompt_file, prompt_text)
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"{engine} timed out after {_TIMEOUT_S}s")
        except FileNotFoundError:
            raise RuntimeError(f"{engine} CLI not found on PATH")
        if proc.returncode != 0:
            raise RuntimeError(
                f"{engine} exited {proc.returncode}: {(proc.stderr or proc.stdout)[-500:]}"
            )
        return _to_result(sub, _parse_json(proc.stdout))

    return score
