"""Job application repository: interface + PostgreSQL-backed implementation.

One row per (position, normalized email) -- the UNIQUE constraint is the
idempotency guard the pipeline orchestrator relies on so a retried or
resubmitted apply() call is always safe (see
application/application_pipeline_service.py). Rate limiting is computed here
too (count_recent_for_position / count_recent_for_ip) rather than in a
separate counter table: the applications already on file are the ground truth,
so a second bookkeeping structure would only be able to drift from it.
"""
from abc import ABC, abstractmethod
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from models.common import ApplicationState
from models.platform import JobApplicationRecord
from services.db.orm_models import JobApplicationRow


class JobApplicationRepositoryError(Exception):
    """Base class for job application repository failures."""


class JobApplicationNotFoundError(JobApplicationRepositoryError):
    """Raised when a requested application id does not exist."""


class DuplicateApplicationError(JobApplicationRepositoryError):
    """Raised only on a genuine race: two concurrent requests both tried to
    insert the first application for the same (position, email). The caller
    (the pipeline orchestrator) catches this and re-reads the row the other
    request just committed via get_for_position_and_email -- so the caller
    that lost the race still returns a normal, successful result."""


class JobApplicationRepository(ABC):
    @abstractmethod
    def create(self, application: JobApplicationRecord) -> JobApplicationRecord: ...

    @abstractmethod
    def get(self, application_id: int) -> JobApplicationRecord: ...

    @abstractmethod
    def get_for_position_and_email(
        self, position_id: int, email_normalized: str,
    ) -> JobApplicationRecord | None: ...

    @abstractmethod
    def update(self, application: JobApplicationRecord) -> JobApplicationRecord: ...

    @abstractmethod
    def list_for_position(self, position_id: int) -> list[JobApplicationRecord]: ...

    @abstractmethod
    def count_recent_for_position(self, position_id: int, since: datetime) -> int: ...

    @abstractmethod
    def count_recent_for_ip(self, ip_hash: str, since: datetime) -> int: ...


class SQLAlchemyJobApplicationRepository(JobApplicationRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create(self, application: JobApplicationRecord) -> JobApplicationRecord:
        with self._session_factory() as session:
            row = JobApplicationRow(
                position_id=application.position_id,
                email_normalized=application.email_normalized,
                full_name=application.full_name,
                phone=application.phone,
                candidate_id=application.candidate_id,
                pipeline_state=application.pipeline_state.value,
                cv_filename=application.cv_filename,
                cv_parse_error=application.cv_parse_error,
                submitter_ip_hash=application.submitter_ip_hash,
                last_error=application.last_error,
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise DuplicateApplicationError(
                    f"An application already exists for position '{application.position_id}' "
                    f"and this email address"
                ) from exc
            session.refresh(row)
            return _to_model(row)

    def get(self, application_id: int) -> JobApplicationRecord:
        with self._session_factory() as session:
            row = session.get(JobApplicationRow, application_id)
            if row is None:
                raise JobApplicationNotFoundError(f"Application '{application_id}' was not found")
            return _to_model(row)

    def get_for_position_and_email(
        self, position_id: int, email_normalized: str,
    ) -> JobApplicationRecord | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(JobApplicationRow).where(
                    JobApplicationRow.position_id == position_id,
                    JobApplicationRow.email_normalized == email_normalized,
                )
            )
            return _to_model(row) if row is not None else None

    def update(self, application: JobApplicationRecord) -> JobApplicationRecord:
        if application.id is None:
            raise ValueError("Cannot update an application without an id")
        with self._session_factory() as session:
            row = session.get(JobApplicationRow, application.id)
            if row is None:
                raise JobApplicationNotFoundError(f"Application '{application.id}' was not found")
            row.full_name = application.full_name
            row.phone = application.phone
            row.candidate_id = application.candidate_id
            row.pipeline_state = application.pipeline_state.value
            row.cv_filename = application.cv_filename
            row.cv_parse_error = application.cv_parse_error
            row.last_error = application.last_error
            session.commit()
            session.refresh(row)
            return _to_model(row)

    def list_for_position(self, position_id: int) -> list[JobApplicationRecord]:
        with self._session_factory() as session:
            stmt = (
                select(JobApplicationRow)
                .where(JobApplicationRow.position_id == position_id)
                .order_by(JobApplicationRow.id.desc())
            )
            return [_to_model(row) for row in session.scalars(stmt).all()]

    def count_recent_for_position(self, position_id: int, since: datetime) -> int:
        with self._session_factory() as session:
            count = session.scalar(
                select(func.count()).select_from(JobApplicationRow).where(
                    JobApplicationRow.position_id == position_id,
                    JobApplicationRow.created_at >= since,
                )
            )
            return count or 0

    def count_recent_for_ip(self, ip_hash: str, since: datetime) -> int:
        with self._session_factory() as session:
            count = session.scalar(
                select(func.count()).select_from(JobApplicationRow).where(
                    JobApplicationRow.submitter_ip_hash == ip_hash,
                    JobApplicationRow.created_at >= since,
                )
            )
            return count or 0


def _to_model(row: JobApplicationRow) -> JobApplicationRecord:
    return JobApplicationRecord(
        id=row.id,
        position_id=row.position_id,
        email_normalized=row.email_normalized,
        full_name=row.full_name,
        phone=row.phone,
        candidate_id=row.candidate_id,
        pipeline_state=ApplicationState(row.pipeline_state),
        cv_filename=row.cv_filename,
        cv_parse_error=row.cv_parse_error,
        submitter_ip_hash=row.submitter_ip_hash,
        last_error=row.last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
