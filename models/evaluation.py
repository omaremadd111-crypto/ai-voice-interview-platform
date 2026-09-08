"""Evaluation and HR report models."""
from pydantic import BaseModel, Field, field_validator, model_validator

from models.common import EvaluationCategory, RecommendationLevel, ScreeningOutcome
from models.interview import InterviewTurn


class CategoryEvaluation(BaseModel):
    category: EvaluationCategory
    score: int | None = None
    sufficient_evidence: bool
    reasoning: str
    evidence: list[str] = Field(
        default_factory=list,
        description=(
            "Short verbatim excerpts copied exactly from candidate answers in the "
            "provided transcript. Never paraphrase, combine, or invent an excerpt."
        ),
    )
    areas_to_validate: list[str] = Field(default_factory=list)

    @field_validator("score")
    @classmethod
    def _score_bounds(cls, v: int | None) -> int | None:
        if v is not None and not (0 <= v <= 100):
            raise ValueError("score must be within 0..100")
        return v

    @model_validator(mode="after")
    def _evidence_consistency(self) -> "CategoryEvaluation":
        if self.sufficient_evidence and self.score is None:
            raise ValueError("sufficient_evidence=True requires a non-null score")
        if not self.sufficient_evidence and self.score is not None:
            raise ValueError("score must be null when sufficient_evidence is False")
        if self.sufficient_evidence and len(self.evidence) == 0:
            raise ValueError("sufficient_evidence=True requires at least one evidence item")
        if not self.sufficient_evidence:
            self.reasoning = "Insufficient evidence"
        return self


class InterviewEvaluation(BaseModel):
    category_evaluations: list[CategoryEvaluation]
    overall_score: int | None
    evidence_coverage: float
    recommendation: RecommendationLevel
    screening_outcome: ScreeningOutcome
    rubric_profile: str

    @field_validator("overall_score")
    @classmethod
    def _overall_bounds(cls, v: int | None) -> int | None:
        if v is not None and not (0 <= v <= 100):
            raise ValueError("overall_score must be within 0..100")
        return v

    @field_validator("evidence_coverage")
    @classmethod
    def _coverage_bounds(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("evidence_coverage must be within 0..1")
        return v


class HRReport(BaseModel):
    session_id: str
    company: str
    role: str
    candidate_name: str | None = None
    candidate_overview: str
    interview_summary: str
    overall_score: int | None
    evidence_coverage: float
    category_scores: list[CategoryEvaluation]
    strong_evidence: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    areas_requiring_validation: list[str] = Field(default_factory=list)
    important_candidate_answers: list[str] = Field(default_factory=list)
    human_follow_up_questions: list[str] = Field(default_factory=list)
    full_transcript: list[InterviewTurn] = Field(default_factory=list)
    recommendation: RecommendationLevel
    screening_outcome: ScreeningOutcome
    llm_provider: str
    is_mock: bool
    generated_at: str

    @field_validator("overall_score")
    @classmethod
    def _overall_bounds(cls, v: int | None) -> int | None:
        if v is not None and not (0 <= v <= 100):
            raise ValueError("overall_score must be within 0..100")
        return v

    @field_validator("evidence_coverage")
    @classmethod
    def _coverage_bounds(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("evidence_coverage must be within 0..1")
        return v
