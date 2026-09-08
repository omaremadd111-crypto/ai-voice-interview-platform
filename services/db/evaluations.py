"""Evaluation repository: interface + PostgreSQL-backed implementation.

One row per interview session (interview_session_id is unique). This is the
read-side/general-purpose counterpart to the evaluation persistence
PostgresSessionStore performs internally as part of its own atomic session save.
"""
from abc import ABC, abstractmethod

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from models.common import RecommendationLevel, ScreeningOutcome
from models.evaluation import CategoryEvaluation, HRReport
from models.platform import EvaluationRecord
from services.db.orm_models import EvaluationRow, InterviewSessionRow


class EvaluationRepositoryError(Exception):
    """Base class for evaluation repository failures."""


class EvaluationNotFoundError(EvaluationRepositoryError):
    """Raised when a requested session has no persisted evaluation."""


class EvaluationRepository(ABC):
    @abstractmethod
    def upsert(self, evaluation: EvaluationRecord) -> EvaluationRecord: ...

    @abstractmethod
    def get_for_session(self, session_id: str) -> EvaluationRecord | None: ...

    @abstractmethod
    def get_latest_for_candidate(self, candidate_id: int) -> EvaluationRecord | None: ...

    @abstractmethod
    def delete_for_session(self, session_id: str) -> None: ...


class SQLAlchemyEvaluationRepository(EvaluationRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def upsert(self, evaluation: EvaluationRecord) -> EvaluationRecord:
        with self._session_factory() as session:
            row = session.scalar(
                select(EvaluationRow).where(EvaluationRow.interview_session_id == evaluation.interview_session_id)
            )
            if row is None:
                row = EvaluationRow(interview_session_id=evaluation.interview_session_id)
                session.add(row)
            row.overall_score = evaluation.overall_score
            row.evidence_coverage = evaluation.evidence_coverage
            row.recommendation = evaluation.recommendation.value
            row.screening_outcome = evaluation.screening_outcome.value
            row.rubric_profile = evaluation.rubric_profile
            row.category_results = [ce.model_dump(mode="json") for ce in evaluation.category_results]
            row.strengths = evaluation.strengths
            row.validation_areas = evaluation.validation_areas
            row.summary = evaluation.summary
            row.report_data = evaluation.report.model_dump(mode="json") if evaluation.report is not None else None
            session.commit()
            session.refresh(row)
            return _to_model(row)

    def get_for_session(self, session_id: str) -> EvaluationRecord | None:
        with self._session_factory() as session:
            row = session.scalar(select(EvaluationRow).where(EvaluationRow.interview_session_id == session_id))
            return _to_model(row) if row is not None else None

    def get_latest_for_candidate(self, candidate_id: int) -> EvaluationRecord | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(EvaluationRow)
                .join(
                    InterviewSessionRow,
                    InterviewSessionRow.id == EvaluationRow.interview_session_id,
                )
                .where(InterviewSessionRow.candidate_id == candidate_id)
                .order_by(EvaluationRow.created_at.desc(), EvaluationRow.id.desc())
                .limit(1)
            )
            return _to_model(row) if row is not None else None

    def delete_for_session(self, session_id: str) -> None:
        with self._session_factory() as session:
            row = session.scalar(select(EvaluationRow).where(EvaluationRow.interview_session_id == session_id))
            if row is None:
                raise EvaluationNotFoundError(f"No evaluation found for session '{session_id}'")
            session.delete(row)
            session.commit()


def _to_model(row: EvaluationRow) -> EvaluationRecord:
    return EvaluationRecord(
        id=row.id,
        interview_session_id=row.interview_session_id,
        overall_score=row.overall_score,
        evidence_coverage=row.evidence_coverage,
        recommendation=RecommendationLevel(row.recommendation),
        screening_outcome=ScreeningOutcome(row.screening_outcome),
        rubric_profile=row.rubric_profile,
        category_results=[CategoryEvaluation.model_validate(c) for c in row.category_results],
        strengths=list(row.strengths),
        validation_areas=list(row.validation_areas),
        summary=row.summary,
        report=HRReport.model_validate(row.report_data) if row.report_data is not None else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
