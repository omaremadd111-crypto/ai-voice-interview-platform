"""Atomic claiming, lease expiry, and the duplicate-call guard.

These run against a real PostgreSQL instance on real, separate connections --
FOR UPDATE SKIP LOCKED has no meaning inside a single shared transaction, so the
shared `db_session_factory` fixture is deliberately not used here.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from models.common import QueueItemStatus, QueueStatus
from models.platform import CallQueueRecord, QueueItemRecord
from services.db.queues import LeaseLostError, QueueItemNotFoundError, SQLAlchemyQueueRepository
from tests.db.queue_fixtures import QueueScenario, autonomous_factory, build_scenario

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
LEASE = timedelta(minutes=5)


@pytest.fixture()
def factory(pg_engine: Engine) -> sessionmaker[Session]:
    return autonomous_factory(pg_engine)


@pytest.fixture()
def repo(factory: sessionmaker[Session]) -> SQLAlchemyQueueRepository:
    return SQLAlchemyQueueRepository(factory)


@pytest.fixture()
def scenario(factory: sessionmaker[Session]) -> Iterator[QueueScenario]:
    built = build_scenario(factory, label="claim", candidates=3, running=True)
    yield built
    built.cleanup()


def claim(repo: SQLAlchemyQueueRepository, worker_id: str, queue_id: int, now: datetime = NOW):
    return repo.claim_next_item(
        worker_id=worker_id, now=now, lease_expires_at=now + LEASE, queue_id=queue_id,
    )


def test_claiming_takes_the_oldest_pending_item_first(
    repo: SQLAlchemyQueueRepository, scenario: QueueScenario,
) -> None:
    claimed = claim(repo, "worker-1", scenario.queue.id)
    assert claimed is not None
    assert claimed.id == scenario.item_ids[0]
    assert claimed.status is QueueItemStatus.CLAIMED
    assert claimed.claimed_by == "worker-1"
    assert claimed.lease_expires_at is not None


def test_claiming_consumes_an_attempt_up_front(
    repo: SQLAlchemyQueueRepository, scenario: QueueScenario,
) -> None:
    """A worker that dies mid-call must not be able to retry forever, so the
    attempt is spent at claim time rather than on completion."""
    claimed = claim(repo, "worker-1", scenario.queue.id)
    assert claimed is not None and claimed.attempts == 1


def test_sequential_claims_never_return_the_same_item(
    repo: SQLAlchemyQueueRepository, scenario: QueueScenario,
) -> None:
    ids = []
    for index in range(3):
        claimed = claim(repo, f"worker-{index}", scenario.queue.id)
        assert claimed is not None
        ids.append(claimed.id)
    assert sorted(ids) == sorted(scenario.item_ids)
    assert claim(repo, "worker-4", scenario.queue.id) is None


def test_concurrent_workers_each_get_a_different_candidate(
    factory: sessionmaker[Session], scenario: QueueScenario,
) -> None:
    """The core concurrency guarantee: three workers racing on the same queue at
    the same instant produce three distinct claims and zero double-calls."""

    def worker(worker_index: int) -> int | None:
        # A separate repository per thread, so each runs on its own connection --
        # otherwise the race being tested would not exist.
        own_repo = SQLAlchemyQueueRepository(factory)
        claimed = claim(own_repo, f"worker-{worker_index}", scenario.queue.id)
        return None if claimed is None else claimed.id

    with ThreadPoolExecutor(max_workers=3) as pool:
        claimed_ids = list(pool.map(worker, range(3)))

    assert None not in claimed_ids, "a worker came away empty-handed despite pending items"
    assert len(set(claimed_ids)) == 3, f"the same item was claimed twice: {claimed_ids}"
    assert sorted(claimed_ids) == sorted(scenario.item_ids)


def test_more_workers_than_items_leaves_the_extras_empty_handed(
    factory: sessionmaker[Session], scenario: QueueScenario,
) -> None:
    def worker(worker_index: int) -> int | None:
        claimed = claim(SQLAlchemyQueueRepository(factory), f"worker-{worker_index}", scenario.queue.id)
        return None if claimed is None else claimed.id

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(worker, range(5)))

    claimed_ids = [item_id for item_id in results if item_id is not None]
    assert len(claimed_ids) == 3
    assert len(set(claimed_ids)) == 3
    assert results.count(None) == 2


def test_a_paused_queue_yields_nothing(
    repo: SQLAlchemyQueueRepository, scenario: QueueScenario,
) -> None:
    scenario.pause()
    assert claim(repo, "worker-1", scenario.queue.id) is None


def test_an_idle_queue_yields_nothing_until_it_is_started(
    factory: sessionmaker[Session], repo: SQLAlchemyQueueRepository,
) -> None:
    scenario = build_scenario(factory, label="idle", candidates=1, running=False)
    try:
        assert scenario.queue.status is QueueStatus.IDLE
        assert claim(repo, "worker-1", scenario.queue.id) is None

        scenario.start()
        assert claim(repo, "worker-1", scenario.queue.id) is not None
    finally:
        scenario.cleanup()


def test_pausing_mid_run_stops_the_next_claim_but_not_the_current_one(
    repo: SQLAlchemyQueueRepository, scenario: QueueScenario,
) -> None:
    in_flight = claim(repo, "worker-1", scenario.queue.id)
    assert in_flight is not None

    scenario.pause()
    assert claim(repo, "worker-2", scenario.queue.id) is None
    # The already-claimed item keeps its lease: pausing must not abandon a call
    # that is already under way.
    assert repo.get_item(in_flight.id).status is QueueItemStatus.CLAIMED

    scenario.start()
    resumed = claim(repo, "worker-2", scenario.queue.id)
    assert resumed is not None and resumed.id != in_flight.id


def test_an_item_waiting_on_backoff_is_not_claimable_until_its_deadline(
    repo: SQLAlchemyQueueRepository, scenario: QueueScenario,
) -> None:
    claimed = claim(repo, "worker-1", scenario.queue.id)
    assert claimed is not None
    retry_at = NOW + timedelta(minutes=10)
    repo.release_for_retry(
        claimed.id, worker_id="worker-1", next_attempt_at=retry_at, error="Simulated no answer.",
    )

    # Every other item is claimed first; the released one stays out of reach.
    while (other := claim(repo, "worker-x", scenario.queue.id)) is not None:
        assert other.id != claimed.id

    assert claim(repo, "worker-2", scenario.queue.id, now=retry_at - timedelta(seconds=1)) is None
    reclaimed = claim(repo, "worker-2", scenario.queue.id, now=retry_at)
    assert reclaimed is not None and reclaimed.id == claimed.id
    assert reclaimed.attempts == 2


def test_the_same_candidate_is_never_called_twice_at_once(
    factory: sessionmaker[Session], repo: SQLAlchemyQueueRepository,
) -> None:
    """A candidate can legitimately sit in two queues. They must still never be
    on two calls at the same time."""
    scenario = build_scenario(factory, label="dup", candidates=1, running=True)
    try:
        second_queue = repo.create_queue(CallQueueRecord(
            position_id=scenario.position.id, name="Second queue", status=QueueStatus.RUNNING,
        ))
        repo.add_item(QueueItemRecord(
            queue_id=second_queue.id, candidate_id=scenario.candidate_ids[0],
        ))

        first = claim(repo, "worker-1", scenario.queue.id)
        assert first is not None

        # The other queue's item is for the same person, so it is not claimable.
        assert claim(repo, "worker-2", second_queue.id) is None

        repo.mark_terminal(
            first.id, worker_id="worker-1", status=QueueItemStatus.COMPLETED,
        )
        # Once the first call is over, the second queue may proceed.
        assert claim(repo, "worker-2", second_queue.id) is not None
    finally:
        scenario.cleanup()


def test_expired_lease_returns_the_item_to_the_queue(
    repo: SQLAlchemyQueueRepository, scenario: QueueScenario,
) -> None:
    """The crashed-worker path: nobody renews the lease, so the item becomes
    claimable again instead of being stuck in_progress forever."""
    claimed = claim(repo, "crashed-worker", scenario.queue.id)
    assert claimed is not None
    repo.mark_in_progress(claimed.id, worker_id="crashed-worker")

    after_expiry = NOW + LEASE + timedelta(seconds=1)
    recovered = repo.recover_expired_leases(after_expiry)

    assert [item.id for item in recovered] == [claimed.id]
    reloaded = repo.get_item(claimed.id)
    assert reloaded.status is QueueItemStatus.PENDING
    assert reloaded.claimed_by is None
    assert reloaded.lease_expires_at is None
    # The spent attempt stays spent.
    assert reloaded.attempts == 1
    assert reloaded.last_error is not None and "lease expired" in reloaded.last_error.lower()


def test_a_live_lease_is_never_recovered(
    repo: SQLAlchemyQueueRepository, scenario: QueueScenario,
) -> None:
    claimed = claim(repo, "worker-1", scenario.queue.id)
    assert claimed is not None
    assert repo.recover_expired_leases(NOW + timedelta(minutes=1)) == []
    assert repo.get_item(claimed.id).status is QueueItemStatus.CLAIMED


def test_heartbeat_extends_a_lease_so_a_long_call_survives(
    repo: SQLAlchemyQueueRepository, scenario: QueueScenario,
) -> None:
    claimed = claim(repo, "worker-1", scenario.queue.id)
    assert claimed is not None
    extended_to = NOW + timedelta(minutes=30)
    repo.extend_lease(claimed.id, worker_id="worker-1", lease_expires_at=extended_to)

    assert repo.recover_expired_leases(NOW + LEASE + timedelta(seconds=1)) == []
    assert repo.get_item(claimed.id).status is QueueItemStatus.CLAIMED


def test_recovery_settles_an_item_that_has_no_attempts_left(
    factory: sessionmaker[Session], repo: SQLAlchemyQueueRepository,
) -> None:
    scenario = build_scenario(factory, label="exhausted", candidates=1, max_attempts=1, running=True)
    try:
        claimed = claim(repo, "crashed-worker", scenario.queue.id)
        assert claimed is not None and claimed.attempts == 1

        repo.recover_expired_leases(NOW + LEASE + timedelta(seconds=1))
        settled = repo.get_item(claimed.id)
        assert settled.status is QueueItemStatus.FAILED
        assert settled.last_error is not None and "no attempts remaining" in settled.last_error.lower()
    finally:
        scenario.cleanup()


def test_a_worker_that_lost_its_lease_cannot_write_to_the_item(
    repo: SQLAlchemyQueueRepository, scenario: QueueScenario,
) -> None:
    """After recovery, the old worker's late completion must be refused -- the
    item belongs to whoever holds it now."""
    claimed = claim(repo, "slow-worker", scenario.queue.id)
    assert claimed is not None
    repo.recover_expired_leases(NOW + LEASE + timedelta(seconds=1))

    with pytest.raises(LeaseLostError):
        repo.mark_terminal(
            claimed.id, worker_id="slow-worker", status=QueueItemStatus.COMPLETED,
        )
    assert repo.get_item(claimed.id).status is QueueItemStatus.PENDING


def test_only_the_holding_worker_may_advance_an_item(
    repo: SQLAlchemyQueueRepository, scenario: QueueScenario,
) -> None:
    claimed = claim(repo, "worker-1", scenario.queue.id)
    assert claimed is not None
    with pytest.raises(LeaseLostError):
        repo.mark_in_progress(claimed.id, worker_id="worker-2")


def test_writing_to_a_missing_item_reports_it_as_missing(
    repo: SQLAlchemyQueueRepository,
) -> None:
    with pytest.raises(QueueItemNotFoundError):
        repo.mark_in_progress(9_999_999, worker_id="worker-1")


def test_mark_terminal_rejects_a_non_terminal_status(
    repo: SQLAlchemyQueueRepository, scenario: QueueScenario,
) -> None:
    claimed = claim(repo, "worker-1", scenario.queue.id)
    assert claimed is not None
    with pytest.raises(ValueError):
        repo.mark_terminal(claimed.id, worker_id="worker-1", status=QueueItemStatus.PENDING)


def test_progress_counts_every_state_of_the_queue(
    repo: SQLAlchemyQueueRepository, scenario: QueueScenario,
) -> None:
    first = claim(repo, "worker-1", scenario.queue.id)
    assert first is not None
    repo.mark_terminal(
        first.id, worker_id="worker-1", status=QueueItemStatus.COMPLETED,
    )
    second = claim(repo, "worker-1", scenario.queue.id)
    assert second is not None

    progress = repo.progress(scenario.queue.id)
    assert progress.total == 3
    assert progress.status is QueueStatus.RUNNING
    assert progress.counts[QueueItemStatus.COMPLETED] == 1
    assert progress.counts[QueueItemStatus.CLAIMED] == 1
    assert progress.counts[QueueItemStatus.PENDING] == 1
    assert progress.finished == 1
    assert progress.in_flight == 1
