"""Transport-neutral request/result/status DTOs for InterviewAgentService."""
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from models.candidate import CandidateAnalysis, FitAnalysis
from models.common import ExperienceLevel, InterviewState, QuestionCategory
from models.interview import InterviewPlan
from models.job import JobAnalysis


class PrepareInterviewRequest(BaseModel):
    """Inputs accepted from a future UI, API, or other transport adapter.

    Each document accepts exactly one source: normalized user text, a filesystem path,
    or bytes plus a filename. No Gradio or web-framework types cross this boundary.
    """

    model_config = ConfigDict(frozen=True)

    company_name: str = "FlairsTech"
    job_title: str
    experience_level: ExperienceLevel | None = None

    job_description_text: str | None = None
    job_description_path: Path | None = None
    job_description_bytes: bytes | None = None
    job_description_filename: str | None = None

    candidate_name: str | None = None
    candidate_cv_text: str | None = None
    candidate_cv_path: Path | None = None
    candidate_cv_bytes: bytes | None = None
    candidate_cv_filename: str | None = None

    num_questions: int = Field(default=6, ge=3, le=15)
    approximate_duration_minutes: int | None = Field(default=None, gt=0)

    @field_validator("company_name", "job_title")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("job_description_text", "candidate_cv_text")
    @classmethod
    def _optional_document_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("document text must not be blank")
        return value

    @field_validator("candidate_name", "job_description_filename", "candidate_cv_filename")
    @classmethod
    def _optional_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def _document_sources_are_unambiguous(self) -> "PrepareInterviewRequest":
        _validate_document_source(
            "job description",
            self.job_description_text,
            self.job_description_path,
            self.job_description_bytes,
            self.job_description_filename,
        )
        _validate_document_source(
            "candidate CV",
            self.candidate_cv_text,
            self.candidate_cv_path,
            self.candidate_cv_bytes,
            self.candidate_cv_filename,
        )
        return self


class PositionQuestionInput(BaseModel):
    """One HR-authored interview question for a position.

    category is REQUIRED and never inferred or defaulted: the Evaluator maps
    QuestionCategory to EvaluationCategory, so a wrong or missing category would
    misdirect -- or silently drop -- that question's evidence during evaluation.
    Future UI convenience (an AI-suggested category) must still require explicit
    HR confirmation before a question can be saved; it must never bypass this field.
    """

    model_config = ConfigDict(frozen=True)

    category: QuestionCategory
    question: str
    purpose: str | None = None
    expected_topics: list[str] = Field(default_factory=list)
    difficulty: str = "medium"
    follow_up_allowed: bool = True

    @field_validator("question", "difficulty")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("purpose")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class PrepareFromPositionRequest(BaseModel):
    """Position-driven interview preparation.

    HR supplies the question set directly instead of asking the AI planner to
    generate one, so no job description or interview-planning LLM call is required
    to build the plan. The candidate's CV is genuinely optional: a candidate can be
    added to a position with just a name and no document, matching a queue/dashboard
    workflow where CVs are not always available up front.
    """

    model_config = ConfigDict(frozen=True)

    company_name: str = "FlairsTech"
    position_id: int | None = None
    job_title: str
    experience_level: ExperienceLevel | None = None
    job_description_text: str | None = None

    candidate_name: str
    candidate_id: int | None = None
    candidate_cv_text: str | None = None

    questions: list[PositionQuestionInput]

    @field_validator("company_name", "job_title", "candidate_name")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("job_description_text", "candidate_cv_text")
    @classmethod
    def _optional_document_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("document text must not be blank")
        return value

    @field_validator("questions")
    @classmethod
    def _at_least_one_question(cls, value: list[PositionQuestionInput]) -> list[PositionQuestionInput]:
        if not value:
            raise ValueError("at least one position question is required")
        return value


class PrepareInterviewResult(BaseModel):
    session_id: str
    state: InterviewState
    job_analysis: JobAnalysis
    candidate_analysis: CandidateAnalysis
    fit_analysis: FitAnalysis
    interview_plan: InterviewPlan
    llm_provider: str
    is_mock: bool


class SessionStatus(BaseModel):
    session_id: str
    company: str
    role: str
    state: InterviewState
    current_question_index: int
    total_questions: int
    answered_main_questions: int
    total_turns: int
    total_follow_ups: int
    has_evaluation: bool
    has_report: bool
    llm_provider: str
    is_mock: bool


def _validate_document_source(
    label: str,
    text: str | None,
    path: Path | None,
    data: bytes | None,
    filename: str | None,
) -> None:
    source_count = sum(source is not None for source in (text, path, data))
    if source_count != 1:
        raise ValueError(f"{label} requires exactly one of text, path, or bytes")
    if data is not None and filename is None:
        raise ValueError(f"{label} bytes require a filename")
    if path is not None and filename is not None:
        raise ValueError(f"{label} filename must not be provided with a path")
