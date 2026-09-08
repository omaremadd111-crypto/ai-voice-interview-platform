"""QueueRepository CRUD and the UNIQUE(queue_id, candidate_id) duplicate guard."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session, sessionmaker

from models.common import QueueItemStatus, QueueKind, QueueStatus
from models.platform import CallQueueRecord, CandidateRecord, HRUser, Position, QueueItemRecord
from services.db.candidates import SQLAlchemyCandidateRepository
from services.db.hr_users import SQLAlchemyHRUserRepository
from services.db.positions import SQLAlchemyPositionRepository
from services.db.postgres_session_store import PostgresSessionStore
from services.db.queues import (
    DuplicateQueueItemError,
    QueueItemNotFoundError,
    QueueNotFoundError,
    SQLAlchemyQueueRepository,
)
from tests.db.session_factories import minimal_session

pytestmark = pytest.mark.usefixtures("pg_engine")


@pytest.fixture()
def queue_repo(db_session_factory: sessionmaker[Session]) -> SQLAlchemyQueueRepository:
    return SQLAlchemyQueueRepository(db_session_factory)


@pytest.fixture()
def owner_id(db_session_factory: sessionmaker[Session]) -> int:
    user_repo = SQLAlchemyHRUserRepository(db_session_factory)
    return user_repo.create(HRUser(
        email="owner@acme.example", password_hash="hashed", full_name="Owner One",
    )).id


@pytest.fixture()
def position_id(db_session_factory: sessionmaker[Session], owner_id: int) -> int:
    position_repo = SQLAlchemyPositionRepository(db_session_factory)
    return position_repo.create(Position(owner_id=owner_id, company_name="Acme", title="Engineer")).id


@pytest.fixture()
def candidate_id(db_session_factory: sessionmaker[Session], position_id: int) -> int:
    candidate_repo = SQLAlchemyCandidateRepository(db_session_factory)
    return candidate_repo.create(CandidateRecord(position_id=position_id, full_name="Jordan Rivera")).id


@pytest.fixture()
def queue_id(queue_repo: SQLAlchemyQueueRepository, position_id: int) -> int:
    return queue_repo.create_queue(CallQueueRecord(position_id=position_id, name="Screening queue")).id


def test_create_queue_and_get(queue_repo: SQLAlchemyQueueRepository, position_id: int) -> None:
    created = queue_repo.create_queue(CallQueueRecord(position_id=position_id, name="Screening queue"))
    fetched = queue_repo.get_queue(created.id)
    assert fetched.name == "Screening queue"
    assert fetched.position_id == position_id


def test_get_missing_queue_raises(queue_repo: SQLAlchemyQueueRepository) -> None:
    with pytest.raises(QueueNotFoundError):
        queue_repo.get_queue(999999)


def test_add_item_defaults_to_pending_with_zero_attempts(
    queue_repo: SQLAlchemyQueueRepository, queue_id: int, candidate_id: int,
) -> None:
    created = queue_repo.add_item(QueueItemRecord(queue_id=queue_id, candidate_id=candidate_id))
    assert created.status == QueueItemStatus.PENDING
    assert created.attempts == 0
    assert created.max_attempts == 3


def test_duplicate_candidate_in_same_queue_is_rejected(
    queue_repo: SQLAlchemyQueueRepository, queue_id: int, candidate_id: int,
) -> None:
    queue_repo.add_item(QueueItemRecord(queue_id=queue_id, candidate_id=candidate_id))
    with pytest.raises(DuplicateQueueItemError):
        queue_repo.add_item(QueueItemRecord(queue_id=queue_id, candidate_id=candidate_id))


def test_same_candidate_can_be_queued_in_two_different_queues(
    queue_repo: SQLAlchemyQueueRepository, position_id: int, candidate_id: int,
) -> None:
    second_queue = queue_repo.create_queue(CallQueueRecord(position_id=position_id, name="Second queue"))
    queue_repo.add_item(QueueItemRecord(queue_id=second_queue.id, candidate_id=candidate_id))
    # No exception -- a duplicate is only rejected within the SAME queue.


def test_update_item_advances_status_and_records_claim_fields(
    queue_repo: SQLAlchemyQueueRepository, queue_id: int, candidate_id: int,
) -> None:
    from datetime import datetime, timezone

    created = queue_repo.add_item(QueueItemRecord(queue_id=queue_id, candidate_id=candidate_id))
    created.status = QueueItemStatus.CLAIMED
    created.attempts = 1
    created.claimed_by = "worker-1"
    created.claimed_at = datetime.now(timezone.utc)
    created.last_error = None
    updated = queue_repo.update_item(created)
    assert updated.status == QueueItemStatus.CLAIMED
    assert updated.claimed_by == "worker-1"

    refetched = queue_repo.get_item(created.id)
    assert refetched.attempts == 1
    assert refetched.claimed_at is not None


def test_update_item_records_failure(
    queue_repo: SQLAlchemyQueueRepository, queue_id: int, candidate_id: int,
) -> None:
    created = queue_repo.add_item(QueueItemRecord(queue_id=queue_id, candidate_id=candidate_id))
    created.status = QueueItemStatus.FAILED
    created.attempts = 3
    created.last_error = "No answer after 3 attempts"
    updated = queue_repo.update_item(created)
    assert updated.status == QueueItemStatus.FAILED
    assert updated.last_error == "No answer after 3 attempts"


def test_list_items_for_queue(
    queue_repo: SQLAlchemyQueueRepository, queue_id: int, candidate_id: int,
    db_session_factory: sessionmaker[Session], position_id: int,
) -> None:
    candidate_repo = SQLAlchemyCandidateRepository(db_session_factory)
    second_candidate = candidate_repo.create(CandidateRecord(position_id=position_id, full_name="Second Candidate"))
    queue_repo.add_item(QueueItemRecord(queue_id=queue_id, candidate_id=candidate_id))
    queue_repo.add_item(QueueItemRecord(queue_id=queue_id, candidate_id=second_candidate.id))

    items = queue_repo.list_items(queue_id)
    assert len(items) == 2


def test_delete_item_removes_it(
    queue_repo: SQLAlchemyQueueRepository, queue_id: int, candidate_id: int,
) -> None:
    created = queue_repo.add_item(QueueItemRecord(queue_id=queue_id, candidate_id=candidate_id))
    queue_repo.delete_item(created.id)
    with pytest.raises(QueueItemNotFoundError):
        queue_repo.get_item(created.id)


def test_get_missing_item_raises(queue_repo: SQLAlchemyQueueRepository) -> None:
    with pytest.raises(QueueItemNotFoundError):
        queue_repo.get_item(999999)


def test_get_item_for_candidate_finds_the_matching_item(
    queue_repo: SQLAlchemyQueueRepository, queue_id: int, candidate_id: int,
) -> None:
    created = queue_repo.add_item(QueueItemRecord(queue_id=queue_id, candidate_id=candidate_id))
    found = queue_repo.get_item_for_candidate(queue_id, candidate_id)
    assert found is not None
    assert found.id == created.id


def test_get_item_for_candidate_returns_none_when_absent(
    queue_repo: SQLAlchemyQueueRepository, queue_id: int, candidate_id: int,
) -> None:
    assert queue_repo.get_item_for_candidate(queue_id, candidate_id) is None


# --- awaiting_candidate / arming (auto screening pipeline) -----------------

def test_awaiting_candidate_items_are_not_claimable(
    queue_repo: SQLAlchemyQueueRepository, queue_id: int, candidate_id: int,
) -> None:
    queue_repo.update_queue(
        queue_repo.get_queue(queue_id).model_copy(update={"status": QueueStatus.RUNNING})
    )
    queue_repo.add_item(QueueItemRecord(
        queue_id=queue_id, candidate_id=candidate_id, status=QueueItemStatus.AWAITING_CANDIDATE,
    ))
    claimed = queue_repo.claim_next_item(
        worker_id="worker-1", now=datetime.now(timezone.utc),
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    assert claimed is None


def test_arm_item_flips_awaiting_candidate_to_pending(
    queue_repo: SQLAlchemyQueueRepository, queue_id: int, candidate_id: int,
) -> None:
    created = queue_repo.add_item(QueueItemRecord(
        queue_id=queue_id, candidate_id=candidate_id, status=QueueItemStatus.AWAITING_CANDIDATE,
    ))
    armed = queue_repo.arm_item(created.id)
    assert armed.status is QueueItemStatus.PENDING


def test_arm_item_on_an_already_armed_item_is_a_harmless_no_op(
    queue_repo: SQLAlchemyQueueRepository, queue_id: int, candidate_id: int,
) -> None:
    created = queue_repo.add_item(QueueItemRecord(
        queue_id=queue_id, candidate_id=candidate_id, status=QueueItemStatus.AWAITING_CANDIDATE,
    ))
    first = queue_repo.arm_item(created.id)
    second = queue_repo.arm_item(created.id)
    assert first.status is QueueItemStatus.PENDING
    assert second.status is QueueItemStatus.PENDING


def test_arm_item_does_not_touch_an_item_already_claimed_by_a_worker(
    queue_repo: SQLAlchemyQueueRepository, queue_id: int, candidate_id: int,
) -> None:
    queue_repo.update_queue(
        queue_repo.get_queue(queue_id).model_copy(update={"status": QueueStatus.RUNNING})
    )
    queue_repo.add_item(QueueItemRecord(queue_id=queue_id, candidate_id=candidate_id))
    claimed = queue_repo.claim_next_item(
        worker_id="worker-1", now=datetime.now(timezone.utc),
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    assert claimed is not None

    # Arming a CLAIMED item (not AWAITING_CANDIDATE) must not disturb it -- the
    # conditional update only ever matches AWAITING_CANDIDATE rows.
    result = queue_repo.arm_item(claimed.id)
    assert result.status is QueueItemStatus.CLAIMED
    assert result.claimed_by == "worker-1"


def test_release_to_awaiting_candidate_clears_the_lease_and_session(
    queue_repo: SQLAlchemyQueueRepository, queue_id: int, candidate_id: int,
    db_session_factory: sessionmaker[Session],
) -> None:
    # interview_session_id is a real foreign key to interview_sessions -- needs
    # an actual row to attach, not just any string.
    PostgresSessionStore(db_session_factory).create(minimal_session("session-abc"))

    queue_repo.update_queue(
        queue_repo.get_queue(queue_id).model_copy(update={"status": QueueStatus.RUNNING})
    )
    queue_repo.add_item(QueueItemRecord(queue_id=queue_id, candidate_id=candidate_id))
    claimed = queue_repo.claim_next_item(
        worker_id="worker-1", now=datetime.now(timezone.utc),
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    assert claimed is not None
    queue_repo.attach_session(claimed.id, worker_id="worker-1", interview_session_id="session-abc")

    released = queue_repo.release_to_awaiting_candidate(
        claimed.id, worker_id="worker-1", error="No candidate joined before the timeout.",
    )
    assert released.status is QueueItemStatus.AWAITING_CANDIDATE
    assert released.claimed_by is None
    assert released.lease_expires_at is None
    assert released.next_attempt_at is None
    assert released.interview_session_id is None
    assert released.last_error == "No candidate joined before the timeout."
    # attempts stays spent, exactly like release_for_retry.
    assert released.attempts == claimed.attempts


# --- pre-warming (auto screening pipeline latency fix) ---------------------


@pytest.fixture()
def auto_queue_id(queue_repo: SQLAlchemyQueueRepository, position_id: int) -> int:
    queue = queue_repo.create_queue(CallQueueRecord(
        position_id=position_id, name="Auto queue", status=QueueStatus.RUNNING, kind=QueueKind.AUTO,
    ))
    return queue.id


def test_claim_for_prewarm_takes_an_awaiting_candidate_item_in_an_auto_queue(
    queue_repo: SQLAlchemyQueueRepository, auto_queue_id: int, candidate_id: int,
) -> None:
    created = queue_repo.add_item(QueueItemRecord(
        queue_id=auto_queue_id, candidate_id=candidate_id, status=QueueItemStatus.AWAITING_CANDIDATE,
    ))
    now = datetime.now(timezone.utc)
    claimed = queue_repo.claim_for_prewarm(now=now)
    assert claimed is not None
    assert claimed.id == created.id
    # Still AWAITING_CANDIDATE: pre-warming never opens a room or arms anything.
    assert claimed.status is QueueItemStatus.AWAITING_CANDIDATE
    assert claimed.prewarm_claimed_at == now


def test_claim_for_prewarm_ignores_manual_queues(
    queue_repo: SQLAlchemyQueueRepository, queue_id: int, candidate_id: int,
) -> None:
    """AWAITING_CANDIDATE items only exist in auto-pipeline queues in practice,
    but the query is explicit about it rather than relying on that."""
    queue_repo.update_queue(
        queue_repo.get_queue(queue_id).model_copy(update={"status": QueueStatus.RUNNING})
    )
    queue_repo.add_item(QueueItemRecord(
        queue_id=queue_id, candidate_id=candidate_id, status=QueueItemStatus.AWAITING_CANDIDATE,
    ))
    assert queue_repo.claim_for_prewarm(now=datetime.now(timezone.utc)) is None


def test_claim_for_prewarm_ignores_an_item_that_already_has_a_session(
    queue_repo: SQLAlchemyQueueRepository, auto_queue_id: int, candidate_id: int,
    db_session_factory: sessionmaker[Session],
) -> None:
    PostgresSessionStore(db_session_factory).create(minimal_session("session-prewarmed"))
    item = queue_repo.add_item(QueueItemRecord(
        queue_id=auto_queue_id, candidate_id=candidate_id, status=QueueItemStatus.AWAITING_CANDIDATE,
    ))
    queue_repo.attach_prepared_session(item.id, interview_session_id="session-prewarmed")
    assert queue_repo.claim_for_prewarm(now=datetime.now(timezone.utc)) is None


def test_claim_for_prewarm_does_not_reclaim_an_already_claimed_item(
    queue_repo: SQLAlchemyQueueRepository, auto_queue_id: int, candidate_id: int,
) -> None:
    queue_repo.add_item(QueueItemRecord(
        queue_id=auto_queue_id, candidate_id=candidate_id, status=QueueItemStatus.AWAITING_CANDIDATE,
    ))
    first = queue_repo.claim_for_prewarm(now=datetime.now(timezone.utc))
    assert first is not None
    assert queue_repo.claim_for_prewarm(now=datetime.now(timezone.utc)) is None


def test_attach_prepared_session_sets_the_session_and_leaves_status_awaiting_candidate(
    queue_repo: SQLAlchemyQueueRepository, auto_queue_id: int, candidate_id: int,
    db_session_factory: sessionmaker[Session],
) -> None:
    PostgresSessionStore(db_session_factory).create(minimal_session("session-prewarmed"))
    item = queue_repo.add_item(QueueItemRecord(
        queue_id=auto_queue_id, candidate_id=candidate_id, status=QueueItemStatus.AWAITING_CANDIDATE,
    ))
    attached = queue_repo.attach_prepared_session(item.id, interview_session_id="session-prewarmed")
    assert attached.interview_session_id == "session-prewarmed"
    assert attached.status is QueueItemStatus.AWAITING_CANDIDATE


def test_attach_prepared_session_also_works_once_armed_but_not_yet_claimed(
    queue_repo: SQLAlchemyQueueRepository, auto_queue_id: int, candidate_id: int,
    db_session_factory: sessionmaker[Session],
) -> None:
    """Covers the narrow window between the candidate pressing Start and a
    worker actually claiming the item: a pre-warm attach landing here must
    still succeed, not be lost."""
    PostgresSessionStore(db_session_factory).create(minimal_session("session-prewarmed"))
    item = queue_repo.add_item(QueueItemRecord(
        queue_id=auto_queue_id, candidate_id=candidate_id, status=QueueItemStatus.AWAITING_CANDIDATE,
    ))
    queue_repo.arm_item(item.id)
    attached = queue_repo.attach_prepared_session(item.id, interview_session_id="session-prewarmed")
    assert attached.interview_session_id == "session-prewarmed"
    assert attached.status is QueueItemStatus.PENDING


def test_attach_prepared_session_never_overwrites_a_session_already_attached(
    queue_repo: SQLAlchemyQueueRepository, auto_queue_id: int, candidate_id: int,
    db_session_factory: sessionmaker[Session],
) -> None:
    """If a live attempt already attached its own session (the item raced
    ahead of pre-warming), a late pre-warm attach must be a harmless no-op --
    never clobber the session the worker is about to dispatch a room for."""
    PostgresSessionStore(db_session_factory).create(minimal_session("session-live"))
    PostgresSessionStore(db_session_factory).create(minimal_session("session-prewarmed-late"))
    item = queue_repo.add_item(QueueItemRecord(
        queue_id=auto_queue_id, candidate_id=candidate_id, status=QueueItemStatus.AWAITING_CANDIDATE,
    ))
    queue_repo.arm_item(item.id)
    now = datetime.now(timezone.utc)
    claimed = queue_repo.claim_next_item(
        worker_id="worker-1", now=now, lease_expires_at=now + timedelta(minutes=5),
        queue_id=auto_queue_id,
    )
    assert claimed is not None
    queue_repo.attach_session(claimed.id, worker_id="worker-1", interview_session_id="session-live")

    result = queue_repo.attach_prepared_session(item.id, interview_session_id="session-prewarmed-late")
    assert result.interview_session_id == "session-live"


def test_release_to_awaiting_candidate_clears_the_prewarm_claim_too(
    queue_repo: SQLAlchemyQueueRepository, auto_queue_id: int, candidate_id: int,
    db_session_factory: sessionmaker[Session],
) -> None:
    """A retried auto-pipeline attempt must be eligible for pre-warming again,
    not permanently skipped because a previous attempt already claimed it."""
    PostgresSessionStore(db_session_factory).create(minimal_session("session-first-attempt"))
    item = queue_repo.add_item(QueueItemRecord(
        queue_id=auto_queue_id, candidate_id=candidate_id, status=QueueItemStatus.AWAITING_CANDIDATE,
    ))
    queue_repo.claim_for_prewarm(now=datetime.now(timezone.utc))
    queue_repo.attach_prepared_session(item.id, interview_session_id="session-first-attempt")

    queue_repo.arm_item(item.id)
    now = datetime.now(timezone.utc)
    claimed = queue_repo.claim_next_item(
        worker_id="worker-1", now=now, lease_expires_at=now + timedelta(minutes=5),
        queue_id=auto_queue_id,
    )
    assert claimed is not None
    released = queue_repo.release_to_awaiting_candidate(
        claimed.id, worker_id="worker-1", error="No candidate joined before the timeout.",
    )
    assert released.prewarm_claimed_at is None
    assert released.interview_session_id is None

    reclaimed = queue_repo.claim_for_prewarm(now=datetime.now(timezone.utc))
    assert reclaimed is not None
    assert reclaimed.id == item.id
