"""Candidate repository: interface + PostgreSQL-backed implementation.

CV text/filename stay optional throughout -- a candidate can be added to a
position with just a name, matching the queue/dashboard workflow already
established by PrepareFromPositionRequest in application/dto.py.
"""
from abc import ABC, abstractmethod

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from models.common import CandidateStatus
from models.platform import CandidateRecord
from services.db.orm_models import CandidateRow


class CandidateRepositoryError(Exception):
    """Base class for candidate repository failures."""


class CandidateNotFoundError(CandidateRepositoryError):
    """Raised when a requested candidate id does not exist."""


class CandidateRepository(ABC):
    @abstractmethod
    def create(self, candidate: CandidateRecord) -> CandidateRecord: ...

    @abstractmethod
    def get(self, candidate_id: int) -> CandidateRecord: ...

    @abstractmethod
    def list_for_position(self, position_id: int) -> list[CandidateRecord]: ...

    @abstractmethod
    def update(self, candidate: CandidateRecord) -> CandidateRecord: ...

    @abstractmethod
    def delete(self, candidate_id: int) -> None: ...


class SQLAlchemyCandidateRepository(CandidateRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create(self, candidate: CandidateRecord) -> CandidateRecord:
        with self._session_factory() as session:
            row = CandidateRow(
                position_id=candidate.position_id,
                full_name=candidate.full_name,
                email=candidate.email,
                phone=candidate.phone,
                cv_text=candidate.cv_text,
                cv_filename=candidate.cv_filename,
                status=candidate.status.value,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _to_model(row)

    def get(self, candidate_id: int) -> CandidateRecord:
        with self._session_factory() as session:
            row = session.get(CandidateRow, candidate_id)
            if row is None:
                raise CandidateNotFoundError(f"Candidate '{candidate_id}' was not found")
            return _to_model(row)

    def list_for_position(self, position_id: int) -> list[CandidateRecord]:
        with self._session_factory() as session:
            stmt = select(CandidateRow).where(CandidateRow.position_id == position_id).order_by(CandidateRow.id)
            rows = session.scalars(stmt).all()
            return [_to_model(row) for row in rows]

    def update(self, candidate: CandidateRecord) -> CandidateRecord:
        if candidate.id is None:
            raise ValueError("Cannot update a candidate without an id")
        with self._session_factory() as session:
            row = session.get(CandidateRow, candidate.id)
            if row is None:
                raise CandidateNotFoundError(f"Candidate '{candidate.id}' was not found")
            row.full_name = candidate.full_name
            row.email = candidate.email
            row.phone = candidate.phone
            row.cv_text = candidate.cv_text
            row.cv_filename = candidate.cv_filename
            row.status = candidate.status.value
            session.commit()
            session.refresh(row)
            return _to_model(row)

    def delete(self, candidate_id: int) -> None:
        with self._session_factory() as session:
            row = session.get(CandidateRow, candidate_id)
            if row is None:
                raise CandidateNotFoundError(f"Candidate '{candidate_id}' was not found")
            session.delete(row)
            session.commit()


def _to_model(row: CandidateRow) -> CandidateRecord:
    return CandidateRecord(
        id=row.id,
        position_id=row.position_id,
        full_name=row.full_name,
        email=row.email,
        phone=row.phone,
        cv_text=row.cv_text,
        cv_filename=row.cv_filename,
        status=CandidateStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
