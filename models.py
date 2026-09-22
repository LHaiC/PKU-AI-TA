from __future__ import annotations

from pydantic import BaseModel, Field


class Attachment(BaseModel):
    filename: str
    data: bytes  # raw bytes


class Submission(BaseModel):
    student_id: str           # real student number, e.g. "2000012515"
    student_name: str
    assignment_id: str        # gradeBookPK (numeric string) or column ID
    text_content: str = ""
    attachments: list[Attachment] = Field(default_factory=list)
    already_graded: bool = False  # True if the newest attempt is already graded on PKU website


class CriterionScore(BaseModel):
    criterion: str
    points_awarded: float
    points_max: float
    reasoning: str


class UncertainPart(BaseModel):
    description: str
    suggested_score: float
    suggested_max: float


class ScoringResult(BaseModel):
    student_id: str
    student_name: str
    assignment_id: str
    total_score: float
    total_max: float
    breakdown: list[CriterionScore] = Field(default_factory=list)
    uncertain_parts: list[UncertainPart] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    llm_reasoning: str = ""
    needs_review: bool = False  # set True when confidence < threshold or uncertain_parts exist

    @property
    def pct(self) -> float:
        return round(self.total_score / self.total_max * 100, 1) if self.total_max else 0.0

    def deduction_summary(self, max_chars: int = 1800) -> str:
        """Student-facing feedback generated from the breakdown: total plus one
        line per deducted criterion. Used to auto-fill reviewer notes, which are
        posted to the platform as richContent (visible to the student)."""
        lines = []
        for b in self.breakdown:
            lost = b.points_max - b.points_awarded
            if lost > 0:
                lines.append(f"- {b.criterion}（{b.points_awarded:g}/{b.points_max:g}）：{b.reasoning}")
        head = f"得分 {self.total_score:g}/{self.total_max:g}。"
        text = head + ("\n扣分项：\n" + "\n".join(lines) if lines else "")
        return text[:max_chars]


class ReviewRecord(BaseModel):
    result: ScoringResult
    reviewer_override_score: float | None = None  # None = accept LLM score
    reviewer_notes: str = ""
    approved: bool = False
    decay_factor: float = 1.0  # late-submission multiplier; 1.0 = on time

    @property
    def base_score(self) -> float:
        """Pre-decay score: a human override wins over the LLM score."""
        if self.reviewer_override_score is not None:
            return self.reviewer_override_score
        return self.result.total_score

    @property
    def final_score(self) -> float:
        return self.base_score * self.decay_factor
