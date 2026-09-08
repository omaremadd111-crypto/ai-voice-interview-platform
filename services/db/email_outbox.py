"""Email outbox repository: interface + PostgreSQL-backed implementation.

Mirrors services/db/queues.py's shape for the same reason: a background pass
needs to atomically claim one due row without two workers ever sending the
same email twice. The claim primitive here is simpler than the queue's --
see models/common.py EmailOutboxStatus for why no CLAIMED/IN_PROGRESS status
or lease is needed.
"""
from abc import ABC, abstractmethod
from datetime import datetime, timedelta

from sqlalchemy import Select, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from models.common import EmailOutboxStatus
from models.platform import EmailOutboxRecord
from services.db.orm_models import EmailOutboxRow


class EmailOutboxRepositoryError(Exception):
    """Base class for email outbox repository failures."""


class EmailOutboxNotFoundError(EmailOutboxRepositoryError):
    """Raised when a requested email_outbox id does not exist."""


class EmailOutboxRepository(ABC):
    @abstractmethod
    def enqueue(self, record: EmailOutboxRecord) -> EmailOutboxRecord:
        """Insert a new row, or return the existing one for the same
        idempotency_key. Never raises for a duplicate enqueue -- see
        application/email_dispatch_service.py."""

    @abstractmethod
    def get(self, outbox_id: int) -> EmailOutboxRecord: ...

    @abstractmethod
    def get_by_idempotency_key(self, idempotency_key: str) -> EmailOutboxRecord | None: ...

    @abstractmethod
    def claim_due(self, *, now: datetime, reserve_seconds: int) -> EmailOutboxRecord | None:
        """Atomically take ownership of one due PENDING row (SELECT ... FOR
        UPDATE SKIP LOCKED), reserving it by pushing next_attempt_at
        reserve_seconds into the future and consuming one attempt -- so a
        worker that crashes mid-send leaves the row to become due again on
        its own, rather than stuck forever. Status stays PENDING throughout;
        only mark_sent/mark_failed make it terminal."""

    @abstractmethod
    def mark_sent(
        self, outbox_id: int, *, now: datetime, provider_message_id: str | None,
    ) -> EmailOutboxRecord:
        """Settle a successful send. Clears payload -- the plaintext token it
        carried has done its job and should not linger in this table."""

    @abstractmethod
    def mark_retry(self, outbox_id: int, *, next_attempt_at: datetime, error: str) -> EmailOutboxRecord: ...

    @abstractmethod
    def mark_failed(self, outbox_id: int, *, error: str) -> EmailOutboxRecord:
        """Settle an exhausted send. Clears payload, same reasoning as mark_sent."""


class SQLAlchemyEmailOutboxRepository(EmailOutboxRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def enqueue(self, record: EmailOutboxRecord) -> EmailOutboxRecord:
        with self._session_factory() as session:
            row = EmailOutboxRow(
                idempotency_key=record.idempotency_key,
                to_email=record.to_email,
                template=record.template,
                payload=record.payload,
                status=record.status.value,
                attempts=record.attempts,
                max_attempts=record.max_attempts,
                next_attempt_at=record.next_attempt_at,
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.scalar(
                    select(EmailOutboxRow).where(
                        EmailOutboxRow.idempotency_key == record.idempotency_key
                    )
                )
                if existing is None:
                    raise
                return _to_model(existing)
            session.refresh(row)
            return _to_model(row)

    def get(self, outbox_id: int) -> EmailOutboxRecord:
        with self._session_factory() as session:
            return _to_model(_require(session, outbox_id))

    def get_by_idempotency_key(self, idempotency_key: str) -> EmailOutboxRecord | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(EmailOutboxRow).where(EmailOutboxRow.idempotency_key == idempotency_key)
            )
            return _to_model(row) if row is not None else None

    def claim_due(self, *, now: datetime, reserve_seconds: int) -> EmailOutboxRecord | None:
        with self._session_factory() as session:
            row = session.scalars(_due_query(now)).first()
            if row is None:
                return None
            row.attempts += 1
            row.next_attempt_at = now + timedelta(seconds=reserve_seconds)
            session.commit()
            session.refresh(row)
            return _to_model(row)

    def mark_sent(
        self, outbox_id: int, *, now: datetime, provider_message_id: str | None,
    ) -> EmailOutboxRecord:
        with self._session_factory() as session:
            session.execute(
                update(EmailOutboxRow)
                .where(EmailOutboxRow.id == outbox_id)
                .values(
                    status=EmailOutboxStatus.SENT.value,
                    sent_at=now,
                    provider_message_id=provider_message_id,
                    next_attempt_at=None,
                    payload={},
                )
            )
            session.commit()
            return _to_model(_require(session, outbox_id))

    def mark_retry(self, outbox_id: int, *, next_attempt_at: datetime, error: str) -> EmailOutboxRecord:
        with self._session_factory() as session:
            session.execute(
                update(EmailOutboxRow)
                .where(EmailOutboxRow.id == outbox_id)
                .values(
                    status=EmailOutboxStatus.PENDING.value,
                    next_attempt_at=next_attempt_at,
                    last_error=error,
                )
            )
            session.commit()
            return _to_model(_require(session, outbox_id))

    def mark_failed(self, outbox_id: int, *, error: str) -> EmailOutboxRecord:
        with self._session_factory() as session:
            session.execute(
                update(EmailOutboxRow)
                .where(EmailOutboxRow.id == outbox_id)
                .values(
                    status=EmailOutboxStatus.FAILED.value,
                    next_attempt_at=None,
                    last_error=error,
                    payload={},
                )
            )
            session.commit()
            return _to_model(_require(session, outbox_id))


def _due_query(now: datetime) -> Select[tuple[EmailOutboxRow]]:
    return (
        select(EmailOutboxRow)
        .where(
            EmailOutboxRow.status == EmailOutboxStatus.PENDING.value,
            or_(EmailOutboxRow.next_attempt_at.is_(None), EmailOutboxRow.next_attempt_at <= now),
        )
        .order_by(EmailOutboxRow.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def _require(session: Session, outbox_id: int) -> EmailOutboxRow:
    row = session.get(EmailOutboxRow, outbox_id)
    if row is None:
        raise EmailOutboxNotFoundError(f"Email outbox row '{outbox_id}' was not found")
    return row


def _to_model(row: EmailOutboxRow) -> EmailOutboxRecord:
    return EmailOutboxRecord(
        id=row.id,
        idempotency_key=row.idempotency_key,
        to_email=row.to_email,
        template=row.template,
        payload=row.payload,
        status=EmailOutboxStatus(row.status),
        attempts=row.attempts,
        max_attempts=row.max_attempts,
        next_attempt_at=row.next_attempt_at,
        sent_at=row.sent_at,
        provider_message_id=row.provider_message_id,
        last_error=row.last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
