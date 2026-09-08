"""Interview preparation use case.

Exposes P1's position-driven interview preparation over the API by loading a
position's persisted questions and a candidate through P2's repositories, then
calling the EXISTING InterviewAgentService.prepare_from_position() unchanged.
This module contains no interview, follow-up, or evaluation logic of its own --
it only assembles the request the core already knows how to handle.
"""
from application.dto import PositionQuestionInput, PrepareFromPositionRequest, PrepareInterviewResult
from application.interview_agent_service import InterviewAgentService
from application.position_service import PositionService
from models.common import ExperienceLevel
from models.platform import HRUser, PositionQuestionRecord
from services.db.candidates import CandidateRepository
from services.db.interview_plans import InterviewPlanRepository
from services.db.questions import PositionQuestionRepository


class InterviewPreparationServiceError(Exception):
    """Base class for interview-preparation use-case failures."""


class PositionHasNoQuestionsError(InterviewPreparationServiceError):
    """Raised when preparing an interview for a position with zero authored questions."""


class CandidatePositionMismatchError(InterviewPreparationServiceError):
    """Raised when the requested candidate does not belong to the requested position."""


class InterviewPreparationService:
    def __init__(
        self,
        position_service: PositionService,
        question_repo: PositionQuestionRepository,
        candidate_repo: CandidateRepository,
        interview_agent_service: InterviewAgentService,
        plan_repo: InterviewPlanRepository | None = None,
    ) -> None:
        self._position_service = position_service
        self._question_repo = question_repo
        self._candidate_repo = candidate_repo
        self._interview_agent_service = interview_agent_service
        self._plan_repo = plan_repo

    def prepare(self, owner: HRUser, position_id: int, candidate_id: int) -> PrepareInterviewResult:
        position = self._position_service.get(owner, position_id)
        candidate = self._candidate_repo.get(candidate_id)
        # Ownership of the candidate is proven via its OWN position, not the
        # requested position_id -- this also catches a candidate belonging to a
        # position the caller does not own, before the mismatch check below.
        self._position_service.get(owner, candidate.position_id)
        if candidate.position_id != position_id:
            raise CandidatePositionMismatchError(
                f"Candidate {candidate_id} belongs to position {candidate.position_id}, not {position_id}"
            )

        # An approved candidate plan is what the recruiter signed off on, so it
        # takes precedence over the position's baseline bank. A draft plan is
        # deliberately ignored -- unreviewed questions must never drive a real
        # interview (SPEC 7).
        plan_inputs = self._approved_plan_questions(candidate_id)
        if plan_inputs is not None:
            questions = plan_inputs
        else:
            bank = self._question_repo.list_for_position(position_id)
            if not bank:
                raise PositionHasNoQuestionsError(
                    f"Position {position_id} has no authored questions"
                )
            questions = [_to_question_input(q) for q in bank]

        request = PrepareFromPositionRequest(
            company_name=position.company_name,
            position_id=position.id,
            job_title=position.title,
            experience_level=_parse_experience_level(position.experience_level),
            job_description_text=position.description,
            candidate_name=candidate.full_name,
            candidate_id=candidate.id,
            candidate_cv_text=candidate.cv_text,
            questions=questions,
        )
        return self._interview_agent_service.prepare_from_position(request)

    def _approved_plan_questions(self, candidate_id: int) -> list[PositionQuestionInput] | None:
        if self._plan_repo is None:
            return None
        plan = self._plan_repo.get_for_candidate(candidate_id)
        if plan is None or not plan.is_approved or not plan.questions:
            return None
        return [
            PositionQuestionInput(
                category=question.category,
                question=question.question,
                purpose=question.purpose,
                expected_topics=question.expected_topics,
                difficulty=question.difficulty,
                follow_up_allowed=question.follow_up_allowed,
            )
            for question in sorted(plan.questions, key=lambda q: q.order)
        ]


def _to_question_input(record: PositionQuestionRecord) -> PositionQuestionInput:
    return PositionQuestionInput(
        category=record.category,
        question=record.question,
        purpose=record.purpose,
        expected_topics=record.expected_topics,
        difficulty=record.difficulty,
        follow_up_allowed=record.follow_up_allowed,
    )


def _parse_experience_level(value: str | None) -> ExperienceLevel | None:
    if value is None:
        return None
    try:
        return ExperienceLevel(value)
    except ValueError:
        return None
