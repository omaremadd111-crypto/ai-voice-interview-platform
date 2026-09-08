"""Interview plan, in-progress turn, and engine transport models."""
from pydantic import BaseModel, Field, field_validator, model_validator

from models.common import InterviewState, QuestionCategory


class InterviewQuestion(BaseModel):
    id: str
    category: QuestionCategory
    question: str
    purpose: str
    expected_topics: list[str] = Field(default_factory=list)
    difficulty: str
    follow_up_allowed: bool = True

    @field_validator("id", "question", "purpose", "difficulty")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


class InterviewPlan(BaseModel):
    questions: list[InterviewQuestion]

    @field_validator("questions")
    @classmethod
    def _valid_question_list(cls, v: list[InterviewQuestion]) -> list[InterviewQuestion]:
        if not v:
            raise ValueError("interview plan must contain at least one question")
        ids = [q.id for q in v]
        if len(ids) != len(set(ids)):
            raise ValueError("interview plan question ids must be unique")
        return v


class InterviewTurn(BaseModel):
    question_id: str
    question: str
    category: QuestionCategory
    answer: str
    is_follow_up: bool
    timestamp: str


class InterviewTranscript(BaseModel):
    turns: list[InterviewTurn] = Field(default_factory=list)

    def turns_for_question(self, question_id: str) -> list[InterviewTurn]:
        return [t for t in self.turns if t.question_id == question_id]


class FollowUpDecision(BaseModel):
    should_ask: bool
    follow_up_question: str | None = None
    reason: str

    @model_validator(mode="after")
    def _question_required_when_asking(self) -> "FollowUpDecision":
        if self.should_ask and not (self.follow_up_question and self.follow_up_question.strip()):
            raise ValueError("follow_up_question is required when should_ask is True")
        return self


class NextPrompt(BaseModel):
    state: InterviewState
    question_id: str | None = None
    question_text: str | None = None
    category: QuestionCategory | None = None
    is_follow_up: bool = False
    question_index: int = 0
    total_questions: int = 0
    progress_pct: float = 0.0
    finished: bool = False

    @field_validator("progress_pct")
    @classmethod
    def _pct_bounds(cls, v: float) -> float:
        if not (0.0 <= v <= 100.0):
            raise ValueError("progress_pct must be within [0, 100]")
        return v
