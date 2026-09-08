"""Candidate interview plan use cases: generate, edit, regenerate, approve.

Two distinct layers, deliberately kept apart:

  Position question bank  -- position_questions. The fixed baseline every
                             applicant for the role is asked.
  Candidate plan          -- this module. The bank PLUS questions generated for
                             one candidate from their CV, after HR review.

Generation reuses the existing InterviewAgentService pipeline (JobAnalyzer ->
CandidateAnalyzer -> FitAnalyzer -> InterviewPlanner); nothing here writes
questions of its own.
"""
import re

from application.interview_agent_service import InterviewAgentService
from application.position_service import PositionService
from models.common import (
    CandidateStatus,
    ExperienceLevel,
    InterviewPlanStatus,
    PlanQuestionSource,
    QuestionCategory,
)
from models.platform import CandidateInterviewPlan, CandidatePlanQuestion, HRUser
from services.db.candidates import CandidateRepository
from services.db.evaluations import EvaluationRepository
from services.db.interview_plans import InterviewPlanRepository, utc_now
from services.db.questions import PositionQuestionRepository

MIN_GENERATED_QUESTIONS = 3
MAX_GENERATED_QUESTIONS = 15
DEFAULT_GENERATED_QUESTIONS = 8

# Interview preparation frames every plan with its own opener/closer, so carrying
# these inside a saved plan would duplicate them.
_ENGINE_SUPPLIED_CATEGORIES = frozenset({QuestionCategory.INTRODUCTION, QuestionCategory.CLOSING})


class InterviewPlanServiceError(Exception):
    """Base class for interview plan use-case failures."""


class PlanNotFoundError(InterviewPlanServiceError):
    """Raised when a candidate has no interview plan yet."""


class EmptyPlanError(InterviewPlanServiceError):
    """Raised when saving or approving a plan with no questions."""


class QuestionIndexError(InterviewPlanServiceError):
    """Raised when a question index does not exist in the plan."""


class PlanLockedError(InterviewPlanServiceError):
    """Raised when a plan is written to after its interview started or ran.

    Separate from the other plan errors because it is not a mistake the caller
    can correct by sending a better request: the plan is closed for good, so the
    API answers 409 rather than 400.
    """


def _normalize(text: str) -> str:
    """Comparison key for duplicate detection: case- and punctuation-insensitive."""
    return re.sub(r"[^a-z0-9 ]+", "", text.casefold()).strip()


class InterviewPlanService:
    def __init__(
        self,
        position_service: PositionService,
        candidate_repo: CandidateRepository,
        question_repo: PositionQuestionRepository,
        plan_repo: InterviewPlanRepository,
        interview_agent_service: InterviewAgentService,
        evaluation_repo: EvaluationRepository,
    ) -> None:
        self._position_service = position_service
        self._candidate_repo = candidate_repo
        self._question_repo = question_repo
        self._plan_repo = plan_repo
        self._interview_agent_service = interview_agent_service
        self._evaluation_repo = evaluation_repo

    # -- reads -------------------------------------------------------------

    def get(self, owner: HRUser, candidate_id: int) -> CandidateInterviewPlan | None:
        self._require_owned_candidate(owner, candidate_id)
        return self._plan_repo.get_for_candidate(candidate_id)

    def require(self, owner: HRUser, candidate_id: int) -> CandidateInterviewPlan:
        plan = self.get(owner, candidate_id)
        if plan is None:
            raise PlanNotFoundError(
                f"Candidate {candidate_id} has no interview plan yet. Generate one first."
            )
        return plan

    # -- generation --------------------------------------------------------

    def generate(
        self, owner: HRUser, candidate_id: int, *, num_questions: int = DEFAULT_GENERATED_QUESTIONS,
    ) -> CandidateInterviewPlan:
        """Build a fresh draft plan: the position's bank questions first, then
        candidate-specific questions generated from the CV, deduplicated.

        Replaces any existing plan and returns it to DRAFT -- a regenerated plan
        has not been reviewed, so a prior approval must not carry over.
        """
        if not MIN_GENERATED_QUESTIONS <= num_questions <= MAX_GENERATED_QUESTIONS:
            raise ValueError(
                f"num_questions must be between {MIN_GENERATED_QUESTIONS} "
                f"and {MAX_GENERATED_QUESTIONS}"
            )
        # Checked before any provider call, so a locked plan costs nothing.
        candidate = self._require_editable(owner, candidate_id)
        position = self._position_service.get(owner, candidate.position_id)
        bank = self._question_repo.list_for_position(candidate.position_id)

        questions: list[CandidatePlanQuestion] = []
        seen: set[str] = set()

        for entry in sorted(bank, key=lambda q: q.order):
            key = _normalize(entry.question)
            if key in seen:
                continue
            seen.add(key)
            questions.append(CandidatePlanQuestion(
                category=entry.category,
                question=entry.question,
                order=len(questions),
                purpose=entry.purpose,
                expected_topics=entry.expected_topics,
                difficulty=entry.difficulty,
                follow_up_allowed=entry.follow_up_allowed,
                source=PlanQuestionSource.BANK,
            ))

        generated = self._interview_agent_service.suggest_questions(
            job_title=position.title,
            company_name=position.company_name,
            experience_level=_parse_experience_level(position.experience_level),
            num_questions=num_questions,
            job_description_text=position.description,
            candidate_cv_text=candidate.cv_text,
            # Passed so the planner adds coverage on top of the baseline instead
            # of restating it.
            existing_questions=[q.question for q in questions],
        )
        for entry in generated.questions:
            if entry.category in _ENGINE_SUPPLIED_CATEGORIES:
                continue
            key = _normalize(entry.question)
            if key in seen:
                continue
            seen.add(key)
            questions.append(CandidatePlanQuestion(
                category=entry.category,
                question=entry.question,
                order=len(questions),
                purpose=entry.purpose,
                expected_topics=entry.expected_topics,
                difficulty=entry.difficulty,
                follow_up_allowed=entry.follow_up_allowed,
                source=PlanQuestionSource.GENERATED,
            ))

        return self._plan_repo.upsert(CandidateInterviewPlan(
            candidate_id=candidate_id,
            status=InterviewPlanStatus.DRAFT,
            questions=questions,
            generated_at=utc_now(),
        ))

    def regenerate_question(
        self, owner: HRUser, candidate_id: int, index: int,
    ) -> CandidateInterviewPlan:
        """Replace one question with a fresh alternative in the same category,
        leaving every other question and the running order untouched."""
        plan = self.require(owner, candidate_id)
        candidate = self._require_editable(owner, candidate_id)
        if not 0 <= index < len(plan.questions):
            raise QuestionIndexError(f"No question at position {index} in this plan.")

        target = plan.questions[index]
        position = self._position_service.get(owner, candidate.position_id)

        # Everything except the question being replaced is off-limits, so the
        # replacement cannot duplicate a question already in the plan.
        keep = [q.question for i, q in enumerate(plan.questions) if i != index]
        generated = self._interview_agent_service.suggest_questions(
            job_title=position.title,
            company_name=position.company_name,
            experience_level=_parse_experience_level(position.experience_level),
            num_questions=MAX_GENERATED_QUESTIONS,
            job_description_text=position.description,
            candidate_cv_text=candidate.cv_text,
            existing_questions=keep,
        )
        excluded = {_normalize(q) for q in keep} | {_normalize(target.question)}
        replacement = next(
            (
                q for q in generated.questions
                if q.category == target.category and _normalize(q.question) not in excluded
            ),
            None,
        )
        if replacement is None:
            # Fall back to any non-duplicate question rather than failing: HR asked
            # for a different question, and category is preserved where possible.
            replacement = next(
                (
                    q for q in generated.questions
                    if q.category not in _ENGINE_SUPPLIED_CATEGORIES
                    and _normalize(q.question) not in excluded
                ),
                None,
            )
        if replacement is None:
            raise InterviewPlanServiceError(
                "Could not produce a different question for this slot. Edit it manually instead."
            )

        questions = list(plan.questions)
        questions[index] = CandidatePlanQuestion(
            category=replacement.category,
            question=replacement.question,
            order=target.order,
            purpose=replacement.purpose,
            expected_topics=replacement.expected_topics,
            difficulty=replacement.difficulty,
            follow_up_allowed=replacement.follow_up_allowed,
            source=PlanQuestionSource.GENERATED,
        )
        return self._save(candidate_id, questions)

    # -- editing -----------------------------------------------------------

    def save_questions(
        self, owner: HRUser, candidate_id: int, questions: list[CandidatePlanQuestion],
    ) -> CandidateInterviewPlan:
        """Replace the whole question list. Covers edit, add, delete and reorder in
        one atomic write, and returns the plan to DRAFT because the approved set
        is no longer what is on file."""
        self.require(owner, candidate_id)
        self._require_editable(owner, candidate_id)
        if not questions:
            raise EmptyPlanError("An interview plan must contain at least one question.")
        return self._save(candidate_id, questions)

    def approve(self, owner: HRUser, candidate_id: int) -> CandidateInterviewPlan:
        plan = self.require(owner, candidate_id)
        self._require_editable(owner, candidate_id)
        if not plan.questions:
            raise EmptyPlanError("Cannot approve an interview plan with no questions.")
        plan.status = InterviewPlanStatus.APPROVED
        plan.approved_at = utc_now()
        return self._plan_repo.upsert(plan)

    def materialize_from_template(self, owner: HRUser, candidate_id: int) -> CandidateInterviewPlan:
        """Copy the position's approved screening template verbatim into an
        ALREADY-APPROVED candidate plan. No LLM call, no independent
        per-candidate approval step.

        This is the architecture guarantee that auto-pipeline plan approval happens at the
        position level" rule, in code: the human already approved this exact
        question set once, at the template level (PositionPublishingService
        .approve_template), so the copy inherits that approval instead of
        requiring a second one. Called only from the automated pipeline
        (application/application_pipeline_service.py) -- never from a
        recruiter-facing endpoint, which still goes through generate()/
        save_questions()/approve() exactly as before.

        Locked candidates are refused here too. The pipeline never hits that --
        _ensure_candidate() creates a fresh APPLIED candidate for every
        application, so this only ever runs against a brand-new one -- but the
        invariant belongs on every path that writes a plan, not only the
        recruiter-facing ones.
        """
        candidate = self._require_editable(owner, candidate_id)
        bank = self._question_repo.list_for_position(candidate.position_id)
        if not bank:
            raise EmptyPlanError(
                f"Position {candidate.position_id} has no screening template questions to copy."
            )
        questions = [
            CandidatePlanQuestion(
                category=entry.category,
                question=entry.question,
                order=index,
                purpose=entry.purpose,
                expected_topics=entry.expected_topics,
                difficulty=entry.difficulty,
                follow_up_allowed=entry.follow_up_allowed,
                source=PlanQuestionSource.BANK,
            )
            for index, entry in enumerate(sorted(bank, key=lambda q: q.order))
        ]
        now = utc_now()
        return self._plan_repo.upsert(CandidateInterviewPlan(
            candidate_id=candidate_id,
            status=InterviewPlanStatus.APPROVED,
            questions=questions,
            generated_at=now,
            approved_at=now,
        ))

    def _save(
        self, candidate_id: int, questions: list[CandidatePlanQuestion],
    ) -> CandidateInterviewPlan:
        existing = self._plan_repo.get_for_candidate(candidate_id)
        renumbered = [
            question.model_copy(update={"order": index})
            for index, question in enumerate(questions)
        ]
        return self._plan_repo.upsert(CandidateInterviewPlan(
            candidate_id=candidate_id,
            # Any edit invalidates a previous approval: the recruiter approved a
            # specific set of questions, not this new one.
            status=InterviewPlanStatus.DRAFT,
            questions=renumbered,
            generated_at=existing.generated_at if existing is not None else None,
        ))

    def _require_owned_candidate(self, owner: HRUser, candidate_id: int):
        candidate = self._candidate_repo.get(candidate_id)
        self._position_service.get(owner, candidate.position_id)
        return candidate

    def _require_editable(self, owner: HRUser, candidate_id: int):
        """Refuse every plan write once the interview has started or produced a
        report. Guards all five methods that reach _plan_repo.upsert(): generate,
        regenerate_question, save_questions, approve, materialize_from_template.

        The plan is the record of what a candidate was actually screened
        against, so rewriting it afterwards would falsify that record -- and
        mid-interview it would change the questions out from under a call in
        flight. Neither is something a recruiter should be able to do by
        accident; reads stay open, and the plan remains fully visible.

        Two signals rather than one, because they can drift apart: a worker that
        crashes between persisting the report and writing SCREENED leaves a
        fully evaluated candidate sitting in QUEUED (see QueueWorker
        ._release_candidate). A stored evaluation is the stronger fact, so it is
        checked even when the status looks editable.

        A failed or unanswered attempt deliberately does NOT lock: the worker
        returns that candidate to QUEUED precisely because no conversation
        happened, and the plan must stay editable before the next attempt.

        Returns the owned candidate so callers that need it do not re-read it.
        """
        candidate = self._require_owned_candidate(owner, candidate_id)
        if candidate.status is CandidateStatus.SCREENING_IN_PROGRESS:
            raise PlanLockedError(
                "This interview is already in progress, so its plan can no longer be changed."
            )
        if (
            candidate.status is CandidateStatus.SCREENED
            or self._evaluation_repo.get_latest_for_candidate(candidate_id) is not None
        ):
            raise PlanLockedError(
                "This interview is complete. Its plan is the record of what the candidate "
                "was screened against and can no longer be changed."
            )
        return candidate


def _parse_experience_level(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return ExperienceLevel(value).value
    except ValueError:
        return value
