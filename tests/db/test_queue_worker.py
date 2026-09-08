"""QueueWorker: the full claim -> interview -> settle cycle against a real database.

Uses the null transport, so no candidate is contacted and no voice provider is
involved -- exactly the point of P5. What is under test is the *worker*: which
candidate it takes next, what it does when a call fails, whether pausing stops it,
and whether it leaves the database in a state a second worker could pick up.
"""
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from application.interview_agent_service import InterviewAgentService
from application.interview_preparation_service import InterviewPreparationService
from application.position_service import PositionService
from application.queue_worker import QueueWorker, RetryPolicy, WorkerSettings
from config.settings import Settings
from models.common import CandidateStatus, InterviewState, QueueItemStatus, QueueKind
from services.db.candidates import SQLAlchemyCandidateRepository
from services.db.hr_users import SQLAlchemyHRUserRepository
from services.db.interview_plans import SQLAlchemyInterviewPlanRepository
from services.db.positions import SQLAlchemyPositionRepository
from services.db.postgres_session_store import PostgresSessionStore
from services.db.questions import SQLAlchemyPositionQuestionRepository
from services.db.queues import SQLAlchemyQueueRepository
from services.interview_transport import (
    InterviewTransport,
    InterviewTransportContext,
    NullInterviewTransport,
    TransportOutcome,
    TransportResult,
)
from tests.db.queue_fixtures import (
    QueueScenario,
    approve_plan,
    autonomous_factory,
    build_scenario,
)

START = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)


class FrozenClock:
    """A clock the test advances explicitly, so backoff deadlines are exact."""

    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class ExplodingTransport(InterviewTransport):
    """A channel that raises instead of returning a result -- the worker must not die."""

    @property
    def name(self) -> str:
        return "exploding"

    def run(
        self,
        session_id: str,
        submitter: object,
        *,
        context: InterviewTransportContext | None = None,
    ) -> TransportResult:
        del context
        raise RuntimeError("simulated channel crash")


@pytest.fixture()
def factory(pg_engine: Engine) -> sessionmaker[Session]:
    return autonomous_factory(pg_engine)


@pytest.fixture()
def queue_repo(factory: sessionmaker[Session]) -> SQLAlchemyQueueRepository:
    return SQLAlchemyQueueRepository(factory)


@pytest.fixture()
def clock() -> FrozenClock:
    return FrozenClock()


def build_worker(
    factory: sessionmaker[Session],
    *,
    transport: InterviewTransport | None = None,
    clock: FrozenClock,
    worker_id: str = "worker-1",
    lease_seconds: int = 300,
    reports_dir,
) -> QueueWorker:
    settings = Settings(mock_mode=True, reports_dir=reports_dir)
    candidate_repo = SQLAlchemyCandidateRepository(factory)
    position_repo = SQLAlchemyPositionRepository(factory)
    agent_service = InterviewAgentService(
        settings=settings, session_store=PostgresSessionStore(factory),
    )
    preparation_service = InterviewPreparationService(
        PositionService(position_repo),
        SQLAlchemyPositionQuestionRepository(factory),
        candidate_repo,
        agent_service,
        SQLAlchemyInterviewPlanRepository(factory),
    )
    return QueueWorker(
        queue_repo=SQLAlchemyQueueRepository(factory),
        candidate_repo=candidate_repo,
        hr_user_repo=SQLAlchemyHRUserRepository(factory),
        position_repo=position_repo,
        plan_repo=SQLAlchemyInterviewPlanRepository(factory),
        preparation_service=preparation_service,
        agent_service=agent_service,
        transport=transport or NullInterviewTransport(),
        settings=WorkerSettings(
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            retry_policy=RetryPolicy(base_seconds=60, factor=2, max_seconds=600),
        ),
        clock=clock,
    )


@pytest.fixture()
def scenario(factory: sessionmaker[Session]) -> Iterator[QueueScenario]:
    built = build_scenario(factory, label="worker", candidates=2, running=True)
    yield built
    built.cleanup()


def test_worker_screens_a_candidate_end_to_end(
    factory: sessionmaker[Session], scenario: QueueScenario, clock: FrozenClock, tmp_path,
) -> None:
    worker = build_worker(factory, clock=clock, reports_dir=tmp_path)
    processed = worker.process_next()

    assert processed is not None
    assert processed.status is QueueItemStatus.COMPLETED
    assert processed.candidate_id == scenario.candidate_ids[0]
    assert processed.session_id is not None

    item = scenario.queue_repo.get_item(processed.item_id)
    assert item.interview_session_id == processed.session_id
    assert item.claimed_by is None and item.lease_expires_at is None

    # The screening really ran through InterviewAgentService, not a shortcut.
    status = build_worker(factory, clock=clock, reports_dir=tmp_path)._agent_service.get_status(
        processed.session_id,
    )
    assert status.state is InterviewState.EVALUATED
    assert status.total_turns > 0


def test_worker_moves_on_to_the_next_candidate(
    factory: sessionmaker[Session], scenario: QueueScenario, clock: FrozenClock, tmp_path,
) -> None:
    worker = build_worker(factory, clock=clock, reports_dir=tmp_path)
    processed = worker.run_until_idle()

    assert [p.status for p in processed] == [QueueItemStatus.COMPLETED, QueueItemStatus.COMPLETED]
    assert [p.candidate_id for p in processed] == scenario.candidate_ids
    # Nothing left to claim.
    assert worker.process_next() is None


def test_candidate_status_follows_the_screening_not_its_outcome(
    factory: sessionmaker[Session], scenario: QueueScenario, clock: FrozenClock, tmp_path,
) -> None:
    candidate_repo = SQLAlchemyCandidateRepository(factory)
    build_worker(factory, clock=clock, reports_dir=tmp_path).process_next()

    candidate = candidate_repo.get(scenario.candidate_ids[0])
    # SCREENED means "the conversation happened" -- never pass, fail, or hired.
    assert candidate.status is CandidateStatus.SCREENED


def test_a_failed_attempt_never_leaves_a_candidate_stuck_mid_screening(
    factory: sessionmaker[Session], scenario: QueueScenario, clock: FrozenClock, tmp_path,
) -> None:
    """A crash mid-attempt must not leave someone showing as "screening in
    progress" forever -- and never as SCREENED, since no conversation happened."""
    candidate_repo = SQLAlchemyCandidateRepository(factory)
    worker = build_worker(
        factory, transport=ExplodingTransport(), clock=clock, reports_dir=tmp_path,
    )
    processed = worker.process_next()
    assert processed is not None

    candidate = candidate_repo.get(scenario.candidate_ids[0])
    assert candidate.status is CandidateStatus.QUEUED


def test_an_exhausted_candidate_is_returned_to_queued_not_screened(
    factory: sessionmaker[Session], clock: FrozenClock, tmp_path,
) -> None:
    scenario = build_scenario(factory, label="exhaust", candidates=1, max_attempts=1, running=True)
    try:
        worker = build_worker(
            factory,
            transport=NullInterviewTransport(fail_with=TransportOutcome.NO_ANSWER),
            clock=clock,
            reports_dir=tmp_path,
        )
        worker.process_next()
        candidate = SQLAlchemyCandidateRepository(factory).get(scenario.candidate_ids[0])
        assert candidate.status is CandidateStatus.QUEUED
    finally:
        scenario.cleanup()


def test_a_paused_queue_gives_the_worker_nothing_to_do(
    factory: sessionmaker[Session], scenario: QueueScenario, clock: FrozenClock, tmp_path,
) -> None:
    scenario.pause()
    worker = build_worker(factory, clock=clock, reports_dir=tmp_path)
    assert worker.process_next() is None
    assert worker.run_until_idle() == []


def test_resuming_lets_the_worker_continue_where_it_stopped(
    factory: sessionmaker[Session], scenario: QueueScenario, clock: FrozenClock, tmp_path,
) -> None:
    worker = build_worker(factory, clock=clock, reports_dir=tmp_path)
    first = worker.process_next()
    assert first is not None

    scenario.pause()
    assert worker.process_next() is None

    scenario.start()
    second = worker.process_next()
    assert second is not None
    assert second.candidate_id == scenario.candidate_ids[1]
    assert second.status is QueueItemStatus.COMPLETED


def test_a_failed_call_is_retried_after_a_deterministic_backoff(
    factory: sessionmaker[Session], scenario: QueueScenario, clock: FrozenClock, tmp_path,
) -> None:
    failing = NullInterviewTransport(fail_with=TransportOutcome.NO_ANSWER)
    worker = build_worker(factory, transport=failing, clock=clock, reports_dir=tmp_path)

    processed = worker.process_next()
    assert processed is not None
    assert processed.status is QueueItemStatus.PENDING
    assert processed.attempts == 1

    item = scenario.queue_repo.get_item(processed.item_id)
    assert item.next_attempt_at == START + timedelta(seconds=60)
    assert item.last_error is not None

    # Before the deadline the worker skips it and takes the other candidate.
    other = worker.process_next()
    assert other is not None and other.item_id != processed.item_id

    assert worker.process_next() is None
    clock.advance(timedelta(seconds=60))
    retried = worker.process_next()
    assert retried is not None and retried.item_id == processed.item_id
    assert retried.attempts == 2


def test_retries_stop_at_max_attempts_and_settle_as_no_answer(
    factory: sessionmaker[Session], clock: FrozenClock, tmp_path,
) -> None:
    scenario = build_scenario(factory, label="noanswer", candidates=1, max_attempts=3, running=True)
    try:
        failing = NullInterviewTransport(fail_with=TransportOutcome.NO_ANSWER)
        worker = build_worker(factory, transport=failing, clock=clock, reports_dir=tmp_path)

        statuses = []
        for _ in range(3):
            processed = worker.process_next()
            assert processed is not None
            statuses.append(processed.status)
            clock.advance(timedelta(seconds=600))

        assert statuses == [
            QueueItemStatus.PENDING, QueueItemStatus.PENDING, QueueItemStatus.NO_ANSWER,
        ]
        item = scenario.item(0)
        assert item.attempts == item.max_attempts == 3
        # NO_ANSWER stays NO_ANSWER: "we could not reach them" is not "it broke",
        # and neither says anything about the candidate.
        assert item.status is QueueItemStatus.NO_ANSWER
        assert item.next_attempt_at is None
        assert worker.process_next() is None
    finally:
        scenario.cleanup()


def test_a_failed_auto_pipeline_call_returns_to_awaiting_candidate_not_pending(
    factory: sessionmaker[Session], clock: FrozenClock, tmp_path,
) -> None:
    """The one worker change auto-pipeline items get (application/queue_worker.py
    _settle_failure): no timer-based retry, because nothing but the candidate
    pressing Start again can put someone in the room. Regression coverage for
    the manual (kind=MANUAL) path already exists above --
    test_a_failed_call_is_retried_after_a_deterministic_backoff -- and stays
    unaffected because it never touches a kind=AUTO queue."""
    scenario = build_scenario(
        factory, label="auto-retry", candidates=1, max_attempts=2, running=True,
        kind=QueueKind.AUTO,
    )
    try:
        failing = NullInterviewTransport(fail_with=TransportOutcome.NO_ANSWER)
        worker = build_worker(factory, transport=failing, clock=clock, reports_dir=tmp_path)

        processed = worker.process_next()
        assert processed is not None
        assert processed.status is QueueItemStatus.AWAITING_CANDIDATE
        assert processed.attempts == 1

        item = scenario.queue_repo.get_item(processed.item_id)
        assert item.next_attempt_at is None
        assert item.last_error is not None
        assert item.interview_session_id is None

        # Genuinely not claimable again on its own: nothing else to hand out.
        assert worker.process_next() is None

        # Only arming -- the candidate pressing Start again -- makes it
        # claimable, exactly like the very first attempt.
        armed = scenario.queue_repo.arm_item(processed.item_id)
        assert armed.status is QueueItemStatus.PENDING
        retried = worker.process_next()
        assert retried is not None
        assert retried.item_id == processed.item_id
        assert retried.attempts == 2
    finally:
        scenario.cleanup()


def test_a_broken_channel_settles_as_failed_not_no_answer(
    factory: sessionmaker[Session], clock: FrozenClock, tmp_path,
) -> None:
    scenario = build_scenario(factory, label="failed", candidates=1, max_attempts=1, running=True)
    try:
        worker = build_worker(
            factory,
            transport=NullInterviewTransport(fail_with=TransportOutcome.FAILED),
            clock=clock,
            reports_dir=tmp_path,
        )
        processed = worker.process_next()
        assert processed is not None and processed.status is QueueItemStatus.FAILED
    finally:
        scenario.cleanup()


def test_an_exception_in_the_channel_never_stops_the_worker(
    factory: sessionmaker[Session], scenario: QueueScenario, clock: FrozenClock, tmp_path,
) -> None:
    """One broken item must not stall every other queue on the machine."""
    worker = build_worker(
        factory, transport=ExplodingTransport(), clock=clock, reports_dir=tmp_path,
    )
    processed = worker.process_next()

    assert processed is not None
    assert processed.status is QueueItemStatus.PENDING  # scheduled for retry
    assert processed.detail is not None and "RuntimeError" in processed.detail

    # And the worker is still able to take the next candidate.
    healthy = build_worker(factory, clock=clock, reports_dir=tmp_path)
    assert healthy.process_next() is not None


def test_a_candidate_whose_plan_is_no_longer_approved_is_never_called(
    factory: sessionmaker[Session], clock: FrozenClock, tmp_path,
) -> None:
    """The human-in-the-loop gate, re-checked at call time: a recruiter may edit a
    plan after queueing, which returns it to draft."""
    scenario = build_scenario(factory, label="unapproved", candidates=1, running=True)
    try:
        plan_repo = SQLAlchemyInterviewPlanRepository(factory)
        approve_plan(plan_repo, scenario.candidate_ids[0], approved=False)

        worker = build_worker(factory, clock=clock, reports_dir=tmp_path)
        processed = worker.process_next()

        assert processed is not None
        assert processed.status is QueueItemStatus.FAILED
        assert processed.session_id is None
        assert processed.detail is not None and "not approved" in processed.detail
        # No interview session was created for this candidate at all.
        assert scenario.item(0).interview_session_id is None
    finally:
        scenario.cleanup()


def test_two_workers_sharing_a_queue_split_the_candidates(
    factory: sessionmaker[Session], clock: FrozenClock, tmp_path,
) -> None:
    scenario = build_scenario(factory, label="twoworkers", candidates=4, running=True)
    try:
        first = build_worker(factory, clock=clock, worker_id="worker-a", reports_dir=tmp_path)
        second = build_worker(factory, clock=clock, worker_id="worker-b", reports_dir=tmp_path)

        processed = []
        while True:
            worker = first if len(processed) % 2 == 0 else second
            result = worker.process_next()
            if result is None:
                break
            processed.append(result)

        assert len(processed) == 4
        assert len({p.candidate_id for p in processed}) == 4
        assert all(p.status is QueueItemStatus.COMPLETED for p in processed)
    finally:
        scenario.cleanup()


def test_a_crashed_workers_item_is_recovered_and_completed_by_another(
    factory: sessionmaker[Session], clock: FrozenClock, tmp_path,
) -> None:
    """Simulates a worker that claimed a candidate and then died: nothing renews
    the lease, so a healthy worker takes over once it expires."""
    scenario = build_scenario(factory, label="crash", candidates=1, running=True)
    try:
        queue_repo = SQLAlchemyQueueRepository(factory)
        crashed = queue_repo.claim_next_item(
            worker_id="crashed-worker",
            now=clock.now,
            lease_expires_at=clock.now + timedelta(seconds=30),
            queue_id=scenario.queue.id,
        )
        assert crashed is not None
        queue_repo.mark_in_progress(crashed.id, worker_id="crashed-worker")

        healthy = build_worker(factory, clock=clock, worker_id="worker-b", reports_dir=tmp_path)
        # While the lease is live, the item stays with its (dead) owner.
        assert healthy.process_next() is None

        clock.advance(timedelta(seconds=31))
        recovered = healthy.process_next()
        assert recovered is not None
        assert recovered.item_id == crashed.id
        assert recovered.status is QueueItemStatus.COMPLETED
        assert recovered.attempts == 2  # the crashed attempt still counts
    finally:
        scenario.cleanup()


def test_run_until_idle_respects_max_items(
    factory: sessionmaker[Session], scenario: QueueScenario, clock: FrozenClock, tmp_path,
) -> None:
    worker = build_worker(factory, clock=clock, reports_dir=tmp_path)
    processed = worker.run_until_idle(max_items=1)
    assert len(processed) == 1
    assert scenario.item(1).status is QueueItemStatus.PENDING


def test_prewarm_next_prepares_a_session_without_arming_or_claiming(
    factory: sessionmaker[Session], clock: FrozenClock, tmp_path,
) -> None:
    """The candidate-facing side of the pre-warming latency fix: preparation
    may run in the background right after Apply, but the item must stay
    exactly as unclaimable and unstarted as it always was until Start."""
    scenario = build_scenario(
        factory, label="prewarm", candidates=1, running=True,
        kind=QueueKind.AUTO, item_status=QueueItemStatus.AWAITING_CANDIDATE,
    )
    try:
        worker = build_worker(factory, clock=clock, reports_dir=tmp_path)
        prewarmed = worker.prewarm_next()

        assert prewarmed is not None
        assert prewarmed.status is QueueItemStatus.AWAITING_CANDIDATE
        assert prewarmed.interview_session_id is not None

        status = worker._agent_service.get_status(prewarmed.interview_session_id)
        # approve_plan() already ran: the session is READY, not just CREATED.
        assert status.state is InterviewState.READY

        # Still not claimable -- pre-warming must never let the worker treat
        # this as a live attempt before the candidate presses Start.
        assert worker.process_next() is None
    finally:
        scenario.cleanup()


def test_prewarm_next_returns_none_for_a_manual_only_scenario(
    factory: sessionmaker[Session], scenario: QueueScenario, clock: FrozenClock, tmp_path,
) -> None:
    worker = build_worker(factory, clock=clock, reports_dir=tmp_path)
    assert worker.prewarm_next() is None


def test_prewarm_next_leaves_an_unapproved_plan_candidate_alone(
    factory: sessionmaker[Session], clock: FrozenClock, tmp_path,
) -> None:
    """Mirrors _run_item's own "plan not approved" gate: pre-warming must
    never call the LLM analyzers for a candidate whose plan is not ready, and
    must never leave the item claimed or otherwise disturbed -- it is exactly
    as AWAITING_CANDIDATE afterward as claim_for_prewarm found it."""
    scenario = build_scenario(
        factory, label="prewarm-unapproved", candidates=1, running=True, approved=False,
        kind=QueueKind.AUTO, item_status=QueueItemStatus.AWAITING_CANDIDATE,
    )
    try:
        worker = build_worker(factory, clock=clock, reports_dir=tmp_path)
        prewarmed = worker.prewarm_next()

        assert prewarmed is not None
        assert prewarmed.status is QueueItemStatus.AWAITING_CANDIDATE
        assert prewarmed.interview_session_id is None

        # The live path after Start hits the same gate and fails the same way
        # -- pre-warming not having run changes nothing about that outcome.
        scenario.queue_repo.arm_item(prewarmed.id)
        processed = worker.process_next()
        assert processed is not None
        assert processed.status is QueueItemStatus.FAILED
    finally:
        scenario.cleanup()


def test_process_next_reuses_a_prewarmed_session_instead_of_preparing_again(
    factory: sessionmaker[Session], clock: FrozenClock, tmp_path,
) -> None:
    """The other half of the latency fix: once a candidate presses Start, the
    worker must dispatch the session pre-warming already built, not prepare a
    second, orphaned one. Reusing approve_plan() on an already-READY session
    would raise (InterviewAgentService.approve_plan is only valid from
    CREATED), so this test would fail loudly if _run_item ever stopped
    checking interview_session_id before re-preparing."""
    scenario = build_scenario(
        factory, label="prewarm-reuse", candidates=1, running=True,
        kind=QueueKind.AUTO, item_status=QueueItemStatus.AWAITING_CANDIDATE,
    )
    try:
        worker = build_worker(factory, clock=clock, reports_dir=tmp_path)
        prewarmed = worker.prewarm_next()
        assert prewarmed is not None
        prewarmed_session_id = prewarmed.interview_session_id
        assert prewarmed_session_id is not None

        scenario.queue_repo.arm_item(prewarmed.id)
        processed = worker.process_next()

        assert processed is not None
        assert processed.status is QueueItemStatus.COMPLETED
        # The session actually used for the interview is the one pre-warming
        # already prepared and approved -- not a second one prepared live.
        assert processed.session_id == prewarmed_session_id
    finally:
        scenario.cleanup()


def test_run_forever_stops_when_asked_and_sleeps_only_when_idle(
    factory: sessionmaker[Session], scenario: QueueScenario, clock: FrozenClock, tmp_path,
) -> None:
    worker = build_worker(factory, clock=clock, reports_dir=tmp_path)
    sleeps: list[float] = []
    passes = {"count": 0}

    def should_continue() -> bool:
        passes["count"] += 1
        # Two candidates, then one idle pass that sleeps, then stop.
        return passes["count"] <= 4

    worker.run_forever(sleeps.append, should_continue=should_continue)

    assert sleeps, "an idle worker must back off rather than spin"
    assert all(item.status is QueueItemStatus.COMPLETED for item in [scenario.item(0), scenario.item(1)])
