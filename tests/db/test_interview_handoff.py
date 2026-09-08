"""Integration coverage for the Phase 2 candidate handoff: durable invitation
-> Start -> queue arm -> worker prepares and dispatches a LiveKit room ->
InterviewLandingService reports "ready" with a voice invite -- all while the
real QueueWorker and a real LiveKitInterviewTransport are running, exactly as
they do in production (worker.py in one process, the API in another).

Every other landing-service test (tests/db/test_interview_landing_service.py)
fakes the "worker already attached a session" state via direct repository
calls. This file is the one place that drives the ACTUAL worker code
(QueueWorker._run_item -> LiveKitInterviewTransport.run -> gateway.dispatch)
on a background thread while polling the landing service from the main
thread, the same way the browser's poll and the worker's own dispatch race
against each other for real.
"""
import threading
import time

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from application.interview_agent_service import InterviewAgentService
from application.interview_invitation_service import InterviewInvitationService
from application.interview_landing_service import InterviewLandingService
from application.interview_preparation_service import InterviewPreparationService
from application.position_service import PositionService
from application.queue_service import QueueService
from application.queue_worker import QueueWorker, RetryPolicy, WorkerSettings
from application.voice_invite_service import VoiceInviteService, VoiceInviteSigner
from config.settings import Settings
from models.common import QueueItemStatus, QueueKind
from services.db.agent_configs import SQLAlchemyAgentConfigRepository
from services.db.candidates import SQLAlchemyCandidateRepository
from services.db.hr_users import SQLAlchemyHRUserRepository
from services.db.interview_plans import SQLAlchemyInterviewPlanRepository
from services.db.invitations import SQLAlchemyInterviewInvitationRepository
from services.db.positions import SQLAlchemyPositionRepository
from services.db.postgres_session_store import PostgresSessionStore
from services.db.questions import SQLAlchemyPositionQuestionRepository
from services.db.queues import SQLAlchemyQueueRepository
from services.db.screening_configs import SQLAlchemyPositionScreeningConfigRepository
from services.interview_transport.livekit_transport import LiveKitInterviewTransport
from services.livekit.base import LiveKitDispatch, LiveKitToken
from tests.db.queue_fixtures import autonomous_factory, build_scenario

pytestmark = pytest.mark.usefixtures("pg_engine")


class FakeGateway:
    """A LiveKitRoomPort that never touches a real LiveKit project. dispatch()
    records the call but otherwise behaves as if Voice V2 accepted the job
    instantly -- the point of this test is what happens in the DATABASE and
    the landing service once dispatch has happened, not LiveKit itself."""

    def __init__(self) -> None:
        self.dispatched: list[LiveKitDispatch] = []

    def dispatch(self, dispatch: LiveKitDispatch) -> None:
        self.dispatched.append(dispatch)

    def create_participant_token(self, **kwargs) -> LiveKitToken:
        return LiveKitToken(
            token="room-jwt", server_url="wss://example.livekit.cloud",
            room_name=kwargs["room_name"], participant_identity=kwargs["participant_identity"],
            participant_name=kwargs["participant_name"],
        )

    def close_room(self, room_name: str) -> None:
        return None


@pytest.fixture()
def factory(pg_engine: Engine) -> sessionmaker[Session]:
    return autonomous_factory(pg_engine)


def _build_landing_service(factory: sessionmaker[Session], gateway: FakeGateway) -> InterviewLandingService:
    position_repo = SQLAlchemyPositionRepository(factory)
    candidate_repo = SQLAlchemyCandidateRepository(factory)
    queue_repo = SQLAlchemyQueueRepository(factory)
    hr_user_repo = SQLAlchemyHRUserRepository(factory)
    settings = Settings(
        mock_mode=True, voice_public_base_url="https://interviews.example",
        voice_join_timeout_seconds=600,
    )
    pos_service = PositionService(position_repo, SQLAlchemyPositionScreeningConfigRepository(factory))
    queue_service = QueueService(
        queue_repo, pos_service, candidate_repo, SQLAlchemyInterviewPlanRepository(factory),
    )
    voice_invite_service = VoiceInviteService(
        queue_service, queue_repo, candidate_repo, position_repo,
        SQLAlchemyAgentConfigRepository(factory),
        gateway, VoiceInviteSigner("test-secret"), settings,
    )
    return InterviewLandingService(
        SQLAlchemyInterviewInvitationRepository(factory), candidate_repo, position_repo,
        queue_repo, hr_user_repo, voice_invite_service,
    )


def _build_worker(factory: sessionmaker[Session], gateway: FakeGateway, tmp_path) -> QueueWorker:
    settings = Settings(mock_mode=True, reports_dir=tmp_path)
    candidate_repo = SQLAlchemyCandidateRepository(factory)
    position_repo = SQLAlchemyPositionRepository(factory)
    agent_service = InterviewAgentService(settings=settings, session_store=PostgresSessionStore(factory))
    preparation_service = InterviewPreparationService(
        PositionService(position_repo),
        SQLAlchemyPositionQuestionRepository(factory),
        candidate_repo,
        agent_service,
        SQLAlchemyInterviewPlanRepository(factory),
    )
    transport = LiveKitInterviewTransport(
        gateway,
        agent_name="test-voice-agent",
        join_timeout_seconds=3.0,
        interview_timeout_seconds=60.0,
        poll_interval_seconds=0.1,
    )
    return QueueWorker(
        queue_repo=SQLAlchemyQueueRepository(factory),
        candidate_repo=candidate_repo,
        hr_user_repo=SQLAlchemyHRUserRepository(factory),
        position_repo=position_repo,
        plan_repo=SQLAlchemyInterviewPlanRepository(factory),
        preparation_service=preparation_service,
        agent_service=agent_service,
        transport=transport,
        settings=WorkerSettings(
            worker_id="handoff-worker",
            retry_policy=RetryPolicy(base_seconds=60, factor=2, max_seconds=600),
        ),
    )


def test_the_landing_page_sees_ready_while_the_worker_is_still_waiting_for_a_candidate(
    factory: sessionmaker[Session], tmp_path,
) -> None:
    """Reproduces the reported handoff end to end: arm the item exactly like
    pressing Start does, let the REAL worker claim it, prepare it, and dispatch
    a (fake) LiveKit room -- then poll the REAL landing service from a second
    thread, exactly like the candidate's browser does, and confirm it observes
    "ready" with a voice_invite_url during the window between dispatch and the
    candidate actually joining (which never happens here -- nothing calls
    start_interview(), so the transport gives up at its 3-second join timeout,
    same as a real candidate who never joins)."""
    scenario = build_scenario(
        factory, label="handoff", candidates=1, running=True,
        kind=QueueKind.AUTO, item_status=QueueItemStatus.AWAITING_CANDIDATE,
    )
    try:
        candidate_id = scenario.candidate_ids[0]
        invitation_service = InterviewInvitationService(
            SQLAlchemyInterviewInvitationRepository(factory),
            SQLAlchemyCandidateRepository(factory),
            SQLAlchemyQueueRepository(factory),
        )
        issued = invitation_service.issue_or_rotate(candidate_id, queue_id=scenario.queue.id, ttl_hours=1)

        gateway = FakeGateway()
        landing_service = _build_landing_service(factory, gateway)

        started = landing_service.start(issued.token)
        assert started.stage == "preparing"

        worker = _build_worker(factory, gateway, tmp_path)
        worker_result: list = []
        worker_thread = threading.Thread(target=lambda: worker_result.append(worker.process_next()))
        worker_thread.start()

        observed_stages: list[str] = []
        ready_landing = None
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            landing = landing_service.get_status(issued.token)
            observed_stages.append(landing.stage)
            if landing.stage == "ready":
                ready_landing = landing
                break
            time.sleep(0.05)

        worker_thread.join(timeout=10.0)
        assert not worker_thread.is_alive(), "the worker never finished processing the item"
        assert worker_result and worker_result[0] is not None

        assert gateway.dispatched, "the worker never dispatched the LiveKit room"
        assert ready_landing is not None, (
            f"the landing page never observed 'ready' after dispatch; "
            f"observed stages were {observed_stages!r}"
        )
        assert ready_landing.voice_invite_url is not None
        assert ready_landing.voice_invite_url.startswith("https://interviews.example/voice/")
    finally:
        scenario.cleanup()


def test_prewarming_before_start_never_leaks_into_the_landing_page_and_speeds_up_the_real_start(
    factory: sessionmaker[Session], tmp_path,
) -> None:
    """The latency half of the fix, end to end: background preparation right
    after Apply (before Start) must stay completely invisible to the
    candidate -- still "not_started", no dispatched room -- and once they do
    press Start, the worker must reuse that prepared session (reaching
    "ready" without a second prepare/approve round trip) rather than
    preparing a second, orphaned one."""
    scenario = build_scenario(
        factory, label="prewarm-handoff", candidates=1, running=True,
        kind=QueueKind.AUTO, item_status=QueueItemStatus.AWAITING_CANDIDATE,
    )
    try:
        candidate_id = scenario.candidate_ids[0]
        invitation_service = InterviewInvitationService(
            SQLAlchemyInterviewInvitationRepository(factory),
            SQLAlchemyCandidateRepository(factory),
            SQLAlchemyQueueRepository(factory),
        )
        issued = invitation_service.issue_or_rotate(candidate_id, queue_id=scenario.queue.id, ttl_hours=1)

        gateway = FakeGateway()
        landing_service = _build_landing_service(factory, gateway)
        worker = _build_worker(factory, gateway, tmp_path)

        # Apply-time background preparation -- the candidate has not seen the
        # landing page yet, let alone pressed Start.
        prewarmed = worker.prewarm_next()
        assert prewarmed is not None
        prewarmed_session_id = prewarmed.interview_session_id
        assert prewarmed_session_id is not None

        before_start = landing_service.get_landing(issued.token)
        assert before_start.stage == "not_started"
        assert before_start.voice_invite_url is None
        assert not gateway.dispatched, "pre-warming must never dispatch a LiveKit room"

        # Now the candidate actually presses Start.
        started = landing_service.start(issued.token)
        assert started.stage == "preparing"

        worker_result: list = []
        worker_thread = threading.Thread(target=lambda: worker_result.append(worker.process_next()))
        worker_thread.start()

        ready_landing = None
        session_id_while_ready = None
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            landing = landing_service.get_status(issued.token)
            if landing.stage == "ready":
                ready_landing = landing
                # Read straight from the queue item at the exact moment the
                # landing page sees "ready" -- worker_result below reflects
                # the join timeout that follows (nothing in this test ever
                # calls start_interview(), same as the other handoff test),
                # which correctly clears interview_session_id on its way back
                # to AWAITING_CANDIDATE. What matters here is which session
                # was actually live during the ready window.
                session_id_while_ready = scenario.queue_repo.get_item(prewarmed.id).interview_session_id
                break
            time.sleep(0.05)

        worker_thread.join(timeout=10.0)
        assert not worker_thread.is_alive(), "the worker never finished processing the item"
        assert worker_result and worker_result[0] is not None

        assert ready_landing is not None, "the landing page never observed 'ready' after Start"
        assert ready_landing.voice_invite_url is not None
        assert session_id_while_ready == prewarmed_session_id, (
            "the live attempt prepared a second session instead of reusing the pre-warmed one"
        )
    finally:
        scenario.cleanup()
