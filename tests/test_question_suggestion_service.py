from unittest.mock import Mock

import pytest

from application.position_question_policy import PositionQuestionPolicyError
from application.question_suggestion_service import QuestionSuggestionService
from models.common import PositionStatus, QuestionCategory
from models.interview import InterviewPlan, InterviewQuestion
from models.platform import HRUser, Position


def _owner() -> HRUser:
    return HRUser(
        id=7,
        email="recruiter@example.test",
        password_hash="hashed",
        full_name="Recruiter",
    )


def _position() -> Position:
    return Position(
        id=11,
        owner_id=7,
        company_name="Acme",
        title="Platform Engineer",
        description="Design reusable Terraform modules for multi-cloud environments.",
        experience_level="Senior",
        status=PositionStatus.ACTIVE,
    )


def _question(
    *,
    category: QuestionCategory = QuestionCategory.TECHNICAL,
    text: str = "Describe your experience designing reusable Terraform modules.",
) -> InterviewQuestion:
    return InterviewQuestion(
        id="role-q-1",
        category=category,
        question=text,
        purpose="Assess a core role requirement.",
        expected_topics=["Terraform"],
        difficulty="hard",
        follow_up_allowed=True,
    )


def _service(question: InterviewQuestion) -> tuple[QuestionSuggestionService, Mock]:
    positions = Mock()
    positions.get.return_value = _position()
    agent = Mock()
    agent.suggest_position_questions.return_value = InterviewPlan(questions=[question])
    return QuestionSuggestionService(positions, agent), agent


def test_position_suggestion_calls_only_the_jd_scoped_agent_method() -> None:
    service, agent = _service(_question())

    result = service.suggest(_owner(), 11, num_questions=6)

    assert result == [_question()]
    agent.suggest_position_questions.assert_called_once_with(
        job_title="Platform Engineer",
        company_name="Acme",
        experience_level="Senior",
        num_questions=6,
        job_description_text="Design reusable Terraform modules for multi-cloud environments.",
    )
    assert not agent.suggest_questions.called


def test_candidate_context_fails_at_the_position_service_signature() -> None:
    service, _agent = _service(_question())

    with pytest.raises(TypeError):
        service.suggest(_owner(), 11, candidate_id=99)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "question",
    [
        _question(text="Your CV highlights Terraform across AWS and Azure. Explain it."),
        _question(text="Priya, tell me how you designed the platform."),
        _question(category=QuestionCategory.CV_PROJECT_VALIDATION),
    ],
)
def test_candidate_specific_or_candidate_only_provider_output_is_rejected(
    question: InterviewQuestion,
) -> None:
    service, _agent = _service(question)

    with pytest.raises(PositionQuestionPolicyError):
        service.suggest(_owner(), 11)
