"""Background calling-queue worker entrypoint.

    python worker.py

Runs as its own OS process, deliberately separate from `app.py` (Gradio) and from
the FastAPI server. That separation is the reliability guarantee: a recruiter can
close the dashboard, reload the page, or restart the API, and the screenings in
this process keep running. If this process dies mid-call, the item's lease expires
and another worker picks it up -- nothing is lost and nobody is called twice.

The interview channel defaults to deterministic null/mock mode. Set
INTERVIEW_TRANSPORT=livekit to dispatch the P6 browser voice agent.

Idle passes also pre-warm one auto-pipeline candidate who has applied but not
pressed Start yet (QueueWorker.prewarm_next), so the slow part of preparation
already ran by the time they do. See application/queue_worker.py.

The same process also drains the email outbox (one due send per idle pass)
and sweeps expired invitations -- see application/worker_cycle.py's WorkerCycle,
which orders every pass and is what main() actually runs.
"""
import logging
import os
import signal
import socket
import time
from types import FrameType

from application.email_dispatch_service import EmailDispatchService
from application.interview_agent_service import InterviewAgentService
from application.interview_preparation_service import InterviewPreparationService
from application.position_service import PositionService
from application.queue_worker import QueueWorker, RetryPolicy, WorkerSettings
from application.worker_cycle import WorkerCycle
from config.settings import Settings, load_settings
from services.db.candidates import SQLAlchemyCandidateRepository
from services.db.email_outbox import SQLAlchemyEmailOutboxRepository
from services.db.engine import build_engine, build_session_factory
from services.db.hr_users import SQLAlchemyHRUserRepository
from services.db.interview_plans import SQLAlchemyInterviewPlanRepository
from services.db.invitations import SQLAlchemyInterviewInvitationRepository
from services.db.positions import SQLAlchemyPositionRepository
from services.db.postgres_session_store import PostgresSessionStore
from services.db.questions import SQLAlchemyPositionQuestionRepository
from services.db.queues import SQLAlchemyQueueRepository
from services.email.factory import get_email_service
from services.interview_transport import (
    InterviewTransport,
    LiveKitInterviewTransport,
    NullInterviewTransport,
)
from services.livekit.gateway import LiveKitRoomGateway
from services.logging_service import configure_logging

_logger = logging.getLogger("interview_agent.queue_worker")


def default_worker_id() -> str:
    """Host plus PID: unique per process, and readable in `claimed_by` when
    someone is working out which machine is holding a stuck item."""
    return f"{socket.gethostname()}-{os.getpid()}"


def build_worker(
    settings: Settings | None = None,
    *,
    worker_id: str | None = None,
    transport: InterviewTransport | None = None,
) -> tuple[QueueWorker, object]:
    """Composition root for the interview-queue half of the worker process.
    Returns (worker, engine).

    The engine is returned so the caller can dispose of it on shutdown; it is
    the only resource this process owns beyond the worker itself. See
    build_worker_cycle for the full process (this plus email + invitation
    expiry), which is what main() actually runs.
    """
    resolved = settings or load_settings()
    configure_logging(resolved.log_level)

    engine = build_engine(resolved)
    session_factory = build_session_factory(engine)

    queue_repo = SQLAlchemyQueueRepository(session_factory)
    candidate_repo = SQLAlchemyCandidateRepository(session_factory)
    hr_user_repo = SQLAlchemyHRUserRepository(session_factory)
    position_repo = SQLAlchemyPositionRepository(session_factory)
    question_repo = SQLAlchemyPositionQuestionRepository(session_factory)
    plan_repo = SQLAlchemyInterviewPlanRepository(session_factory)

    agent_service = InterviewAgentService(
        settings=resolved,
        session_store=PostgresSessionStore(session_factory),
    )
    preparation_service = InterviewPreparationService(
        PositionService(position_repo), question_repo, candidate_repo, agent_service, plan_repo,
    )

    worker = QueueWorker(
        queue_repo=queue_repo,
        candidate_repo=candidate_repo,
        hr_user_repo=hr_user_repo,
        position_repo=position_repo,
        plan_repo=plan_repo,
        preparation_service=preparation_service,
        agent_service=agent_service,
        transport=transport or _default_transport(resolved),
        settings=WorkerSettings(
            worker_id=worker_id or default_worker_id(),
            lease_seconds=resolved.queue_worker_lease_seconds,
            poll_interval_seconds=resolved.queue_worker_poll_interval_seconds,
            retry_policy=RetryPolicy(
                base_seconds=resolved.queue_retry_base_seconds,
                factor=resolved.queue_retry_factor,
                max_seconds=resolved.queue_retry_max_seconds,
            ),
        ),
    )
    return worker, engine


def _default_transport(settings: Settings) -> InterviewTransport:
    if settings.interview_transport == "null":
        return NullInterviewTransport()
    return LiveKitInterviewTransport(
        LiveKitRoomGateway(settings),
        agent_name=settings.livekit_agent_name,
        join_timeout_seconds=settings.voice_join_timeout_seconds,
        interview_timeout_seconds=settings.voice_interview_timeout_seconds,
        poll_interval_seconds=settings.voice_transport_poll_interval_seconds,
    )


def build_worker_cycle(
    settings: Settings | None = None,
    *,
    worker_id: str | None = None,
    transport: InterviewTransport | None = None,
) -> tuple[WorkerCycle, object]:
    """Composition root for the full worker process: the interview queue
    (build_worker) plus draining the email outbox and sweeping expired
    invitations, all on the same reliable poll loop. Returns (cycle, engine).
    """
    resolved = settings or load_settings()
    worker, engine = build_worker(resolved, worker_id=worker_id, transport=transport)
    session_factory = build_session_factory(engine)

    email_dispatch = EmailDispatchService(
        SQLAlchemyEmailOutboxRepository(session_factory),
        get_email_service(resolved),
        resolved,
    )
    invitation_repo = SQLAlchemyInterviewInvitationRepository(session_factory)

    cycle = WorkerCycle(
        worker,
        email_dispatch,
        invitation_repo,
        poll_interval_seconds=resolved.queue_worker_poll_interval_seconds,
    )
    return cycle, engine


def main() -> None:
    cycle, engine = build_worker_cycle()
    worker_id = cycle.worker_id
    running = True

    def stop(signum: int, frame: FrameType | None) -> None:
        # Cooperative shutdown: the current item finishes its cycle rather than
        # being abandoned mid-call, and the loop then exits.
        nonlocal running
        running = False
        _logger.info("queue_worker_shutdown_requested worker=%s signal=%s", worker_id, signum)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    _logger.info("queue_worker_started worker=%s", worker_id)
    try:
        cycle.run_forever(time.sleep, should_continue=lambda: running)
    finally:
        engine.dispose()
        _logger.info("queue_worker_stopped worker=%s", worker_id)


if __name__ == "__main__":
    main()
