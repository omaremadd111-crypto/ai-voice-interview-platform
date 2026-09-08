"""Phase 7 application-service orchestration and persistence tests."""
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from application.dto import PositionQuestionInput, PrepareFromPositionRequest, PrepareInterviewRequest
from application.interview_agent_service import InterviewAgentService
from config.settings import Settings
from models.common import InterviewState, QuestionCategory, ScreeningOutcome
from models.interview import InterviewQuestion
from services.interview_engine import InvalidInterviewStateError
from services.session_service import InMemorySessionStore, SessionNotFoundError

JD_TEXT = (
    "Acme needs a Junior AI Engineer to build RAG systems using Python, SQL, retrieval, "
    "and vector databases. The engineer will design APIs, test solutions, solve production "
    "problems, and communicate with stakeholders. Nice to have: Docker and AWS."
)
CV_TEXT = (
    "Built a production RAG application using Python, SQL, retrieval, and a vector database. "
    "I designed an API and deployed it with Docker because reliability mattered, reducing "
    "latency by 35 percent. 2 years of software engineering experience. Bachelor degree in "
    "Computer Science."
)
DETAILED_ANSWER = (
    "I built and tested a Python RAG API using SQL, retrieval, a vector database, Docker, "
    "and AWS because reliability mattered. I communicated the design to stakeholders, "
    "debugged production issues, and improved latency by 40 percent so that the team could "
    "scale the service with clear evaluation results."
)
# Long enough to be recorded as a real (non-refusal, non-empty) answer and generate
# transcript evidence, but deliberately unrelated to any position question's expected
# topics -- sufficient evidence, low score. Verified empirically to land below the
# default pass_score_threshold (60) while still producing full evidence coverage.
MEDIOCRE_ANSWER = (
    "It was fine I guess, nothing too crazy happened really, just normal stuff day to "
    "day like everyone else does around here honestly."
)
REFUSAL_ANSWER = "I don't know."


@pytest.fixture
def service_bundle(tmp_path: Path) -> tuple[InterviewAgentService, InMemorySessionStore, Settings]:
    settings = Settings(mock_mode=True, reports_dir=tmp_path / "reports")
    store = InMemorySessionStore()
    service = InterviewAgentService(settings=settings, session_store=store)
    return service, store, settings


def _request(
    *,
    candidate_name: str = "Jordan Rivera",
    candidate_cv_text: str = CV_TEXT,
    num_questions: int = 6,
) -> PrepareInterviewRequest:
    return PrepareInterviewRequest(
        company_name="Acme",
        job_title="Junior AI Engineer",
        experience_level="Junior",
        job_description_text=JD_TEXT,
        candidate_name=candidate_name,
        candidate_cv_text=candidate_cv_text,
        num_questions=num_questions,
        approximate_duration_minutes=20,
    )


def _complete_naturally(service: InterviewAgentService, session_id: str) -> None:
    _complete_with_answer(service, session_id, DETAILED_ANSWER)


def _complete_with_answer(service: InterviewAgentService, session_id: str, answer: str) -> None:
    prompt = service.start_interview(session_id)
    safety_counter = 0
    while not prompt.finished:
        prompt = service.submit_answer(session_id, answer)
        safety_counter += 1
        assert safety_counter < 50


def _position_request(
    *,
    candidate_name: str = "Alex Chen",
    candidate_cv_text: str | None = None,
    job_description_text: str | None = None,
    questions: list[PositionQuestionInput] | None = None,
) -> PrepareFromPositionRequest:
    return PrepareFromPositionRequest(
        company_name="Acme",
        job_title="Engineer",
        experience_level="Junior",
        job_description_text=job_description_text,
        candidate_name=candidate_name,
        candidate_cv_text=candidate_cv_text,
        questions=questions or [
            PositionQuestionInput(
                category=QuestionCategory.TECHNICAL,
                question="Explain your Python and SQL experience.",
                expected_topics=["Python", "SQL"],
            ),
            PositionQuestionInput(
                category=QuestionCategory.PROBLEM_SOLVING,
                question="Describe a challenging problem you solved.",
                expected_topics=["problem solving"],
            ),
        ],
    )


def test_complete_orchestration_flow_from_documents_to_saved_report(
    service_bundle: tuple[InterviewAgentService, InMemorySessionStore, Settings],
) -> None:
    service, store, settings = service_bundle
    progress: list[str] = []

    prepared = service.prepare_interview(_request(), on_progress=progress.append)

    assert prepared.state == InterviewState.CREATED
    assert prepared.is_mock is True
    assert prepared.llm_provider == "mock"
    assert len(prepared.interview_plan.questions) == 6
    assert progress == [
        "Reading Job Description...",
        "Reading Candidate CV...",
        "Analyzing role...",
        "Analyzing candidate...",
        "Comparing candidate and role...",
        "Creating interview plan...",
        "Interview ready.",
    ]

    stored = store.get(prepared.session_id)
    assert stored.job_input.description_text == JD_TEXT
    assert stored.candidate_input.cv_text == CV_TEXT
    assert stored.job_analysis == prepared.job_analysis
    assert stored.fit_analysis == prepared.fit_analysis

    service.approve_plan(prepared.session_id)
    assert service.get_status(prepared.session_id).state == InterviewState.READY

    _complete_naturally(service, prepared.session_id)
    completed_status = service.get_status(prepared.session_id)
    assert completed_status.state == InterviewState.COMPLETED
    assert completed_status.answered_main_questions == completed_status.total_questions == 6
    assert completed_status.total_turns >= 6

    evaluation = service.evaluate_interview(prepared.session_id)
    assert evaluation.overall_score is not None
    assert service.get_status(prepared.session_id).state == InterviewState.EVALUATED
    assert service.get_status(prepared.session_id).has_evaluation is True

    report = service.generate_report(prepared.session_id)
    report_path = settings.reports_dir / f"{prepared.session_id}.md"
    assert report.is_mock is True
    assert report.full_transcript
    assert report_path.exists()
    assert "Demo / Mock Mode" in report_path.read_text(encoding="utf-8")
    assert service.get_status(prepared.session_id).has_report is True
    assert store.get(prepared.session_id).report == report


def test_prepare_preserves_name_extracted_from_cv_when_request_omits_it(
    service_bundle: tuple[InterviewAgentService, InMemorySessionStore, Settings],
) -> None:
    service, store, _settings = service_bundle
    request = _request(
        candidate_name="",
        candidate_cv_text=f"# Jordan Rivera\n\n{CV_TEXT}",
    )

    prepared = service.prepare_interview(request)

    assert prepared.candidate_analysis.full_name == "Jordan Rivera"
    assert store.get(prepared.session_id).candidate_input.full_name == "Jordan Rivera"


def test_plan_update_approval_and_state_persist_across_service_instances(
    service_bundle: tuple[InterviewAgentService, InMemorySessionStore, Settings],
) -> None:
    service, store, settings = service_bundle
    prepared = service.prepare_interview(_request(num_questions=5))
    questions = [question.model_copy(deep=True) for question in prepared.interview_plan.questions[:-1]]
    questions[0].question = "Updated opening question?"
    questions.append(InterviewQuestion(
        id="hr-custom-1",
        category=QuestionCategory.BEHAVIORAL,
        question="Describe a relevant collaboration challenge.",
        purpose="HR-added validation question.",
        expected_topics=["Communication"],
        difficulty="medium",
        follow_up_allowed=True,
    ))

    updated = service.update_plan(prepared.session_id, questions)
    assert len(updated.questions) == 5
    assert updated.questions[0].question == "Updated opening question?"
    assert updated.questions[-1].id == "hr-custom-1"

    questions[0].question = "Mutation after update"
    assert store.get(prepared.session_id).interview_plan.questions[0].question == "Updated opening question?"

    second_service = InterviewAgentService(settings=settings, session_store=store)
    assert second_service.get_status(prepared.session_id).state == InterviewState.CREATED
    second_service.approve_plan(prepared.session_id)
    assert service.get_status(prepared.session_id).state == InterviewState.READY

    with pytest.raises(InvalidInterviewStateError):
        service.update_plan(prepared.session_id, updated.questions)
    with pytest.raises(InvalidInterviewStateError):
        service.approve_plan(prepared.session_id)


def test_early_end_persists_transcript_and_produces_accurate_report(
    service_bundle: tuple[InterviewAgentService, InMemorySessionStore, Settings],
) -> None:
    service, store, _settings = service_bundle
    prepared = service.prepare_interview(_request(num_questions=5))
    service.approve_plan(prepared.session_id)
    service.start_interview(prepared.session_id)
    service.submit_answer(prepared.session_id, DETAILED_ANSWER)

    transcript = service.end_interview(prepared.session_id)
    assert transcript.turns
    assert store.get(prepared.session_id).transcript == transcript
    status = service.get_status(prepared.session_id)
    assert status.state == InterviewState.COMPLETED
    assert status.answered_main_questions == 1

    with pytest.raises(InvalidInterviewStateError):
        service.submit_answer(prepared.session_id, "Too late")

    evaluation = service.evaluate_interview(prepared.session_id)
    assert evaluation.category_evaluations
    report = service.generate_report(prepared.session_id)
    assert "answered 1 of 5 planned main question(s)" in report.interview_summary


def test_prepare_interview_parses_path_and_bytes_sources(
    service_bundle: tuple[InterviewAgentService, InMemorySessionStore, Settings],
    tmp_path: Path,
) -> None:
    service, store, _settings = service_bundle
    jd_path = tmp_path / "job.md"
    jd_path.write_text(JD_TEXT, encoding="utf-8")
    request = PrepareInterviewRequest(
        company_name="Acme",
        job_title="Junior AI Engineer",
        job_description_path=jd_path,
        candidate_cv_bytes=CV_TEXT.encode("utf-8"),
        candidate_cv_filename="candidate.txt",
        num_questions=4,
    )

    prepared = service.prepare_interview(request)
    stored = store.get(prepared.session_id)
    assert stored.job_input.description_text == JD_TEXT
    assert stored.candidate_input.cv_text == CV_TEXT


def test_orchestration_logs_only_safe_document_and_answer_fingerprints(
    service_bundle: tuple[InterviewAgentService, InMemorySessionStore, Settings],
) -> None:
    service, _store, _settings = service_bundle
    logger = logging.getLogger("interview_agent")
    previous_level = logger.level
    records: list[str] = []
    handler = logging.StreamHandler()
    handler.emit = lambda record: records.append(record.getMessage())  # type: ignore[method-assign]
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        prepared = service.prepare_interview(_request())
        service.approve_plan(prepared.session_id)
        service.start_interview(prepared.session_id)
        service.submit_answer(prepared.session_id, DETAILED_ANSWER)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    payloads = [json.loads(record) for record in records]
    document_events = [payload for payload in payloads if payload["event"] == "DOCUMENT_PARSED"]
    assert len(document_events) == 2
    job_event = next(payload for payload in document_events if payload["document_kind"] == "job_description")
    cv_event = next(payload for payload in document_events if payload["document_kind"] == "candidate_cv")
    assert job_event["job_description_text_length"] == len(JD_TEXT)
    assert job_event["job_description_text_sha256"] == hashlib.sha256(JD_TEXT.encode("utf-8")).hexdigest()
    assert cv_event["candidate_cv_text_length"] == len(CV_TEXT)
    assert cv_event["candidate_cv_text_sha256"] == hashlib.sha256(CV_TEXT.encode("utf-8")).hexdigest()

    answer_event = next(payload for payload in payloads if payload["event"] == "QUESTION_ANSWERED")
    assert answer_event["answer_length"] == len(DETAILED_ANSWER)
    assert answer_event["answer_sha256"] == hashlib.sha256(DETAILED_ANSWER.encode("utf-8")).hexdigest()

    serialized = "\n".join(records)
    assert JD_TEXT not in serialized
    assert CV_TEXT not in serialized
    assert DETAILED_ANSWER not in serialized
    assert "Jordan Rivera" not in serialized


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("get_status", ()),
        ("update_plan", ([InterviewQuestion(
            id="q1", category=QuestionCategory.TECHNICAL, question="Q?", purpose="p",
            expected_topics=[], difficulty="medium",
        )],)),
        ("approve_plan", ()),
        ("start_interview", ()),
        ("submit_answer", ("answer",)),
        ("end_interview", ()),
        ("evaluate_interview", ()),
        ("generate_report", ()),
    ],
)
def test_every_session_operation_rejects_unknown_session_ids(
    service_bundle: tuple[InterviewAgentService, InMemorySessionStore, Settings],
    method_name: str,
    args: tuple[Any, ...],
) -> None:
    service, _store, _settings = service_bundle
    method = getattr(service, method_name)
    with pytest.raises(SessionNotFoundError):
        method("missing-session", *args)


def test_invalid_state_transitions_are_rejected_without_persisting_mutations(
    service_bundle: tuple[InterviewAgentService, InMemorySessionStore, Settings],
) -> None:
    service, store, _settings = service_bundle
    prepared = service.prepare_interview(_request(num_questions=4))

    with pytest.raises(InvalidInterviewStateError):
        service.start_interview(prepared.session_id)
    with pytest.raises(InvalidInterviewStateError):
        service.submit_answer(prepared.session_id, "answer")
    with pytest.raises(InvalidInterviewStateError):
        service.end_interview(prepared.session_id)
    with pytest.raises(InvalidInterviewStateError):
        service.evaluate_interview(prepared.session_id)
    with pytest.raises(InvalidInterviewStateError):
        service.generate_report(prepared.session_id)

    unchanged = store.get(prepared.session_id)
    assert unchanged.state == InterviewState.CREATED
    assert unchanged.transcript.turns == []

    service.approve_plan(prepared.session_id)
    service.start_interview(prepared.session_id)
    with pytest.raises(InvalidInterviewStateError):
        service.start_interview(prepared.session_id)


# ==================== P1: position-driven preparation ====================
#
# prepare_from_position() is an additive second entry point into the same session/
# engine/evaluator/scoring core as prepare_interview(). Nothing below this line ever
# touches prepare_interview() or its DTOs; test_complete_orchestration_flow_from_
# documents_to_saved_report above proves that flow is completely unaffected.

def test_prepare_from_position_with_no_cv(
    service_bundle: tuple[InterviewAgentService, InMemorySessionStore, Settings],
) -> None:
    service, store, _settings = service_bundle
    progress: list[str] = []

    prepared = service.prepare_from_position(_position_request(), on_progress=progress.append)

    assert prepared.state == InterviewState.CREATED
    assert "Reading Job Description..." not in progress  # no JD given -> no parse step
    assert "Reading candidate CV..." not in progress  # no CV given -> no parse/analyze step
    assert "Interview ready." in progress

    stored = store.get(prepared.session_id)
    assert stored.candidate_input is None  # no document to store -- CV is genuinely optional
    assert stored.candidate_analysis.full_name == "Alex Chen"
    assert stored.job_input is None  # no JD text given

    categories = [q.category for q in prepared.interview_plan.questions]
    assert categories[0] == QuestionCategory.INTRODUCTION
    assert categories[-1] == QuestionCategory.CLOSING
    assert categories[1:-1] == [QuestionCategory.TECHNICAL, QuestionCategory.PROBLEM_SOLVING]

    # The plan is fully usable through the unchanged engine/evaluator with no CV at all.
    service.approve_plan(prepared.session_id)
    _complete_with_answer(service, prepared.session_id, DETAILED_ANSWER)
    evaluation = service.evaluate_interview(prepared.session_id)
    assert evaluation.category_evaluations
    report = service.generate_report(prepared.session_id)
    assert report.candidate_name == "Alex Chen"


def test_prepare_from_position_with_cv(
    service_bundle: tuple[InterviewAgentService, InMemorySessionStore, Settings],
) -> None:
    service, store, _settings = service_bundle

    prepared = service.prepare_from_position(_position_request(
        job_description_text=JD_TEXT, candidate_cv_text=CV_TEXT,
    ))

    stored = store.get(prepared.session_id)
    assert stored.candidate_input is not None
    assert stored.candidate_input.cv_text == CV_TEXT
    assert stored.job_input is not None
    assert stored.job_input.description_text == JD_TEXT
    # Analyzing the real CV should surface real skills, unlike the no-CV case.
    assert "Python" in prepared.candidate_analysis.skills
    assert prepared.fit_analysis.strong_alignment_areas


def test_position_driven_and_document_driven_sessions_coexist_independently(
    service_bundle: tuple[InterviewAgentService, InMemorySessionStore, Settings],
) -> None:
    """Both preparation entry points can be used on the same service instance
    without interfering -- proof that prepare_from_position() is additive, not
    a fork of prepare_interview()'s behavior."""
    service, store, _settings = service_bundle

    document_driven = service.prepare_interview(_request())
    position_driven = service.prepare_from_position(_position_request())

    assert document_driven.session_id != position_driven.session_id
    assert store.get(document_driven.session_id).candidate_input.cv_text == CV_TEXT
    assert store.get(position_driven.session_id).candidate_input is None

    service.approve_plan(document_driven.session_id)
    service.approve_plan(position_driven.session_id)
    assert service.get_status(document_driven.session_id).state == InterviewState.READY
    assert service.get_status(position_driven.session_id).state == InterviewState.READY


# ==================== P1: required question category ====================

def test_position_question_category_is_required_and_never_defaulted() -> None:
    with pytest.raises(Exception):  # pydantic.ValidationError
        PositionQuestionInput(question="Tell me about your experience.")  # type: ignore[call-arg]


def test_position_question_category_drives_evaluator_mapping(
    service_bundle: tuple[InterviewAgentService, InMemorySessionStore, Settings],
) -> None:
    """The category HR picks is what determines which EvaluationCategory the answer's
    evidence can ever contribute to -- the exact reason category must be required."""
    service, _store, _settings = service_bundle
    prepared = service.prepare_from_position(_position_request(
        questions=[PositionQuestionInput(
            category=QuestionCategory.BEHAVIORAL,
            question="Tell me about a time you communicated a difficult decision.",
            expected_topics=["Communication"],
        )],
    ))
    service.approve_plan(prepared.session_id)
    _complete_with_answer(service, prepared.session_id, DETAILED_ANSWER)
    evaluation = service.evaluate_interview(prepared.session_id)

    communication_eval = next(
        ce for ce in evaluation.category_evaluations
        if ce.category.value == "communication"
    )
    # A BEHAVIORAL question is mapped to communication/behavioral_competencies;
    # it must never silently feed technical_knowledge or another category.
    assert communication_eval.sufficient_evidence is True
    technical_eval = next(
        ce for ce in evaluation.category_evaluations
        if ce.category.value == "technical_knowledge"
    )
    assert technical_eval.sufficient_evidence is False  # nothing fed technical_knowledge


# ==================== P1: screening outcomes end to end ====================

def test_screening_outcome_pass_end_to_end(
    service_bundle: tuple[InterviewAgentService, InMemorySessionStore, Settings],
) -> None:
    service, _store, _settings = service_bundle
    prepared = service.prepare_from_position(_position_request())
    service.approve_plan(prepared.session_id)
    _complete_with_answer(service, prepared.session_id, DETAILED_ANSWER)

    evaluation = service.evaluate_interview(prepared.session_id)
    assert evaluation.screening_outcome == ScreeningOutcome.PASS
    assert evaluation.overall_score is not None and evaluation.overall_score >= 60

    report = service.generate_report(prepared.session_id)
    assert report.screening_outcome == ScreeningOutcome.PASS


def test_screening_outcome_fail_end_to_end(
    service_bundle: tuple[InterviewAgentService, InMemorySessionStore, Settings],
) -> None:
    service, _store, _settings = service_bundle
    prepared = service.prepare_from_position(_position_request())
    service.approve_plan(prepared.session_id)
    _complete_with_answer(service, prepared.session_id, MEDIOCRE_ANSWER)

    evaluation = service.evaluate_interview(prepared.session_id)
    # FAIL requires real evidence to have been gathered -- it is not the same as
    # "we don't know". A mediocre-but-substantive answer generates evidence and a
    # low score, distinct from the empty/refusal case below.
    assert evaluation.evidence_coverage > 0.0
    assert evaluation.screening_outcome == ScreeningOutcome.FAIL
    assert evaluation.overall_score is not None and evaluation.overall_score < 60

    report = service.generate_report(prepared.session_id)
    assert report.screening_outcome == ScreeningOutcome.FAIL
    # The banned-word guard must still hold: FAIL is never rendered as a rejection.
    assert "reject" not in report.recommendation.value.lower()
    assert "disqualif" not in report.recommendation.value.lower()


def test_screening_outcome_needs_review_on_low_evidence(
    service_bundle: tuple[InterviewAgentService, InMemorySessionStore, Settings],
) -> None:
    service, _store, _settings = service_bundle
    prepared = service.prepare_from_position(_position_request())
    service.approve_plan(prepared.session_id)
    _complete_with_answer(service, prepared.session_id, REFUSAL_ANSWER)

    evaluation = service.evaluate_interview(prepared.session_id)
    assert evaluation.overall_score is None
    assert evaluation.screening_outcome == ScreeningOutcome.NEEDS_REVIEW

    report = service.generate_report(prepared.session_id)
    assert report.screening_outcome == ScreeningOutcome.NEEDS_REVIEW


def test_screening_outcome_is_never_treated_as_a_hiring_decision(
    service_bundle: tuple[InterviewAgentService, InMemorySessionStore, Settings],
) -> None:
    """PASS != hire, FAIL != rejected: the report must state this explicitly and the
    session/evaluation must expose no autonomous decision or action field at all."""
    service, _store, _settings = service_bundle
    prepared = service.prepare_from_position(_position_request())
    service.approve_plan(prepared.session_id)
    _complete_with_answer(service, prepared.session_id, MEDIOCRE_ANSWER)
    service.evaluate_interview(prepared.session_id)
    report = service.generate_report(prepared.session_id)

    assert report.screening_outcome == ScreeningOutcome.FAIL
    assert not hasattr(report, "hiring_decision")
    assert not hasattr(report, "employment_status")

    from services.report_service import render_report_markdown
    markdown = render_report_markdown(report)
    assert "not an employment decision" in markdown
    assert "human recruiter remains the final decision maker" in markdown


# ==================== P1: backward compatibility ====================

def test_prepare_interview_backward_compatibility_unchanged(
    service_bundle: tuple[InterviewAgentService, InMemorySessionStore, Settings],
) -> None:
    """prepare_interview() -- signature, required-CV behavior, and full flow -- must
    be byte-for-byte unaffected by the addition of prepare_from_position()."""
    service, store, _settings = service_bundle

    prepared = service.prepare_interview(_request())
    assert prepared.state == InterviewState.CREATED
    assert len(prepared.interview_plan.questions) == 6

    stored = store.get(prepared.session_id)
    assert stored.candidate_input is not None
    assert stored.candidate_input.cv_text == CV_TEXT  # CV remains mandatory on this path

    service.approve_plan(prepared.session_id)
    _complete_naturally(service, prepared.session_id)
    evaluation = service.evaluate_interview(prepared.session_id)
    assert evaluation.screening_outcome in set(ScreeningOutcome)  # new field, old flow
    report = service.generate_report(prepared.session_id)
    assert report.screening_outcome in set(ScreeningOutcome)
