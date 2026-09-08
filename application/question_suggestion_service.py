"""AI-assisted, JD-only Position Question Bank suggestions.

This use case deliberately has no candidate repository and accepts no candidate
identifier or document. It reuses InterviewAgentService's existing planning
agent in position-baseline scope, then validates that provider output stayed on
the reusable role side of the domain boundary.
"""
from application.interview_agent_service import InterviewAgentService
from application.position_question_policy import validate_position_question_content
from application.position_service import PositionService
from models.common import ExperienceLevel
from models.interview import InterviewQuestion
from models.platform import HRUser

MIN_SUGGESTED_QUESTIONS = 3
MAX_SUGGESTED_QUESTIONS = 15


class QuestionSuggestionService:
    def __init__(
        self,
        position_service: PositionService,
        interview_agent_service: InterviewAgentService,
    ) -> None:
        self._position_service = position_service
        self._interview_agent_service = interview_agent_service

    def suggest(
        self,
        owner: HRUser,
        position_id: int,
        *,
        num_questions: int = 6,
    ) -> list[InterviewQuestion]:
        if not (MIN_SUGGESTED_QUESTIONS <= num_questions <= MAX_SUGGESTED_QUESTIONS):
            raise ValueError(
                f"num_questions must be between {MIN_SUGGESTED_QUESTIONS} "
                f"and {MAX_SUGGESTED_QUESTIONS}"
            )

        position = self._position_service.get(owner, position_id)
        plan = self._interview_agent_service.suggest_position_questions(
            job_title=position.title,
            company_name=position.company_name,
            experience_level=_parse_experience_level(position.experience_level),
            num_questions=num_questions,
            job_description_text=position.description,
        )
        for question in plan.questions:
            validate_position_question_content(
                category=question.category,
                question=question.question,
                purpose=question.purpose,
                expected_topics=question.expected_topics,
            )
        return plan.questions


def _parse_experience_level(value: str | None) -> str | None:
    """Normalize to a known ExperienceLevel value, or pass through unknown text.

    The planner takes a plain string, so an unrecognised free-text level is still
    useful context rather than something to discard.
    """
    if value is None:
        return None
    try:
        return ExperienceLevel(value).value
    except ValueError:
        return value
