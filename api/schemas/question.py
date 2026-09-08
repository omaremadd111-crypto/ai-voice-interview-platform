"""Position question request/response schemas. category is always required --
never inferred or defaulted (see PositionQuestionInput in application/dto.py)."""
from pydantic import BaseModel, ConfigDict, Field, field_validator

from models.common import POSITION_BASELINE_CATEGORIES, QuestionCategory


def _position_category(category: QuestionCategory) -> QuestionCategory:
    if category not in POSITION_BASELINE_CATEGORIES:
        raise ValueError("category is not valid for reusable Position Question Bank questions")
    return category


class QuestionCreateRequest(BaseModel):
    category: QuestionCategory
    question: str = Field(min_length=1)
    order: int = Field(ge=0)
    purpose: str | None = None
    expected_topics: list[str] = Field(default_factory=list)
    difficulty: str = "medium"
    follow_up_allowed: bool = True

    _validate_category = field_validator("category")(_position_category)


class QuestionUpdateRequest(BaseModel):
    category: QuestionCategory | None = None
    question: str | None = Field(default=None, min_length=1)
    order: int | None = Field(default=None, ge=0)
    purpose: str | None = None
    expected_topics: list[str] | None = None
    difficulty: str | None = None
    follow_up_allowed: bool | None = None

    @field_validator("category")
    @classmethod
    def _validate_category(cls, value: QuestionCategory | None) -> QuestionCategory | None:
        return _position_category(value) if value is not None else None


class QuestionResponse(BaseModel):
    id: int
    position_id: int
    category: QuestionCategory
    question: str
    order: int
    purpose: str | None
    expected_topics: list[str]
    difficulty: str
    follow_up_allowed: bool


class ReorderQuestionsRequest(BaseModel):
    """Ordered list of this position's question ids; position in the list becomes
    the new order. Must contain exactly the position's existing question ids."""

    question_ids: list[int] = Field(min_length=1)


class SuggestQuestionsRequest(BaseModel):
    """Ask the AI for reusable role questions using Position/JD context only.

    Unknown fields are rejected so candidate_id, CV text, candidate claims, or
    any future candidate-specific field cannot be silently ignored at this
    server-side boundary.
    """

    model_config = ConfigDict(extra="forbid")

    num_questions: int = Field(default=6, ge=3, le=15)


class SuggestedQuestionResponse(BaseModel):
    """A proposed question. NOT persisted -- HR reviews and saves what it wants,
    which is what keeps the recruiter in control of the final question set."""

    suggestion_id: str
    category: QuestionCategory
    question: str
    purpose: str | None
    expected_topics: list[str]
    difficulty: str
    follow_up_allowed: bool
