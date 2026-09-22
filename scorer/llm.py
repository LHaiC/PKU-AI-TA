"""
LLM-based scorer using OpenAI-compatible API (OpenRouter → Qwen).

Scoring pipeline per submission:
  1. Try to extract text from the attachment (pypdf for PDFs, python-docx for Word).
  2. If text extraction yields nothing (scanned/image PDF or image file), convert PDF
     pages to images via pymupdf and send them to the vision model instead.
  3. Ask the model to return a structured JSON scoring result.
  4. Parse the JSON into a ScoringResult.
"""
from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path

from openai import OpenAI

from config import settings
from models import Attachment, CriterionScore, ScoringResult, Submission, UncertainPart

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

_client: OpenAI | None = None

_UNREADABLE_MARKERS = (
    "no extractable text",
    "image file",
    "image-only",
    "image format",
    "manual review required",
    "Could not extract text",
    "Unknown file format",
)


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
        )
    return _client


_DEFAULT_PROMPT = _PROMPTS_DIR / "system_en.md"


def get_system_prompt(path: Path) -> str:
    """Load the system prompt from a file path."""
    if path.exists():
        return path.read_text(encoding="utf-8")
    raise FileNotFoundError(
        f"System prompt file not found: {path}\n"
        f"Built-in prompts are in {_PROMPTS_DIR}/: system_en.md, system_zh.md"
    )


# ---------------------------------------------------------------------------
# Attachment handling
# ---------------------------------------------------------------------------

def _extract_text(attachment: Attachment) -> str:
    """Extract text from a PDF, Word doc, or plain-text file."""
    data = attachment.data
    fname = attachment.filename.lower()

    if data[:4] == b'%PDF' or fname.endswith('.pdf'):
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n\n".join(pages).strip()
            return text if text else "(PDF has no extractable text — may be scanned/image-only)"
        except Exception as e:
            return f"(Could not extract text from PDF: {e})"

    if data[:2] == b'PK' or fname.endswith(('.docx', '.doc')):
        try:
            from docx import Document
            doc = Document(io.BytesIO(data))
            text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            return text if text else "(Word document has no extractable text)"
        except Exception as e:
            return f"(Could not extract text from Word document: {e})"

    if data[:3] == b'\xff\xd8\xff' or fname.endswith(('.jpg', '.jpeg', '.png')):
        return "(Submission is an image file — text extraction not supported)"

    try:
        return data.decode("utf-8", errors="replace")
    except Exception:
        return f"(Unknown file format for {attachment.filename})"


def _pdf_has_embedded_images(data: bytes) -> bool:
    """Check if a PDF has embedded images (indicating it's a scanned PDF)."""
    try:
        import fitz  # pymupdf
        doc = fitz.open(stream=data, filetype="pdf")
        for page in doc:
            if page.get_images(full=True):
                return True
        return False
    except Exception:
        return False


def _needs_vision(text: str, attachment: Attachment | None = None) -> bool:
    if any(marker in text for marker in _UNREADABLE_MARKERS):
        return True

    # If PDF has embedded images, use vision mode (avoids OCR text layer issues)
    if attachment and attachment.filename.lower().endswith('.pdf'):
        if _pdf_has_embedded_images(attachment.data):
            return True

    # Very short extracted text almost certainly means watermarks only, not real content
    return len(text.strip()) < 200


def _pdf_to_image_parts(data: bytes) -> list[dict]:
    """Render PDF pages to images for vision model.

    Always renders pages directly rather than trying to extract embedded images,
    because PDFs often contain non-content images (ICC profiles, thumbnails, etc.)
    that aren't student work. Rendering ensures we see exactly what a human sees.
    """
    import fitz  # pymupdf
    doc = fitz.open(stream=data, filetype="pdf")
    parts = []
    for page in doc:
        mat = fitz.Matrix(2.0, 2.0)
        pix = page.get_pixmap(matrix=mat)
        b64 = base64.b64encode(pix.tobytes("png")).decode()
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"},
        })
    return parts


def _attachment_content_parts(attachment: Attachment) -> list[dict]:
    """
    Return content parts for one attachment.

    Tries text extraction first. For scanned/image files falls back to sending
    the raw image bytes as image_url so the model can read them visually.
    """
    text = _extract_text(attachment)
    if not _needs_vision(text, attachment):
        return [{"type": "text", "text": f"**File: {attachment.filename}**\n\n{text}"}]

    data = attachment.data
    fname = attachment.filename.lower()

    # JPEG submitted directly
    if data[:3] == b'\xff\xd8\xff' or fname.endswith(('.jpg', '.jpeg')):
        b64 = base64.b64encode(data).decode()
        return [
            {"type": "text", "text": f"**File: {attachment.filename}** (image)"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
        ]

    # PNG submitted directly
    if data[:8] == b'\x89PNG\r\n\x1a\n' or fname.endswith('.png'):
        b64 = base64.b64encode(data).decode()
        return [
            {"type": "text", "text": f"**File: {attachment.filename}** (image)"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"}},
        ]

    # Scanned PDF — render pages to images
    if data[:4] == b'%PDF' or fname.endswith('.pdf'):
        try:
            img_parts = _pdf_to_image_parts(data)
            if img_parts:
                return (
                    [{"type": "text", "text": f"**File: {attachment.filename}** (scanned — pages rendered)"}]
                    + img_parts
                )
        except Exception:
            pass

    # Unrenderable — pass the error text so the model knows
    return [{"type": "text", "text": f"**File: {attachment.filename}**\n\n{text}"}]


# ---------------------------------------------------------------------------
# Main scoring function
# ---------------------------------------------------------------------------

def score_submission(submission: Submission, rubric: str, prompt: Path = _DEFAULT_PROMPT) -> ScoringResult:
    """Score a single submission against the rubric using the LLM.

    Args:
        submission: The student submission to score.
        rubric: The rubric text.
        prompt: Path to the system prompt file. Defaults to prompts/system_en.md.
    """
    system_prompt = get_system_prompt(prompt)

    content: list[dict] = [
        {"type": "text", "text": f"## Scoring Rubric\n\n{rubric}\n\n## Student Submission"},
    ]

    if submission.text_content.strip():
        content.append({"type": "text", "text": f"**Text answer:**\n{submission.text_content}"})

    for att in submission.attachments:
        content.extend(_attachment_content_parts(att))

    extra: dict = {}
    if not settings.enable_thinking:
        # Only send enable_thinking for Qwen models on platforms that support it
        # Skip for Volcengine/other providers that don't support this parameter
        model_name = settings.ta_model.lower()
        if "qwen" in model_name and ("openrouter" in settings.openai_base_url or "dashscope" in settings.openai_base_url):
            extra["extra_body"] = {"enable_thinking": False}

    response = _get_client().chat.completions.create(
        model=settings.ta_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        temperature=0.2,
        **extra,
    )

    raw = response.choices[0].message.content or ""
    data = _parse_json(raw)

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


# ---------------------------------------------------------------------------
# JSON parsing helpers
# ---------------------------------------------------------------------------

def _sanitize_json(s: str) -> str:
    """Escape stray backslashes (e.g. LaTeX \\frac) that make JSON invalid."""
    return re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', s)


def _parse_json(raw: str) -> dict:
    """Extract JSON from model output, tolerating minor formatting issues."""
    cleaned = re.sub(r"```(?:json)?\s*|```", "", raw).strip()
    for candidate in [cleaned, _sanitize_json(cleaned)]:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        block = match.group()
        for candidate in [block, _sanitize_json(block)]:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
    raise ValueError(f"Could not parse LLM response as JSON:\n{raw[:500]}")
