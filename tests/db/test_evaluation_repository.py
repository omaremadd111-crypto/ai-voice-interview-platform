"""EvaluationRepository CRUD: nullable overall_score, screening outcome, category results."""
import pytest
from sqlalchemy.orm import Session, sessionmaker

from models.common import EvaluationCategory, RecommendationLevel, ScreeningOutcome
from models.evaluation import CategoryEvaluation
from models.platform import EvaluationRecord
from services.db.evaluations import EvaluationNotFoundError, SQLAlchemyEvaluationRepository
from services.db.postgres_session_store import PostgresSessionStore
from tests.db.session_factories import minimal_session

pytestmark = pytest.mark.usefixtures("pg_engine")


@pytest.fixture()
def evaluation_repo(db_session_factory: sessionmaker[Session]) -> SQLAlchemyEvaluationRepository:
    return SQLAlchemyEvaluationRepository(db_session_factory)


@pytest.fixture()
def session_id(db_session_factory: sessionmaker[Session]) -> str:
    store = PostgresSessionStore(db_session_factory)
    session = minimal_session("sess-evaluation-1")
    store.create(session)
    return session.id


def _sufficient_category(score: int = 80) -> CategoryEvaluation:
    return CategoryEvaluation(
        category=EvaluationCategory.TECHNICAL_KNOWLEDGE,
        score=score,
        sufficient_evidence=True,
        reasoning="Candidate demonstrated solid understanding.",
        evidence=["Candidate described hybrid retrieval with reranking."],
        areas_to_validate=[],
    )


def _insufficient_category() -> CategoryEvaluation:
    return CategoryEvaluation(
        category=EvaluationCategory.BEHAVIORAL_COMPETENCIES,
        score=None,
        sufficient_evidence=False,
        reasoning="Insufficient evidence",
        evidence=[],
        areas_to_validate=["Team conflict handling"],
    )


def test_upsert_persists_overall_score_and_category_results(
    evaluation_repo: SQLAlchemyEvaluationRepository, session_id: str,
) -> None:
    record = EvaluationRecord(
        interview_session_id=session_id,
        overall_score=78,
        evidence_coverage=0.8,
        recommendation=RecommendationLevel.STRONG_EVIDENCE_FOR_HUMAN_REVIEW,
        screening_outcome=ScreeningOutcome.PASS,
        rubric_profile="technical",
        category_results=[_sufficient_category(78)],
        strengths=["Strong retrieval architecture knowledge"],
        validation_areas=["Production evaluation methodology"],
        summary="Solid technical interview.",
    )
    evaluation_repo.upsert(record)
    fetched = evaluation_repo.get_for_session(session_id)
    assert fetched.overall_score == 78
    assert fetched.category_results[0].score == 78
    assert fetched.category_results[0].category == EvaluationCategory.TECHNICAL_KNOWLEDGE
    assert fetched.strengths == ["Strong retrieval architecture knowledge"]
    assert fetched.validation_areas == ["Production evaluation methodology"]
    assert fetched.summary == "Solid technical interview."


def test_nullable_overall_score_persists_as_null(
    evaluation_repo: SQLAlchemyEvaluationRepository, session_id: str,
) -> None:
    record = EvaluationRecord(
        interview_session_id=session_id,
        overall_score=None,
        evidence_coverage=0.2,
        recommendation=RecommendationLevel.INSUFFICIENT_EVIDENCE_FROM_INTERVIEW,
        screening_outcome=ScreeningOutcome.NEEDS_REVIEW,
        rubric_profile="technical",
        category_results=[_insufficient_category()],
    )
    evaluation_repo.upsert(record)
    fetched = evaluation_repo.get_for_session(session_id)
    assert fetched.overall_score is None
    assert fetched.category_results[0].score is None
    assert fetched.category_results[0].sufficient_evidence is False


@pytest.mark.parametrize("outcome", list(ScreeningOutcome))
def test_every_screening_outcome_persists_round_trip(
    evaluation_repo: SQLAlchemyEvaluationRepository, session_id: str, outcome: ScreeningOutcome,
) -> None:
    record = EvaluationRecord(
        interview_session_id=session_id,
        overall_score=65 if outcome != ScreeningOutcome.NEEDS_REVIEW else None,
        evidence_coverage=0.9 if outcome != ScreeningOutcome.NEEDS_REVIEW else 0.1,
        recommendation=RecommendationLevel.PROCEED_TO_DEEPER_TECHNICAL_ASSESSMENT,
        screening_outcome=outcome,
        rubric_profile="technical",
        category_results=[_sufficient_category(65)] if outcome != ScreeningOutcome.NEEDS_REVIEW else [],
    )
    evaluation_repo.upsert(record)
    assert evaluation_repo.get_for_session(session_id).screening_outcome == outcome


def test_upsert_twice_updates_the_same_row(
    evaluation_repo: SQLAlchemyEvaluationRepository, session_id: str,
) -> None:
    first = EvaluationRecord(
        interview_session_id=session_id,
        overall_score=50,
        evidence_coverage=0.6,
        recommendation=RecommendationLevel.REQUIRES_ADDITIONAL_VALIDATION,
        screening_outcome=ScreeningOutcome.FAIL,
        rubric_profile="technical",
        category_results=[_sufficient_category(50)],
    )
    first_saved = evaluation_repo.upsert(first)
    second = first.model_copy(update={"overall_score": 90, "screening_outcome": ScreeningOutcome.PASS})
    second_saved = evaluation_repo.upsert(second)

    assert second_saved.id == first_saved.id  # same row updated, not a second row inserted
    fetched = evaluation_repo.get_for_session(session_id)
    assert fetched.overall_score == 90
    assert fetched.screening_outcome == ScreeningOutcome.PASS


def test_get_for_session_returns_none_when_absent(
    evaluation_repo: SQLAlchemyEvaluationRepository, session_id: str,
) -> None:
    assert evaluation_repo.get_for_session(session_id) is None


def test_delete_for_session_removes_evaluation(
    evaluation_repo: SQLAlchemyEvaluationRepository, session_id: str,
) -> None:
    record = EvaluationRecord(
        interview_session_id=session_id,
        overall_score=70,
        evidence_coverage=0.7,
        recommendation=RecommendationLevel.PROCEED_TO_DEEPER_TECHNICAL_ASSESSMENT,
        screening_outcome=ScreeningOutcome.PASS,
        rubric_profile="technical",
        category_results=[_sufficient_category(70)],
    )
    evaluation_repo.upsert(record)
    evaluation_repo.delete_for_session(session_id)
    assert evaluation_repo.get_for_session(session_id) is None


def test_delete_missing_evaluation_raises(evaluation_repo: SQLAlchemyEvaluationRepository) -> None:
    with pytest.raises(EvaluationNotFoundError):
        evaluation_repo.delete_for_session("does-not-exist")
