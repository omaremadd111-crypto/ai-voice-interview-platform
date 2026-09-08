"""Single transport-neutral orchestration API for the interview product.

This layer coordinates the Phase 1-6.5 components without reimplementing their
business rules. UI, API, and voice adapters call this service only.
"""
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from agents.candidate_analyzer import CandidateAnalyzer
from agents.evaluator import Evaluator
from agents.fit_analyzer import FitAnalyzer
from agents.interview_planner import InterviewPlanner
from agents.interviewer import Interviewer
from agents.job_analyzer import JobAnalyzer
from application.dto import (
    PositionQuestionInput,
    PrepareFromPositionRequest,
    PrepareInterviewRequest,
    PrepareInterviewResult,
    SessionStatus,
)
from config.rubric_config import RubricConfig, load_rubric_config
from config.settings import Settings
from models.candidate import CandidateAnalysis, CandidateInput, FitAnalysis
from models.common import InterviewState, QuestionCategory
from models.evaluation import HRReport, InterviewEvaluation
from models.interview import InterviewPlan, InterviewQuestion, InterviewTranscript, NextPrompt
from models.job import JobAnalysis, JobInput
from models.session import InterviewSession
from models.voice_intent import VoiceIntent
from services.document_parser import DocumentParser, ParsedDocument
from services.interview_engine import InterviewEngine, InvalidInterviewStateError
from services.llm.base import LLMService
from services.llm.factory import get_llm_service
from services.logging_service import Event, content_metadata, log_event
from services.report_service import build_hr_report, save_report
from services.scoring import build_interview_evaluation
from services.session_service import InMemorySessionStore, SessionStore

ProgressCallback = Callable[[str], None]


class InterviewAgentService:
    def __init__(
        self,
        settings: Settings | None = None,
        session_store: SessionStore | None = None,
        llm_service: LLMService | None = None,
        document_parser: DocumentParser | None = None,
    ) -> None:
        self._settings = settings or Settings()
        self._store = session_store or InMemorySessionStore()
        self._llm = llm_service or get_llm_service(self._settings)
        self._document_parser = document_parser or DocumentParser(self._settings)

        self._job_analyzer = JobAnalyzer(self._llm)
        self._candidate_analyzer = CandidateAnalyzer(self._llm)
        self._fit_analyzer = FitAnalyzer(self._llm)
        self._interview_planner = InterviewPlanner(self._llm)
        self._interviewer = Interviewer(self._llm)
        self._interview_engine = InterviewEngine(
            self._interviewer,
            max_follow_ups_per_question=self._settings.max_follow_ups_per_question,
        )
        self._evaluator = Evaluator(self._llm)
        self._rubric_config: RubricConfig = load_rubric_config(self._settings.rubrics_path)

    def prepare_interview(
        self,
        req: PrepareInterviewRequest,
        on_progress: ProgressCallback | None = None,
    ) -> PrepareInterviewResult:
        session_id = uuid4().hex

        _notify(on_progress, "Reading Job Description...")
        job_document = self._parse_document(
            text=req.job_description_text,
            path=req.job_description_path,
            data=req.job_description_bytes,
            filename=req.job_description_filename,
            default_filename="job_description.txt",
        )

        _notify(on_progress, "Reading Candidate CV...")
        candidate_document = self._parse_document(
            text=req.candidate_cv_text,
            path=req.candidate_cv_path,
            data=req.candidate_cv_bytes,
            filename=req.candidate_cv_filename,
            default_filename="candidate_cv.txt",
        )

        experience_level = req.experience_level.value if req.experience_level is not None else None

        _notify(on_progress, "Analyzing role...")
        job_analysis = self._job_analyzer.analyze(
            req.job_title,
            experience_level,
            job_document.text,
        )

        _notify(on_progress, "Analyzing candidate...")
        candidate_analysis = self._candidate_analyzer.analyze(candidate_document.text)

        _notify(on_progress, "Comparing candidate and role...")
        fit_analysis = self._fit_analyzer.analyze(job_analysis, candidate_analysis)

        _notify(on_progress, "Creating interview plan...")
        interview_plan = self._interview_planner.plan(
            req.job_title,
            req.company_name,
            experience_level,
            req.num_questions,
            job_analysis,
            candidate_analysis,
            fit_analysis,
        )

        now = _now_iso()
        session = InterviewSession(
            id=session_id,
            company=req.company_name,
            job_input=JobInput(
                company_name=req.company_name,
                job_title=req.job_title,
                experience_level=req.experience_level,
                description_text=job_document.text,
            ),
            job_analysis=job_analysis,
            candidate_input=CandidateInput(
                full_name=req.candidate_name or candidate_analysis.full_name,
                cv_text=candidate_document.text,
            ),
            candidate_analysis=candidate_analysis,
            fit_analysis=fit_analysis,
            interview_plan=interview_plan,
            state=InterviewState.CREATED,
            created_at=now,
            updated_at=now,
            llm_provider=self._llm.provider_name,
            is_mock=self._llm.is_mock,
        )
        self._store.create(session)
        self._log_preparation(session, job_document, candidate_document)

        _notify(on_progress, "Interview ready.")
        return PrepareInterviewResult(
            session_id=session.id,
            state=session.state,
            job_analysis=job_analysis,
            candidate_analysis=candidate_analysis,
            fit_analysis=fit_analysis,
            interview_plan=interview_plan,
            llm_provider=session.llm_provider,
            is_mock=session.is_mock,
        )

    def prepare_from_position(
        self,
        req: PrepareFromPositionRequest,
        on_progress: ProgressCallback | None = None,
    ) -> PrepareInterviewResult:
        """Position-driven preparation: HR's own questions become the interview plan
        directly, with no AI planning call. Preserves prepare_interview() unchanged --
        this is an additive second entry point into the same session/engine/evaluator
        core, not a replacement.
        """
        session_id = uuid4().hex
        experience_level = req.experience_level.value if req.experience_level is not None else None

        job_input: JobInput | None = None
        if req.job_description_text is not None:
            _notify(on_progress, "Reading position description...")
            job_document = self._document_parser.parse_bytes(
                req.job_description_text.encode("utf-8"), "position_description.txt",
            )
            _notify(on_progress, "Analyzing role...")
            job_analysis = self._job_analyzer.analyze(req.job_title, experience_level, job_document.text)
            job_input = JobInput(
                company_name=req.company_name, job_title=req.job_title,
                experience_level=req.experience_level, description_text=job_document.text,
            )
            _log_parsed_document(session_id, "position_description", job_document)
        else:
            job_analysis = JobAnalysis(job_title=req.job_title, experience_level=experience_level)

        # A CV is genuinely optional here: a candidate may be added with just a name.
        candidate_input: CandidateInput | None = None
        candidate_analysis = CandidateAnalysis(full_name=req.candidate_name)
        if req.candidate_cv_text is not None:
            _notify(on_progress, "Reading candidate CV...")
            cv_document = self._document_parser.parse_bytes(
                req.candidate_cv_text.encode("utf-8"), "candidate_cv.txt",
            )
            _notify(on_progress, "Analyzing candidate...")
            candidate_analysis = self._candidate_analyzer.analyze(cv_document.text)
            candidate_input = CandidateInput(
                full_name=req.candidate_name or candidate_analysis.full_name,
                cv_text=cv_document.text,
            )
            _log_parsed_document(session_id, "candidate_cv", cv_document)

        fit_analysis = FitAnalysis()
        if req.job_description_text is not None and req.candidate_cv_text is not None:
            _notify(on_progress, "Comparing candidate and role...")
            fit_analysis = self._fit_analyzer.analyze(job_analysis, candidate_analysis)

        _notify(on_progress, "Building interview plan from position questions...")
        interview_plan = _plan_from_position_questions(req.job_title, req.company_name, req.questions)

        now = _now_iso()
        session = InterviewSession(
            id=session_id,
            company=req.company_name,
            position_id=req.position_id,
            candidate_id=req.candidate_id,
            job_input=job_input,
            job_analysis=job_analysis,
            candidate_input=candidate_input,
            candidate_analysis=candidate_analysis,
            fit_analysis=fit_analysis,
            interview_plan=interview_plan,
            state=InterviewState.CREATED,
            created_at=now,
            updated_at=now,
            llm_provider=self._llm.provider_name,
            is_mock=self._llm.is_mock,
        )
        self._store.create(session)

        log_event(
            Event.SESSION_CREATED,
            session_id=session.id,
            llm_provider=session.llm_provider,
            is_mock=session.is_mock,
            source="position",
        )
        if job_input is not None:
            log_event(
                Event.JOB_ANALYZED,
                session_id=session.id,
                required_skill_count=len(job_analysis.required_skills),
                technical_topic_count=len(job_analysis.technical_topics),
            )
        if candidate_input is not None:
            log_event(
                Event.CANDIDATE_ANALYZED,
                session_id=session.id,
                skill_count=len(candidate_analysis.skills),
                claim_count=len(candidate_analysis.important_cv_claims),
            )
        log_event(
            Event.INTERVIEW_PLAN_CREATED,
            session_id=session.id,
            question_count=len(interview_plan.questions),
            plan_updated=False,
            source="position",
        )

        _notify(on_progress, "Interview ready.")
        return PrepareInterviewResult(
            session_id=session.id,
            state=session.state,
            job_analysis=job_analysis,
            candidate_analysis=candidate_analysis,
            fit_analysis=fit_analysis,
            interview_plan=interview_plan,
            llm_provider=session.llm_provider,
            is_mock=session.is_mock,
        )

    def suggest_questions(
        self,
        *,
        job_title: str,
        company_name: str,
        experience_level: str | None,
        num_questions: int,
        job_description_text: str | None,
        candidate_cv_text: str | None,
        existing_questions: list[str] | None = None,
    ) -> InterviewPlan:
        """Propose an interview plan WITHOUT creating a session or persisting anything.

        Runs the same JobAnalyzer -> CandidateAnalyzer -> FitAnalyzer ->
        InterviewPlanner pipeline prepare_interview() already uses, so there is
        exactly one implementation of question generation. The result is a
        proposal only: HR reviews, edits, and approves it before any of it
        becomes a position's questions (SPEC 7 -- the AI proposes, the human
        recruiter approves).
        """
        job_analysis = (
            self._job_analyzer.analyze(job_title, experience_level, job_description_text)
            if job_description_text
            else JobAnalysis(job_title=job_title, experience_level=experience_level)
        )
        candidate_analysis = (
            self._candidate_analyzer.analyze(candidate_cv_text)
            if candidate_cv_text
            else CandidateAnalysis()
        )
        fit_analysis = (
            self._fit_analyzer.analyze(job_analysis, candidate_analysis)
            if job_description_text and candidate_cv_text
            else FitAnalysis()
        )
        return self._interview_planner.plan(
            job_title,
            company_name,
            experience_level,
            num_questions,
            job_analysis,
            candidate_analysis,
            fit_analysis,
            existing_questions,
        )

    def suggest_position_questions(
        self,
        *,
        job_title: str,
        company_name: str,
        experience_level: str | None,
        num_questions: int,
        job_description_text: str | None,
        existing_questions: list[str] | None = None,
    ) -> InterviewPlan:
        """Propose reusable Position Question Bank content from role/JD only.

        This is the same JobAnalyzer -> InterviewPlanner architecture used by
        candidate planning, with the candidate analyzers intentionally excluded.
        The strict signature prevents candidate identifiers, CVs, claims, names,
        or candidate plans from entering this scope.
        """
        job_analysis = (
            self._job_analyzer.analyze(job_title, experience_level, job_description_text)
            if job_description_text
            else JobAnalysis(job_title=job_title, experience_level=experience_level)
        )
        return self._interview_planner.plan_position_questions(
            job_title,
            company_name,
            experience_level,
            num_questions,
            job_analysis,
            existing_questions,
        )

    def update_plan(
        self,
        session_id: str,
        questions: list[InterviewQuestion],
    ) -> InterviewPlan:
        session = self._store.get(session_id)
        _require_state(session, InterviewState.CREATED, "update the interview plan")
        updated_plan = InterviewPlan.model_validate({"questions": questions})
        session.interview_plan = updated_plan
        _touch(session)
        self._store.save(session)
        log_event(
            Event.INTERVIEW_PLAN_CREATED,
            session_id=session.id,
            question_count=len(updated_plan.questions),
            plan_updated=True,
        )
        return updated_plan.model_copy(deep=True)

    def approve_plan(self, session_id: str) -> None:
        session = self._store.get(session_id)
        _require_state(session, InterviewState.CREATED, "approve the interview plan")
        if session.interview_plan is None or not session.interview_plan.questions:
            raise InvalidInterviewStateError("Cannot approve a session with no interview plan.")
        session.state = InterviewState.READY
        _touch(session)
        self._store.save(session)

    def start_interview(self, session_id: str) -> NextPrompt:
        session = self._store.get(session_id)
        prompt = self._interview_engine.start(session)
        self._store.save(session)
        return prompt

    def submit_answer(
        self,
        session_id: str,
        answer: str,
        *,
        intent_hint: VoiceIntent | None = None,
    ) -> NextPrompt:
        session = self._store.get(session_id)
        prompt = self._interview_engine.submit_answer(session, answer, intent_hint=intent_hint)
        self._store.save(session)
        return prompt

    def get_current_prompt(self, session_id: str) -> NextPrompt:
        """Expose the engine-owned current prompt for repeat/rephrase requests."""
        session = self._store.get(session_id)
        return self._interview_engine.current_prompt(session)

    def get_upcoming_category(self, session_id: str) -> QuestionCategory | None:
        """Read-only look-ahead: the category of the question AFTER the current one.

        Lets the voice layer tell the single classifier call whether a topic
        change is imminent so the same call can produce a context-sensitive
        transition. Engine-owned data only -- this never mutates anything and
        the engine alone decides what actually happens next.
        """
        session = self._store.get(session_id)
        if (
            session.state is not InterviewState.IN_PROGRESS
            or session.interview_plan is None
        ):
            return None
        questions = session.interview_plan.questions
        upcoming_index = session.current_question_index + 1
        if upcoming_index >= len(questions):
            return None
        return questions[upcoming_index].category

    def end_interview(self, session_id: str) -> InterviewTranscript:
        session = self._store.get(session_id)
        transcript = self._interview_engine.end(session)
        self._store.save(session)
        return transcript.model_copy(deep=True)

    def evaluate_interview(self, session_id: str) -> InterviewEvaluation:
        session = self._store.get(session_id)
        _require_state(session, InterviewState.COMPLETED, "evaluate the interview")
        if session.interview_plan is None:
            raise InvalidInterviewStateError("Cannot evaluate a session with no interview plan.")

        category_evaluations = self._evaluator.evaluate_all_categories(
            session.interview_plan,
            session.transcript,
            session_id=session.id,
        )
        profile_name = self._rubric_config.default_profile
        profile = self._rubric_config.get_profile(profile_name)
        evaluation = build_interview_evaluation(
            category_evaluations,
            profile,
            profile_name,
            self._rubric_config.thresholds,
            session_id=session.id,
        )
        session.evaluation = evaluation
        session.state = InterviewState.EVALUATED
        _touch(session)
        self._store.save(session)
        return evaluation.model_copy(deep=True)

    def generate_report(self, session_id: str) -> HRReport:
        session = self._store.get(session_id)
        _require_state(session, InterviewState.EVALUATED, "generate the HR report")
        if session.evaluation is None:
            raise InvalidInterviewStateError("Cannot generate a report without an evaluation.")

        report = build_hr_report(session, session.evaluation)
        save_report(report, self._settings.reports_dir)
        session.report = report
        _touch(session)
        self._store.save(session)
        return report.model_copy(deep=True)

    def get_status(self, session_id: str) -> SessionStatus:
        session = self._store.get(session_id)
        plan_questions = session.interview_plan.questions if session.interview_plan is not None else []
        planned_ids = {question.id for question in plan_questions}
        answered_ids = {
            turn.question_id
            for turn in session.transcript.turns
            if not turn.is_follow_up and turn.question_id in planned_ids
        }
        role = (
            session.job_analysis.job_title
            if session.job_analysis is not None
            else session.job_input.job_title if session.job_input is not None else "Unknown role"
        )
        return SessionStatus(
            session_id=session.id,
            company=session.company,
            role=role,
            state=session.state,
            current_question_index=session.current_question_index,
            total_questions=len(plan_questions),
            answered_main_questions=len(answered_ids),
            total_turns=len(session.transcript.turns),
            total_follow_ups=session.total_follow_up_count,
            has_evaluation=session.evaluation is not None,
            has_report=session.report is not None,
            llm_provider=session.llm_provider,
            is_mock=session.is_mock,
        )

    def _parse_document(
        self,
        *,
        text: str | None,
        path: Path | None,
        data: bytes | None,
        filename: str | None,
        default_filename: str,
    ) -> ParsedDocument:
        if path is not None:
            return self._document_parser.parse_path(path)
        if data is not None:
            if filename is None:
                raise ValueError("A filename is required when parsing document bytes")
            return self._document_parser.parse_bytes(data, filename)
        if text is not None:
            return self._document_parser.parse_bytes(
                text.encode("utf-8"),
                filename or default_filename,
            )
        raise ValueError("Document source is missing")

    def _log_preparation(
        self,
        session: InterviewSession,
        job_document: ParsedDocument,
        candidate_document: ParsedDocument,
    ) -> None:
        log_event(
            Event.SESSION_CREATED,
            session_id=session.id,
            llm_provider=session.llm_provider,
            is_mock=session.is_mock,
        )
        _log_parsed_document(session.id, "job_description", job_document)
        _log_parsed_document(session.id, "candidate_cv", candidate_document)
        log_event(
            Event.JOB_ANALYZED,
            session_id=session.id,
            required_skill_count=len(session.job_analysis.required_skills),
            technical_topic_count=len(session.job_analysis.technical_topics),
        )
        log_event(
            Event.CANDIDATE_ANALYZED,
            session_id=session.id,
            skill_count=len(session.candidate_analysis.skills),
            claim_count=len(session.candidate_analysis.important_cv_claims),
        )
        log_event(
            Event.INTERVIEW_PLAN_CREATED,
            session_id=session.id,
            question_count=len(session.interview_plan.questions),
            plan_updated=False,
        )


def _notify(callback: ProgressCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _require_state(session: InterviewSession, required: InterviewState, action: str) -> None:
    if session.state != required:
        raise InvalidInterviewStateError(
            f"Cannot {action} from state {session.state}; must be {required}."
        )


def _touch(session: InterviewSession) -> None:
    session.updated_at = _now_iso()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_parsed_document(session_id: str, label: str, document: ParsedDocument) -> None:
    log_event(
        Event.DOCUMENT_PARSED,
        session_id=session_id,
        document_kind=label,
        filename=document.filename,
        extension=document.extension,
        page_count=document.page_count,
        truncated=document.truncated,
        **content_metadata(f"{label}_text", document.text),
    )


def _to_interview_question(item: PositionQuestionInput, index: int) -> InterviewQuestion:
    return InterviewQuestion(
        id=f"position-q-{index}",
        category=item.category,
        question=item.question,
        purpose=item.purpose or f"Assess {item.category.value.replace('_', ' ')}.",
        expected_topics=item.expected_topics,
        difficulty=item.difficulty,
        follow_up_allowed=item.follow_up_allowed,
    )


def _plan_from_position_questions(
    job_title: str, company_name: str, questions: list[PositionQuestionInput],
) -> InterviewPlan:
    """Wrap HR's authored questions with the same generic intro/closing shape the
    AI planner already produces, so InterviewEngine/Evaluator see an identical
    InterviewPlan structure regardless of which preparation path built it.

    The generic intro/closing are only added when HR's own list does not already
    contain one. Without that check, a position whose questions came from the AI
    suggestion flow (which can propose its own intro/closing) would be framed by
    a second pair, and the candidate would be greeted twice.
    """
    content_questions = [_to_interview_question(item, index) for index, item in enumerate(questions, start=1)]
    authored_categories = {question.category for question in content_questions}
    intro = InterviewQuestion(
        id="position-intro",
        category=QuestionCategory.INTRODUCTION,
        question=(
            f"Thanks for joining. Could you briefly introduce yourself and what "
            f"interests you about the {job_title} role at {company_name}?"
        ),
        purpose="Warm-up and initial rapport; establish the candidate's framing of their own fit.",
        difficulty="easy",
        follow_up_allowed=False,
    )
    closing = InterviewQuestion(
        id="position-closing",
        category=QuestionCategory.CLOSING,
        question="Do you have any questions for us about the role, the team, or the company?",
        purpose="Give the candidate an opportunity to ask questions; assess engagement.",
        difficulty="easy",
        follow_up_allowed=False,
    )
    return InterviewPlan(questions=[
        *([] if QuestionCategory.INTRODUCTION in authored_categories else [intro]),
        *content_questions,
        *([] if QuestionCategory.CLOSING in authored_categories else [closing]),
    ])
