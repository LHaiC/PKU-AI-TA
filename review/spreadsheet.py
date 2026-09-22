"""
Export ScoringResults to an Excel spreadsheet for human review,
and import the reviewed spreadsheet back into ReviewRecords.

Columns:
  student_id | student_name | total_score | total_max | pct | confidence
  | needs_review | breakdown_json | uncertain_parts_json | llm_reasoning
  | reviewer_override_score | reviewer_notes | approved
"""
from __future__ import annotations

from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill

from config import settings
from models import ReviewRecord, ScoringResult

# Yellow fill for rows that need human review
_REVIEW_FILL = PatternFill(start_color="FFFF99", end_color="FFFF99", fill_type="solid")
_HEADER_FONT = Font(bold=True)

COLUMNS = [
    "student_id",
    "student_name",
    "assignment_id",
    "total_score",
    "total_max",
    "pct",
    "confidence",
    "needs_review",
    "breakdown_json",
    "uncertain_parts_json",
    "llm_reasoning",
    # --- human fills these ---
    "reviewer_override_score",
    "reviewer_notes",
    "approved",
]


def export(results: list[ScoringResult], path: Path) -> None:
    """Write scoring results to an Excel file.

    Preserves reviewer fields (override/notes/approved) from an existing file
    at `path`, keyed by student_id — regrades must not clobber human work.
    If a student's score or breakdown changed, `approved` resets to NO: the
    old approval applied to a different evaluation.
    """
    import json

    prior: dict[str, dict] = {}
    if path.exists():
        try:
            old_ws = openpyxl.load_workbook(path).active
            oidx = {c.value: i for i, c in enumerate(old_ws[1])}
            for row in old_ws.iter_rows(min_row=2, values_only=True):
                if row[oidx["student_id"]]:
                    prior[str(row[oidx["student_id"]])] = {
                        n: row[i] if i < len(row) else None for n, i in oidx.items()
                    }
        except Exception:
            pass

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Scores"

    # Header row
    for col, name in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=col, value=name)
        cell.font = _HEADER_FONT

    # Freeze header
    ws.freeze_panes = "A2"

    for row_idx, r in enumerate(results, start=2):
        breakdown_json = json.dumps([b.model_dump() for b in r.breakdown], ensure_ascii=False)
        override_v, notes_v, approved_v = "", "", "NO"
        old = prior.get(str(r.student_id))
        if old is not None:
            override_v = old.get("reviewer_override_score") or ""
            notes_v = old.get("reviewer_notes") or ""
            changed = (
                float(old.get("total_score") or 0) != r.total_score
                or str(old.get("breakdown_json") or "") != breakdown_json
            )
            if not changed and str(old.get("approved") or "").upper() == "YES":
                approved_v = "YES"
        row_data = [
            r.student_id,
            r.student_name,
            r.assignment_id,
            r.total_score,
            r.total_max,
            r.pct,
            r.confidence,
            "YES" if r.needs_review else "NO",
            breakdown_json,
            json.dumps([u.model_dump() for u in r.uncertain_parts], ensure_ascii=False),
            r.llm_reasoning,
            override_v,   # reviewer_override_score
            notes_v,      # reviewer_notes
            approved_v,   # approved — preserved only if the result is unchanged
        ]
        for col, value in enumerate(row_data, start=1):
            ws.cell(row=row_idx, column=col, value=value)

        # Highlight rows that need review
        if r.needs_review:
            for col in range(1, len(COLUMNS) + 1):
                ws.cell(row=row_idx, column=col).fill = _REVIEW_FILL

    # Auto-width for readability
    for col in ws.columns:
        max_len = max((len(str(c.value or "")) for c in col), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 60)

    wb.save(path)


def load_reviewed(path: Path) -> list[ReviewRecord]:
    """Read a reviewed spreadsheet back into ReviewRecord objects."""
    import json

    from models import CriterionScore, ScoringResult, UncertainPart

    wb = openpyxl.load_workbook(path)
    ws = wb.active

    headers = [cell.value for cell in ws[1]]
    idx = {name: i for i, name in enumerate(headers)}

    records: list[ReviewRecord] = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row[idx["student_id"]]:
            continue

        breakdown = [CriterionScore(**b) for b in json.loads(row[idx["breakdown_json"]] or "[]")]
        uncertain = [UncertainPart(**u) for u in json.loads(row[idx["uncertain_parts_json"]] or "[]")]

        result = ScoringResult(
            student_id=str(row[idx["student_id"]]),
            student_name=str(row[idx["student_name"]]),
            assignment_id=str(row[idx["assignment_id"]]),
            total_score=float(row[idx["total_score"]]),
            total_max=float(row[idx["total_max"]]),
            confidence=float(row[idx["confidence"]]),
            needs_review=row[idx["needs_review"]] == "YES",
            breakdown=breakdown,
            uncertain_parts=uncertain,
            llm_reasoning=str(row[idx["llm_reasoning"]] or ""),
        )
        # Low scores are always review-worthy even if the stored flag says NO
        # (exports predate the review_below rule).
        if result.pct < settings.review_below:
            result.needs_review = True

        override_raw = row[idx["reviewer_override_score"]]
        override = float(override_raw) if override_raw not in (None, "") else None
        approved = str(row[idx["approved"]]).strip().upper() == "YES"
        decay_raw = row[idx["decay_factor"]] if "decay_factor" in idx else None
        decay = float(decay_raw) if decay_raw not in (None, "") else 1.0

        records.append(ReviewRecord(
            result=result,
            reviewer_override_score=override,
            reviewer_notes=str(row[idx["reviewer_notes"]] or ""),
            approved=approved,
            decay_factor=decay,
        ))

    return records
