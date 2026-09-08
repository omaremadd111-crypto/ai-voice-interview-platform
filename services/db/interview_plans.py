"""Candidate interview plan repository: interface + PostgreSQL-backed implementation.

One plan per candidate, stored with its ordered question list as a single unit --
HR edits the plan as a document (reorder/add/delete/edit, then Save), so a
whole-list upsert matches how it is actually used and keeps every save atomic.
"""
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from models.common import InterviewPlanStatus
from models.platform import CandidateInterviewPlan, CandidatePlanQuestion
from services.db.orm_models import CandidateInterviewPlanRow


class InterviewPlanRepositoryError(Exception):
    """Base class for interview plan repository failures."""


class InterviewPlanNotFoundError(InterviewPlanRepositoryError):
    """Raised when a candidate has no interview plan yet."""


class InterviewPlanRepository(ABC):
    @abstractmethod
    def get_for_candidate(self, candidate_id: int) -> CandidateInterviewPlan | None: ...

    @abstractmethod
    def upsert(self, plan: CandidateInterviewPlan) -> CandidateInterviewPlan: ...

    @abstractmethod
    def delete_for_candidate(self, candidate_id: int) -> None: ...


class SQLAlchemyInterviewPlanRepository(InterviewPlanRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_for_candidate(self, candidate_id: int) -> CandidateInterviewPlan | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(CandidateInterviewPlanRow).where(
                    CandidateInterviewPlanRow.candidate_id == candidate_id
                )
            )
            return _to_model(row) if row is not None else None

    def upsert(self, plan: CandidateInterviewPlan) -> CandidateInterviewPlan:
        with self._session_factory() as session:
            row = session.scalar(
                select(CandidateInterviewPlanRow).where(
                    CandidateInterviewPlanRow.candidate_id == plan.candidate_id
                )
            )
            if row is None:
                row = CandidateInterviewPlanRow(candidate_id=plan.candidate_id)
                session.add(row)
            row.status = plan.status.value
            # Persist in explicit order so the stored list is itself the running order.
            row.questions = [
                question.model_dump(mode="json")
                for question in sorted(plan.questions, key=lambda q: q.order)
            ]
            row.generated_at = plan.generated_at
            row.approved_at = (
                plan.approved_at
                if plan.status is InterviewPlanStatus.APPROVED
                # Clearing this on any non-approved save keeps "approved_at" from
                # outliving the approval it refers to.
                else None
            )
            session.commit()
            session.refresh(row)
            return _to_model(row)

    def delete_for_candidate(self, candidate_id: int) -> None:
        with self._session_factory() as session:
            row = session.scalar(
                select(CandidateInterviewPlanRow).where(
                    CandidateInterviewPlanRow.candidate_id == candidate_id
                )
            )
            if row is None:
                raise InterviewPlanNotFoundError(
                    f"Candidate '{candidate_id}' has no interview plan"
                )
            session.delete(row)
            session.commit()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_model(row: CandidateInterviewPlanRow) -> CandidateInterviewPlan:
    return CandidateInterviewPlan(
        id=row.id,
        candidate_id=row.candidate_id,
        status=InterviewPlanStatus(row.status),
        questions=[CandidatePlanQuestion.model_validate(q) for q in row.questions],
        generated_at=row.generated_at,
        approved_at=row.approved_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
