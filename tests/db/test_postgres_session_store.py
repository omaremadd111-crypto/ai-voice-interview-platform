"""PostgresSessionStore: full serialization round trip, optimistic locking, isolation.

Mirrors the scenarios InMemorySessionStore is held to in tests/test_session_service.py,
plus Postgres-specific proof that a real JSONB/relational round trip is lossless and
that a stale write is rejected rather than silently overwriting newer state. See
test_session_store_parity.py for the shared-contract tests run against both stores.
"""
import pytest
from sqlalchemy.orm import Session, sessionmaker

from models.candidate import CandidateAnalysis, CandidateInput, FitAnalysis
from models.common import (
    EvaluationCategory,
    InterviewState,
    QuestionCategory,
    RecommendationLevel,
    ScreeningOutcome,
)
from models.evaluation import CategoryEvaluation, HRReport, InterviewEvaluation
from models.interview import InterviewPlan, InterviewQuestion, InterviewTurn
from models.job import JobAnalysis, JobInput
from services.db.postgres_session_store import PostgresSessionStore
from services.session_service import SessionAlreadyExistsError, SessionNotFoundError, StaleSessionVersionError
from tests.db.session_factories import minimal_session

pytestmark = pytest.mark.usefixtures("pg_engine")


@pytest.fixture()
def store(db_session_factory: sessionmaker[Session]) -> PostgresSessionStore:
    return PostgresSessionStore(db_session_factory)


def test_create_and_get_round_trip_minimal_session(store: PostgresSessionStore) -> None:
    original = minimal_session("sess-min-1")
    store.create(original)
    fetched = store.get("sess-min-1")
    assert fetched.company == "Acme"
    assert fetched.candidate_input.full_name == "Jordan Rivera"
    assert fetched.state == InterviewState.CREATED
    assert fetched.version == 0


def test_create_duplicate_id_raises(store: PostgresSessionStore) -> None:
    store.create(minimal_session("sess-dup"))
    with pytest.raises(SessionAlreadyExistsError):
        store.create(minimal_session("sess-dup"))


def test_get_missing_session_raises(store: PostgresSessionStore) -> None:
    with pytest.raises(SessionNotFoundError):
        store.get("does-not-exist")


def test_save_unknown_session_raises(store: PostgresSessionStore) -> None:
    with pytest.raises(SessionNotFoundError):
        store.save(minimal_session("never-created"))


def _fully_populated_session(session_id: str) -> object:
    session = minimal_session(
        session_id,
        job_input=JobInput(
            company_name="Acme", job_title="AI Engineer", description_text="Build RAG systems with Python.",
        ),
        job_analysis=JobAnalysis(
            job_title="AI Engineer",
            experience_level="Junior",
            required_skills=["Python", "SQL"],
            technical_topics=["retrieval", "vector databases"],
            role_summary="Builds retrieval-augmented systems.",
        ),
        candidate_analysis=CandidateAnalysis(
            full_name="Jordan Rivera",
            skills=["Python", "Docker"],
            important_cv_claims=["Built a production RAG application."],
        ),
        fit_analysis=FitAnalysis(
            strong_alignment_areas=["Python", "retrieval"],
            questions_to_investigate=["Production evaluation methodology"],
        ),
    )
    return session


def test_full_analysis_fields_round_trip_through_jsonb(store: PostgresSessionStore) -> None:
    original = _fully_populated_session("sess-full-1")
    store.create(original)
    fetched = store.get("sess-full-1")

    assert fetched.job_input.description_text == "Build RAG systems with Python."
    assert fetched.job_analysis.required_skills == ["Python", "SQL"]
    assert fetched.job_analysis.technical_topics == ["retrieval", "vector databases"]
    assert fetched.candidate_analysis.skills == ["Python", "Docker"]
    assert fetched.candidate_analysis.important_cv_claims == ["Built a production RAG application."]
    assert fetched.fit_analysis.strong_alignment_areas == ["Python", "retrieval"]
    assert fetched.fit_analysis.questions_to_investigate == ["Production evaluation methodology"]
    assert fetched.interview_plan.questions[0].id == "q1"


def test_transcript_round_trips_with_ordering_and_follow_up_flag(store: PostgresSessionStore) -> None:
    original = minimal_session("sess-transcript-store-1")
    store.create(original)
    fetched = store.get("sess-transcript-store-1")
    fetched.transcript.turns = [
        InterviewTurn(
            question_id="q1", question="Explain your project.", category=QuestionCategory.TECHNICAL,
            answer="I built a Python API.", is_follow_up=False, timestamp="2026-08-19T00:00:01+00:00",
        ),
        InterviewTurn(
            question_id="q1", question="How did you test it?", category=QuestionCategory.TECHNICAL,
            answer="With pytest and integration tests.", is_follow_up=True,
            timestamp="2026-08-19T00:00:02+00:00",
        ),
    ]
    store.save(fetched)

    refetched = store.get("sess-transcript-store-1")
    assert [t.answer for t in refetched.transcript.turns] == [
        "I built a Python API.", "With pytest and integration tests.",
    ]
    assert refetched.transcript.turns[0].is_follow_up is False
    assert refetched.transcript.turns[1].is_follow_up is True
    assert refetched.transcript.turns[1].question_id == "q1"
    assert refetched.transcript.turns[0].timestamp == "2026-08-19T00:00:01+00:00"


def test_evaluation_with_null_overall_score_round_trips(store: PostgresSessionStore) -> None:
    original = minimal_session("sess-eval-null-1")
    store.create(original)
    fetched = store.get("sess-eval-null-1")
    fetched.evaluation = InterviewEvaluation(
        category_evaluations=[
            CategoryEvaluation(
                category=EvaluationCategory.TECHNICAL_KNOWLEDGE,
                score=None, sufficient_evidence=False, reasoning="Insufficient evidence",
                evidence=[], areas_to_validate=["Retrieval architecture"],
            ),
        ],
        overall_score=None,
        evidence_coverage=0.1,
        recommendation=RecommendationLevel.INSUFFICIENT_EVIDENCE_FROM_INTERVIEW,
        screening_outcome=ScreeningOutcome.NEEDS_REVIEW,
        rubric_profile="technical",
    )
    store.save(fetched)

    refetched = store.get("sess-eval-null-1")
    assert refetched.evaluation.overall_score is None
    assert refetched.evaluation.screening_outcome == ScreeningOutcome.NEEDS_REVIEW
    assert refetched.evaluation.category_evaluations[0].score is None
    assert refetched.evaluation.category_evaluations[0].sufficient_evidence is False


def test_evaluation_and_report_round_trip_together(store: PostgresSessionStore) -> None:
    original = minimal_session("sess-eval-report-1")
    store.create(original)
    fetched = store.get("sess-eval-report-1")
    fetched.evaluation = InterviewEvaluation(
        category_evaluations=[
            CategoryEvaluation(
                category=EvaluationCategory.TECHNICAL_KNOWLEDGE,
                score=82, sufficient_evidence=True, reasoning="Solid understanding.",
                evidence=["Described hybrid retrieval with reranking."], areas_to_validate=[],
            ),
        ],
        overall_score=82,
        evidence_coverage=0.9,
        recommendation=RecommendationLevel.STRONG_EVIDENCE_FOR_HUMAN_REVIEW,
        screening_outcome=ScreeningOutcome.PASS,
        rubric_profile="technical",
    )
    fetched.report = HRReport(
        session_id=fetched.id,
        company="Acme",
        role="AI Engineer",
        candidate_name="Jordan Rivera",
        candidate_overview="Strong technical background.",
        interview_summary="Candidate performed well overall.",
        overall_score=82,
        evidence_coverage=0.9,
        category_scores=fetched.evaluation.category_evaluations,
        strengths=["Deep retrieval knowledge"],
        areas_requiring_validation=["Production monitoring experience"],
        recommendation=RecommendationLevel.STRONG_EVIDENCE_FOR_HUMAN_REVIEW,
        screening_outcome=ScreeningOutcome.PASS,
        llm_provider="mock",
        is_mock=True,
        generated_at="2026-08-19T01:00:00+00:00",
    )
    store.save(fetched)

    refetched = store.get("sess-eval-report-1")
    assert refetched.report is not None
    assert refetched.report.candidate_name == "Jordan Rivera"
    assert refetched.report.strengths == ["Deep retrieval knowledge"]
    assert refetched.report.areas_requiring_validation == ["Production monitoring experience"]
    assert refetched.report.interview_summary == "Candidate performed well overall."
    assert refetched.report.recommendation == RecommendationLevel.STRONG_EVIDENCE_FOR_HUMAN_REVIEW


def test_save_increments_version_and_mutates_caller_object_in_place(store: PostgresSessionStore) -> None:
    store.create(minimal_session("sess-version-1"))
    fetched = store.get("sess-version-1")
    assert fetched.version == 0

    fetched.company = "Updated Co"
    store.save(fetched)
    assert fetched.version == 1  # mutated in place after a successful save

    refetched = store.get("sess-version-1")
    assert refetched.version == 1
    assert refetched.company == "Updated Co"


def test_stale_write_is_rejected_and_does_not_overwrite_newer_state(store: PostgresSessionStore) -> None:
    store.create(minimal_session("sess-stale-1"))
    reader_a = store.get("sess-stale-1")
    reader_b = store.get("sess-stale-1")

    reader_a.company = "Writer A wins"
    store.save(reader_a)  # version 0 -> 1, succeeds

    reader_b.company = "Writer B loses"
    with pytest.raises(StaleSessionVersionError):
        store.save(reader_b)  # still holds version 0 -- rejected

    current = store.get("sess-stale-1")
    assert current.company == "Writer A wins"
    assert current.version == 1


def test_two_postgres_sessions_never_share_candidate_state(store: PostgresSessionStore) -> None:
    store.create(minimal_session("sess-iso-a", candidate_input=CandidateInput(
        full_name="Alpha", cv_text="Alpha-only project details.",
    )))
    store.create(minimal_session("sess-iso-b", candidate_input=CandidateInput(
        full_name="Beta", cv_text="Beta-only project details.",
    )))

    session_a = store.get("sess-iso-a")
    session_a.candidate_input.cv_text = "Mutated locally, not saved"
    fetched_b = store.get("sess-iso-b")

    assert "Beta-only" in fetched_b.candidate_input.cv_text
    assert "Alpha" not in fetched_b.candidate_input.cv_text
    assert store.get("sess-iso-a").candidate_input.cv_text == "Alpha-only project details."
