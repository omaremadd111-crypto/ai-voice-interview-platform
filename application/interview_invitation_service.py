"""Durable interview invitation issuance and rotation.

Shared by the two callers that both need exactly the same "give this candidate
a fresh, usable link" operation: application/application_pipeline_service.py
(automatic, right after a candidate is queued) and the recruiter-facing
create-or-resend pipeline-board action (api/routers/pipeline.py). Neither
duplicates this logic.

See IssuedInvitation for the one place the plaintext token exists outside the
HTTP response that returns it -- everywhere else, only its SHA-256 is stored
(services/db/invitations.py).
"""
import hashlib
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from models.common import InvitationStatus
from services.db.candidates import CandidateRepository
from services.db.invitations import InterviewInvitationRepository
from services.db.queues import QueueRepository
from models.platform import InterviewInvitationRecord


class InterviewInvitationServiceError(Exception):
    """Base class for interview invitation use-case failures."""


class CandidateNotQueuedError(InterviewInvitationServiceError):
    """Raised when a candidate has no item in the given queue yet -- an
    invitation with no queue item behind it would just be a dead link."""


class IssuedInvitation(BaseModel):
    token: str
    expires_at: datetime


Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InterviewInvitationService:
    def __init__(
        self,
        invitation_repo: InterviewInvitationRepository,
        candidate_repo: CandidateRepository,
        queue_repo: QueueRepository,
        clock: Clock = utc_now,
    ) -> None:
        self._invitation_repo = invitation_repo
        self._candidate_repo = candidate_repo
        self._queue_repo = queue_repo
        self._clock = clock

    def issue_or_rotate(self, candidate_id: int, *, queue_id: int, ttl_hours: int) -> IssuedInvitation:
        """Revoke any existing active invitation for this candidate and issue a
        fresh one. Never leaves more than one ACTIVE row per candidate --
        matches the partial unique index, which is the actual guarantee; this
        is just what keeps the application-level state consistent with it.
        """
        candidate = self._candidate_repo.get(candidate_id)
        item = self._queue_repo.get_item_for_candidate(queue_id, candidate_id)
        if item is None or item.id is None:
            raise CandidateNotQueuedError(
                f"Candidate {candidate_id} has no item in queue {queue_id} yet."
            )

        now = self._clock()
        existing = self._invitation_repo.get_active_for_candidate(candidate_id)
        if existing is not None and existing.id is not None:
            self._invitation_repo.revoke(existing.id, now=now)

        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        expires_at = now + timedelta(hours=ttl_hours)
        self._invitation_repo.create(InterviewInvitationRecord(
            candidate_id=candidate_id,
            position_id=candidate.position_id,
            queue_item_id=item.id,
            token_hash=token_hash,
            status=InvitationStatus.ACTIVE,
            expires_at=expires_at,
        ))
        return IssuedInvitation(token=token, expires_at=expires_at)
