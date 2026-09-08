"""InterviewLandingService: token verify/arm/status against a real database.

Uses a FakeGateway for the LiveKit half of VoiceInviteService (mirroring
tests/test_voice_invite_service.py's own pattern) so the "ready" stage's real
create_invitation() call is exercised end to end without a live LiveKit
project.
"""
import hashlib
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session, sessionmaker

from application.interview_invitation_service import InterviewInvitationService
from application.interview_landing_service import InterviewLandingService, InvitationNotFoundError
from application.position_service import PositionService
from application.queue_service import QueueService
from application.voice_invite_service import VoiceInviteService, VoiceInviteSigner
from config.settings import Settings
from models.common import QueueItemStatus, QueueKind, QueueStatus
from models.platform import CallQueueRecord, CandidateRecord, HRUser, Position, QueueItemRecord
from services.db.agent_configs import SQLAlchemyAgentConfigRepository
from services.db.candidates import SQLAlchemyCandidateRepository
from services.db.hr_users import SQLAlchemyHRUserRepository
from services.db.interview_plans import SQLAlchemyInterviewPlanRepository
from services.db.invitations import SQLAlchemyInterviewInvitationRepository
from services.db.positions import SQLAlchemyPositionRepository
from services.db.postgres_session_store import PostgresSessionStore
from services.db.queues import SQLAlchemyQueueRepository
from services.db.screening_configs import SQLAlchemyPositionScreeningConfigRepository
from services.livekit.base import LiveKitToken
from tests.db.queue_fixtures import unique_email
from tests.db.session_factories import minimal_session

pytestmark = pytest.mark.usefixtures("pg_engine")


class FakeGateway:
    def close_room(self, room_name: str) -> None:
        return None

    def create_participant_token(self, **kwargs) -> LiveKitToken:
        return LiveKitToken(
            token="room-jwt",
            server_url="wss://example.livekit.cloud",
            room_name=kwargs["room_name"],
            participant_identity=kwargs["participant_identity"],
            participant_name=kwargs["participant_name"],
        )


@pytest.fixture()
def owner(db_session_factory: sessionmaker[Session]) -> HRUser:
    return SQLAlchemyHRUserRepository(db_session_factory).create(HRUser(
        email=unique_email("landing-owner"), password_hash="hashed", full_name="Owner",
    ))


@pytest.fixture()
def position(db_session_factory: sessionmaker[Session], owner: HRUser) -> Position:
    return SQLAlchemyPositionRepository(db_session_factory).create(Position(
        owner_id=owner.id, company_name="Acme", title="Junior AI Engineer",
    ))


@pytest.fixture()
def candidate(db_session_factory: sessionmaker[Session], position: Position) -> CandidateRecord:
    return SQLAlchemyCandidateRepository(db_session_factory).create(CandidateRecord(
        position_id=position.id, full_name="Jordan Rivera",
    ))


@pytest.fixture()
def auto_queue_item(
    db_session_factory: sessionmaker[Session], position: Position, candidate: CandidateRecord,
) -> QueueItemRecord:
    queue_repo = SQLAlchemyQueueRepository(db_session_factory)
    queue = queue_repo.create_queue(CallQueueRecord(
        position_id=position.id, name="Auto queue", status=QueueStatus.RUNNING, kind=QueueKind.AUTO,
    ))
    return queue_repo.add_item(QueueItemRecord(
        queue_id=queue.id, candidate_id=candidate.id, status=QueueItemStatus.AWAITING_CANDIDATE,
    ))


@pytest.fixture()
def invitation_service(db_session_factory: sessionmaker[Session]) -> InterviewInvitationService:
    return InterviewInvitationService(
        SQLAlchemyInterviewInvitationRepository(db_session_factory),
        SQLAlchemyCandidateRepository(db_session_factory),
        SQLAlchemyQueueRepository(db_session_factory),
    )


@pytest.fixture()
def issued_token(
    invitation_service: InterviewInvitationService, auto_queue_item: QueueItemRecord, candidate: CandidateRecord,
) -> str:
    issued = invitation_service.issue_or_rotate(
        candidate.id, queue_id=auto_queue_item.queue_id, ttl_hours=168,
    )
    return issued.token


@pytest.fixture()
def landing_service(db_session_factory: sessionmaker[Session]) -> InterviewLandingService:
    settings = Settings(voice_public_base_url="https://interviews.example", voice_join_timeout_seconds=600)
    position_repo = SQLAlchemyPositionRepository(db_session_factory)
    candidate_repo = SQLAlchemyCandidateRepository(db_session_factory)
    queue_repo = SQLAlchemyQueueRepository(db_session_factory)
    hr_user_repo = SQLAlchemyHRUserRepository(db_session_factory)
    pos_service = PositionService(position_repo, SQLAlchemyPositionScreeningConfigRepository(db_session_factory))
    queue_service = QueueService(
        queue_repo, pos_service, candidate_repo, SQLAlchemyInterviewPlanRepository(db_session_factory),
    )
    voice_invite_service = VoiceInviteService(
        queue_service, queue_repo, candidate_repo, position_repo,
        SQLAlchemyAgentConfigRepository(db_session_factory),
        FakeGateway(), VoiceInviteSigner("test-secret"), settings,
    )
    return InterviewLandingService(
        SQLAlchemyInterviewInvitationRepository(db_session_factory), candidate_repo, position_repo,
        queue_repo, hr_user_repo, voice_invite_service,
    )


def test_get_landing_for_a_fresh_invitation_shows_not_started(
    landing_service: InterviewLandingService, issued_token: str, position: Position,
) -> None:
    landing = landing_service.get_landing(issued_token)
    assert landing.stage == "not_started"
    assert landing.position_title == position.title
    assert landing.company_name == "Acme"
    assert landing.candidate_first_name == "Jordan"
    assert landing.voice_invite_url is None


def test_get_landing_marks_the_invitation_opened(
    landing_service: InterviewLandingService, issued_token: str,
    db_session_factory: sessionmaker[Session],
) -> None:
    landing_service.get_landing(issued_token)
    token_hash = hashlib.sha256(issued_token.encode("ascii")).hexdigest()
    invitation = SQLAlchemyInterviewInvitationRepository(db_session_factory).get_by_token_hash(token_hash)
    assert invitation is not None
    assert invitation.first_opened_at is not None


def test_unknown_token_raises_invitation_not_found(landing_service: InterviewLandingService) -> None:
    with pytest.raises(InvitationNotFoundError):
        landing_service.get_landing("not-a-real-token")


def test_expired_invitation_raises_invitation_not_found(
    landing_service: InterviewLandingService,
    invitation_service: InterviewInvitationService,
    auto_queue_item: QueueItemRecord,
    candidate: CandidateRecord,
) -> None:
    issued = invitation_service.issue_or_rotate(candidate.id, queue_id=auto_queue_item.queue_id, ttl_hours=0)
    # ttl_hours=0 -> expires_at is "now", already in the past by the time we check.
    time.sleep(0.01)
    with pytest.raises(InvitationNotFoundError):
        landing_service.get_landing(issued.token)


def test_start_arms_the_item_and_moves_to_preparing(
    landing_service: InterviewLandingService, issued_token: str,
    db_session_factory: sessionmaker[Session], auto_queue_item: QueueItemRecord,
) -> None:
    landing = landing_service.start(issued_token)
    assert landing.stage == "preparing"
    item = SQLAlchemyQueueRepository(db_session_factory).get_item(auto_queue_item.id)
    assert item.status is QueueItemStatus.PENDING


def test_start_is_idempotent_for_an_already_armed_item(
    landing_service: InterviewLandingService, issued_token: str,
) -> None:
    first = landing_service.start(issued_token)
    second = landing_service.start(issued_token)
    assert first.stage == second.stage == "preparing"


def test_completed_queue_item_reports_completed_stage(
    landing_service: InterviewLandingService, issued_token: str,
    db_session_factory: sessionmaker[Session], auto_queue_item: QueueItemRecord,
) -> None:
    queue_repo = SQLAlchemyQueueRepository(db_session_factory)
    # AWAITING_CANDIDATE is deliberately not claimable -- arm it first, exactly
    # like a candidate pressing Start would.
    queue_repo.arm_item(auto_queue_item.id)
    now = datetime.now(timezone.utc)
    claimed = queue_repo.claim_next_item(
        worker_id="worker-1", now=now, lease_expires_at=now + timedelta(minutes=5),
        queue_id=auto_queue_item.queue_id,
    )
    assert claimed is not None
    queue_repo.mark_terminal(claimed.id, worker_id="worker-1", status=QueueItemStatus.COMPLETED)

    landing = landing_service.get_status(issued_token)
    assert landing.stage == "completed"


def test_in_progress_with_a_session_reports_ready_with_a_voice_invite_url(
    landing_service: InterviewLandingService, issued_token: str,
    db_session_factory: sessionmaker[Session], auto_queue_item: QueueItemRecord,
) -> None:
    # interview_session_id is a real foreign key to interview_sessions.
    PostgresSessionStore(db_session_factory).create(minimal_session("session-xyz"))

    queue_repo = SQLAlchemyQueueRepository(db_session_factory)
    queue_repo.arm_item(auto_queue_item.id)
    now = datetime.now(timezone.utc)
    claimed = queue_repo.claim_next_item(
        worker_id="worker-1", now=now, lease_expires_at=now + timedelta(minutes=5),
        queue_id=auto_queue_item.queue_id,
    )
    assert claimed is not None
    queue_repo.mark_in_progress(claimed.id, worker_id="worker-1")
    queue_repo.attach_session(claimed.id, worker_id="worker-1", interview_session_id="session-xyz")

    landing = landing_service.get_status(issued_token)
    assert landing.stage == "ready"
    assert landing.voice_invite_url is not None
    assert landing.voice_invite_url.startswith("https://interviews.example/voice/")
