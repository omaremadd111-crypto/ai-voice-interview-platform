"""Recruiter-facing projection of the persisted HR report."""

from pydantic import BaseModel

from models.common import RecommendationLevel, ScreeningOutcome
from models.evaluation import CategoryEvaluation, HRReport
from models.interview import InterviewTurn


class ScreeningResultResponse(BaseModel):
    session_id: str
    company: str
    role: str
    candidate_name: str | None
    overall_score: int | None
    evidence_coverage: float
    screening_outcome: ScreeningOutcome
    recommendation: RecommendationLevel
    category_scores: list[CategoryEvaluation]
    strengths: list[str]
    areas_to_validate: list[str]
    ai_summary: str
    human_follow_up_questions: list[str]
    full_transcript: list[InterviewTurn]
    llm_provider: str
    is_mock: bool
    generated_at: str

    @classmethod
    def from_report(cls, report: HRReport) -> "ScreeningResultResponse":
        return cls(
            session_id=report.session_id,
            company=report.company,
            role=report.role,
            candidate_name=report.candidate_name,
            overall_score=report.overall_score,
            evidence_coverage=report.evidence_coverage,
            screening_outcome=report.screening_outcome,
            recommendation=report.recommendation,
            category_scores=report.category_scores,
            strengths=report.strengths,
            areas_to_validate=report.areas_requiring_validation,
            ai_summary=report.interview_summary,
            human_follow_up_questions=report.human_follow_up_questions,
            full_transcript=report.full_transcript,
            llm_provider=report.llm_provider,
            is_mock=report.is_mock,
            generated_at=report.generated_at,
        )
