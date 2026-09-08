"""Transport-neutral DTO validation for Phase 7 orchestration."""
from pathlib import Path

import pytest
from pydantic import ValidationError

from application.dto import PositionQuestionInput, PrepareFromPositionRequest, PrepareInterviewRequest
from models.common import QuestionCategory


def test_request_accepts_text_sources_without_transport_types() -> None:
    request = PrepareInterviewRequest(
        company_name="Acme",
        job_title="Engineer",
        job_description_text="Build Python systems.",
        candidate_cv_text="Built Python systems.",
    )
    assert request.job_description_path is None
    assert request.candidate_cv_path is None
    assert request.num_questions == 6


def test_request_accepts_path_sources() -> None:
    request = PrepareInterviewRequest(
        company_name="Acme",
        job_title="Engineer",
        job_description_path=Path("job.md"),
        candidate_cv_path=Path("cv.txt"),
    )
    assert request.job_description_path == Path("job.md")
    assert request.candidate_cv_path == Path("cv.txt")


def test_bytes_sources_require_filenames() -> None:
    with pytest.raises(ValidationError):
        PrepareInterviewRequest(
            company_name="Acme",
            job_title="Engineer",
            job_description_bytes=b"Build Python systems.",
            candidate_cv_bytes=b"Built Python systems.",
            candidate_cv_filename="cv.txt",
        )


def test_document_sources_must_be_present_and_unambiguous() -> None:
    with pytest.raises(ValidationError):
        PrepareInterviewRequest(
            company_name="Acme",
            job_title="Engineer",
            job_description_text="Build Python systems.",
            job_description_path=Path("job.md"),
            candidate_cv_text="Built Python systems.",
        )
    with pytest.raises(ValidationError):
        PrepareInterviewRequest(
            company_name="Acme",
            job_title="Engineer",
            job_description_text="Build Python systems.",
        )


@pytest.mark.parametrize("num_questions", [2, 16])
def test_question_count_must_remain_within_mvp_bounds(num_questions: int) -> None:
    with pytest.raises(ValidationError):
        PrepareInterviewRequest(
            company_name="Acme",
            job_title="Engineer",
            job_description_text="Build Python systems.",
            candidate_cv_text="Built Python systems.",
            num_questions=num_questions,
        )


# ==================== P1: PositionQuestionInput / PrepareFromPositionRequest ====================

def test_position_question_requires_category() -> None:
    with pytest.raises(ValidationError) as exc_info:
        PositionQuestionInput(question="Tell me about your Python experience.")  # type: ignore[call-arg]
    assert any(error["loc"] == ("category",) for error in exc_info.value.errors())


def test_position_question_never_defaults_a_category() -> None:
    """No amount of other data supplied should let category be inferred or omitted --
    it must always come from an explicit, separate value."""
    with pytest.raises(ValidationError):
        PositionQuestionInput(
            question="Tell me about your Python experience.",
            purpose="Assess technical depth.",
            expected_topics=["Python"],
            difficulty="medium",
            follow_up_allowed=True,
        )  # type: ignore[call-arg]


def test_position_question_rejects_blank_question_text() -> None:
    with pytest.raises(ValidationError):
        PositionQuestionInput(category=QuestionCategory.TECHNICAL, question="   ")


def test_position_question_purpose_and_difficulty_have_sensible_defaults() -> None:
    question = PositionQuestionInput(category=QuestionCategory.TECHNICAL, question="Explain your SQL experience.")
    assert question.purpose is None  # filled in deterministically by the service, never guessed as a category
    assert question.difficulty == "medium"
    assert question.follow_up_allowed is True
    assert question.expected_topics == []


def test_position_question_is_frozen() -> None:
    question = PositionQuestionInput(category=QuestionCategory.TECHNICAL, question="Explain your SQL experience.")
    with pytest.raises(ValidationError):
        question.category = QuestionCategory.BEHAVIORAL  # type: ignore[misc]


def _position_question(**overrides: object) -> PositionQuestionInput:
    defaults: dict[str, object] = {"category": QuestionCategory.TECHNICAL, "question": "Explain your Python experience."}
    defaults.update(overrides)
    return PositionQuestionInput(**defaults)  # type: ignore[arg-type]


def test_prepare_from_position_requires_candidate_name() -> None:
    with pytest.raises(ValidationError):
        PrepareFromPositionRequest(
            job_title="Engineer", candidate_name="   ", questions=[_position_question()],
        )


def test_prepare_from_position_requires_at_least_one_question() -> None:
    with pytest.raises(ValidationError):
        PrepareFromPositionRequest(job_title="Engineer", candidate_name="Jordan Rivera", questions=[])


def test_prepare_from_position_cv_and_jd_are_both_optional() -> None:
    request = PrepareFromPositionRequest(
        job_title="Engineer", candidate_name="Jordan Rivera", questions=[_position_question()],
    )
    assert request.candidate_cv_text is None
    assert request.job_description_text is None


def test_prepare_from_position_rejects_blank_optional_documents() -> None:
    with pytest.raises(ValidationError):
        PrepareFromPositionRequest(
            job_title="Engineer", candidate_name="Jordan Rivera",
            candidate_cv_text="   ", questions=[_position_question()],
        )
    with pytest.raises(ValidationError):
        PrepareFromPositionRequest(
            job_title="Engineer", candidate_name="Jordan Rivera",
            job_description_text="   ", questions=[_position_question()],
        )


def test_prepare_from_position_accepts_multiple_categorized_questions() -> None:
    request = PrepareFromPositionRequest(
        job_title="Engineer",
        candidate_name="Jordan Rivera",
        questions=[
            _position_question(category=QuestionCategory.TECHNICAL, question="Explain your Python experience."),
            _position_question(category=QuestionCategory.BEHAVIORAL, question="Describe a team conflict."),
        ],
    )
    assert [q.category for q in request.questions] == [QuestionCategory.TECHNICAL, QuestionCategory.BEHAVIORAL]
