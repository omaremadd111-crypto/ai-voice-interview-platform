"""Interview invitation repository: interface + PostgreSQL-backed implementation.

The durable, shareable/emailable candidate link (see
models.platform.InterviewInvitationRecord for why this is a separate concept
from VoiceInviteService's short-lived, room-bound envelope). Looked up ONLY by
token hash -- the plaintext token itself never reaches this layer, see
application/interview_invitation_service.py.

Whether a link is still usable right now is a read-time computation
(status == ACTIVE and expires_at > now): InterviewLandingService._resolve
rejects an expired-but-still-ACTIVE row on its own, so a candidate's
experience never depends on expire_stale having run. expire_stale (P7 phase
3) exists purely for recruiter-visible bookkeeping -- so a dead link reads as
"expired" on the pipeline board instead of looking indistinguishably ACTIVE
forever. Nor does this layer ever flip status to REDEEMED: once the
underlying queue item leaves AWAITING_CANDIDATE, arm_item()'s conditional
update naturally makes a second "Start" a no-op, and the status poll endpoint
reports the queue item's own state -- so a completed interview's invitation
staying ACTIVE causes no double-booking risk, it simply stops being able to
do anything.
"""
from abc import ABC, abstractmethod
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from models.common import InvitationStatus
from models.platform import InterviewInvitationRecord
from services.db.orm_models import InterviewInvitationRow


class InterviewInvitationRepositoryError(Exception):
    """Base class for interview invitation repository failures."""


class InterviewInvitationNotFoundError(InterviewInvitationRepositoryError):
    """Raised when a requested invitation id does not exist."""


class InterviewInvitationRepository(ABC):
    @abstractmethod
    def create(self, invitation: InterviewInvitationRecord) -> InterviewInvitationRecord: ...

    @abstractmethod
    def get_by_token_hash(self, token_hash: str) -> InterviewInvitationRecord | None: ...

    @abstractmethod
    def get_active_for_candidate(self, candidate_id: int) -> InterviewInvitationRecord | None: ...

    @abstractmethod
    def revoke(self, invitation_id: int, *, now: datetime) -> None: ...

    @abstractmethod
    def mark_opened(self, invitation_id: int, *, now: datetime) -> None: ...

    @abstractmethod
    def expire_stale(self, *, now: datetime) -> list[InterviewInvitationRecord]:
        """Bulk-flip every ACTIVE invitation past its expires_at to EXPIRED.
        Bookkeeping only -- see this module's docstring for why nothing about
        a candidate's or recruiter's correctness depends on this having run."""


class SQLAlchemyInterviewInvitationRepository(InterviewInvitationRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create(self, invitation: InterviewInvitationRecord) -> InterviewInvitationRecord:
        with self._session_factory() as session:
            row = InterviewInvitationRow(
                candidate_id=invitation.candidate_id,
                position_id=invitation.position_id,
                queue_item_id=invitation.queue_item_id,
                token_hash=invitation.token_hash,
                status=invitation.status.value,
                expires_at=invitation.expires_at,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _to_model(row)

    def get_by_token_hash(self, token_hash: str) -> InterviewInvitationRecord | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(InterviewInvitationRow).where(InterviewInvitationRow.token_hash == token_hash)
            )
            return _to_model(row) if row is not None else None

    def get_active_for_candidate(self, candidate_id: int) -> InterviewInvitationRecord | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(InterviewInvitationRow).where(
                    InterviewInvitationRow.candidate_id == candidate_id,
                    InterviewInvitationRow.status == InvitationStatus.ACTIVE.value,
                )
            )
            return _to_model(row) if row is not None else None

    def revoke(self, invitation_id: int, *, now: datetime) -> None:
        with self._session_factory() as session:
            row = session.get(InterviewInvitationRow, invitation_id)
            if row is None:
                raise InterviewInvitationNotFoundError(f"Invitation '{invitation_id}' was not found")
            if row.status == InvitationStatus.ACTIVE.value:
                row.status = InvitationStatus.REVOKED.value
                row.revoked_at = now
                session.commit()

    def mark_opened(self, invitation_id: int, *, now: datetime) -> None:
        with self._session_factory() as session:
            row = session.get(InterviewInvitationRow, invitation_id)
            if row is None:
                raise InterviewInvitationNotFoundError(f"Invitation '{invitation_id}' was not found")
            if row.first_opened_at is None:
                row.first_opened_at = now
                session.commit()

    def expire_stale(self, *, now: datetime) -> list[InterviewInvitationRecord]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(InterviewInvitationRow).where(
                    InterviewInvitationRow.status == InvitationStatus.ACTIVE.value,
                    InterviewInvitationRow.expires_at <= now,
                )
            ).all()
            for row in rows:
                row.status = InvitationStatus.EXPIRED.value
            session.commit()
            return [_to_model(row) for row in rows]


def _to_model(row: InterviewInvitationRow) -> InterviewInvitationRecord:
    return InterviewInvitationRecord(
        id=row.id,
        candidate_id=row.candidate_id,
        position_id=row.position_id,
        queue_item_id=row.queue_item_id,
        token_hash=row.token_hash,
        status=InvitationStatus(row.status),
        expires_at=row.expires_at,
        first_opened_at=row.first_opened_at,
        redeemed_at=row.redeemed_at,
        revoked_at=row.revoked_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
