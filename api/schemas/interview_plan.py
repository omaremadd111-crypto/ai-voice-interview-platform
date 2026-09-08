"""Candidate interview plan request/response schemas."""
from datetime import datetime

from pydantic import BaseModel, Field

from models.common import InterviewPlanStatus, PlanQuestionSource, QuestionCategory


class PlanQuestionPayload(BaseModel):
    """A question as HR submits it when saving the plan. No id: the whole list is
    saved as a unit, and its position in the list becomes its order."""

    category: QuestionCategory
    question: str = Field(min_length=1)
    purpose: str | None = None
    expected_topics: list[str] = Field(default_factory=list)
    difficulty: str = "medium"
    follow_up_allowed: bool = True
    source: PlanQuestionSource = PlanQuestionSource.MANUAL


class PlanQuestionResponse(PlanQuestionPayload):
    order: int


class InterviewPlanResponse(BaseModel):
    candidate_id: int
    status: InterviewPlanStatus
    questions: list[PlanQuestionResponse]
    generated_at: datetime | None
    approved_at: datetime | None


class GeneratePlanRequest(BaseModel):
    num_questions: int = Field(default=8, ge=3, le=15)


class SavePlanRequest(BaseModel):
    """The complete ordered question list. Covers edit, add, delete and reorder in
    a single atomic save."""

    questions: list[PlanQuestionPayload] = Field(min_length=1)
