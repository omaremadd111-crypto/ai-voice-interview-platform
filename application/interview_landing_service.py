"""The public interview-landing flow: verify a durable invitation token, let
the candidate arm their queue item ("Start interview"), and poll for room
readiness.

No owner/session of its own -- reached from api/routers/public.py with no
authentication at all. For the one downstream call that needs a recruiter
identity (minting the existing short-lived voice invitation once the room is
ready), this acts on behalf of the position's owning recruiter, exactly like
QueueWorker._owner_for() already does for the same reason: the durable token
itself is what proves this caller is entitled to this one interview, so it
substitutes for a login the way the worker substitutes for one too.
"""
import hashlib
from collections.abc import Callable
from datetime import datetime, timezone

from pydantic import BaseModel

from application.voice_invite_service import VoiceInviteService, VoiceRoomNotReadyError
from models.common import InvitationStatus, QueueItemStatus
from models.platform import InterviewInvitationRecord, QueueItemRecord
from services.db.candidates import CandidateRepository
from services.db.hr_users import HRUserRepository
from services.db.invitations import InterviewInvitationRepository
from services.db.positions import PositionRepository
from services.db.queues import QueueRepository

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InterviewLandingError(Exception):
    """Base class for interview-landing use-case failures."""


class InvitationNotFoundError(InterviewLandingError):
    """Raised for a token that does not exist, is revoked, or has expired --
    deliberately the same outcome for all three, so a caller can never tell
    which of those is true from the response alone."""


#: What the landing page renders. "not_started" = AWAITING_CANDIDATE (Start is
#: available); "preparing" = armed, worker hasn't attached a live room yet;
#: "ready" = a room is open and voice_invite_url is set, join now;
#: "completed"/"failed" are terminal.
Stage = str

class InterviewLanding(BaseModel):
    position_title: str
    company_name: str
    candidate_first_name: str
    expires_at: datetime
    stage: Stage
    voice_invite_url: str | None = None


class InterviewLandingService:
    def __init__(
        self,
        invitation_repo: InterviewInvitationRepository,
        candidate_repo: CandidateRepository,
        position_repo: PositionRepository,
        queue_repo: QueueRepository,
        hr_user_repo: HRUserRepository,
        voice_invite_service: VoiceInviteService,
        clock: Clock = utc_now,
    ) -> None:
        self._invitation_repo = invitation_repo
        self._candidate_repo = candidate_repo
        self._position_repo = position_repo
        self._queue_repo = queue_repo
        self._hr_user_repo = hr_user_repo
        self._voice_invite_service = voice_invite_service
        self._clock = clock

    def get_landing(self, token: str) -> InterviewLanding:
        invitation, item = self._resolve(token)
        if invitation.id is not None:
            self._invitation_repo.mark_opened(invitation.id, now=self._clock())
        return self._to_landing(invitation, item)

    def start(self, token: str) -> InterviewLanding:
        """Arm the queue item so the worker may claim it. Idempotent: arming
        an item no longer in AWAITING_CANDIDATE (already armed, claimed, mid-
        interview, or settled) is a harmless no-op -- see
        QueueRepository.arm_item -- so this never errors for a double click or
        a page reload after starting."""
        invitation, item = self._resolve(token)
        if item.id is not None:
            self._queue_repo.arm_item(item.id)
        invitation, item = self._resolve(token)
        return self._to_landing(invitation, item)

    def get_status(self, token: str) -> InterviewLanding:
        invitation, item = self._resolve(token)
        return self._to_landing(invitation, item)

    def _resolve(self, token: str) -> tuple[InterviewInvitationRecord, QueueItemRecord]:
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        invitation = self._invitation_repo.get_by_token_hash(token_hash)
        if invitation is None or invitation.status is not InvitationStatus.ACTIVE:
            raise InvitationNotFoundError("This interview link is not available.")
        if invitation.expires_at <= self._clock():
            raise InvitationNotFoundError("This interview link is not available.")
        item = self._queue_repo.get_item(invitation.queue_item_id)
        return invitation, item

    def _to_landing(
        self, invitation: InterviewInvitationRecord, item: QueueItemRecord,
    ) -> InterviewLanding:
        candidate = self._candidate_repo.get(invitation.candidate_id)
        position = self._position_repo.get(invitation.position_id)
        stage, voice_invite_url = self._stage_for(item, position)
        first_name = candidate.full_name.strip().split(" ")[0] if candidate.full_name.strip() else "there"

        return InterviewLanding(
            position_title=position.title,
            company_name=position.company_name,
            candidate_first_name=first_name,
            expires_at=invitation.expires_at,
            stage=stage,
            voice_invite_url=voice_invite_url,
        )

    def _stage_for(self, item: QueueItemRecord, position) -> tuple[Stage, str | None]:
        if item.status is QueueItemStatus.AWAITING_CANDIDATE:
            return "not_started", None
        if item.status is QueueItemStatus.COMPLETED:
            return "completed", None
        if item.status in (QueueItemStatus.FAILED, QueueItemStatus.NO_ANSWER):
            return "failed", None
        if item.status is QueueItemStatus.IN_PROGRESS and item.interview_session_id is not None:
            owner = self._hr_user_repo.get(position.owner_id)
            try:
                voice_invitation = self._voice_invite_service.create_invitation(
                    owner, item.queue_id, item.id,
                )
                return "ready", voice_invitation.invite_url
            except VoiceRoomNotReadyError:
                return "preparing", None
        # PENDING, CLAIMED, or IN_PROGRESS without a session yet: the worker
        # has picked this up but the room is not ready to join.
        return "preparing", None
