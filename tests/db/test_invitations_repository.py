"""InterviewInvitationRepository: CRUD, lookup by token hash, and the partial
UNIQUE(candidate_id) WHERE status='active' database guarantee."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from models.common import InvitationStatus
from models.platform import (
    CandidateRecord,
    HRUser,
    InterviewInvitationRecord,
    Position,
    QueueItemRecord,
)
from services.db.candidates import SQLAlchemyCandidateRepository
from services.db.hr_users import SQLAlchemyHRUserRepository
from services.db.invitations import (
    InterviewInvitationNotFoundError,
    SQLAlchemyInterviewInvitationRepository,
)
from services.db.positions import SQLAlchemyPositionRepository
from services.db.queues import SQLAlchemyQueueRepository

pytestmark = pytest.mark.usefixtures("pg_engine")


@pytest.fixture()
def repo(db_session_factory: sessionmaker[Session]) -> SQLAlchemyInterviewInvitationRepository:
    return SQLAlchemyInterviewInvitationRepository(db_session_factory)


@pytest.fixture()
def owner_id(db_session_factory: sessionmaker[Session]) -> int:
    return SQLAlchemyHRUserRepository(db_session_factory).create(HRUser(
        email="owner-invitations@acme.example", password_hash="hashed", full_name="Owner",
    )).id


@pytest.fixture()
def position_id(db_session_factory: sessionmaker[Session], owner_id: int) -> int:
    return SQLAlchemyPositionRepository(db_session_factory).create(Position(
        owner_id=owner_id, company_name="Acme", title="Junior AI Engineer",
    )).id


@pytest.fixture()
def candidate_id(db_session_factory: sessionmaker[Session], position_id: int) -> int:
    return SQLAlchemyCandidateRepository(db_session_factory).create(CandidateRecord(
        position_id=position_id, full_name="Jordan Rivera",
    )).id


@pytest.fixture()
def queue_item_id(
    db_session_factory: sessionmaker[Session], position_id: int, candidate_id: int,
) -> int:
    from models.platform import CallQueueRecord

    queue_repo = SQLAlchemyQueueRepository(db_session_factory)
    queue = queue_repo.create_queue(CallQueueRecord(position_id=position_id, name="Auto queue"))
    item = queue_repo.add_item(QueueItemRecord(queue_id=queue.id, candidate_id=candidate_id))
    return item.id


@pytest.fixture()
def second_candidate_and_item(
    db_session_factory: sessionmaker[Session], position_id: int,
) -> tuple[int, int]:
    """A second, independent candidate+queue-item pair -- expire_stale tests
    need two DIFFERENT candidates since only one ACTIVE invitation per
    candidate is ever allowed at the database level."""
    from models.platform import CallQueueRecord

    candidate = SQLAlchemyCandidateRepository(db_session_factory).create(CandidateRecord(
        position_id=position_id, full_name="Alex Chen",
    ))
    queue_repo = SQLAlchemyQueueRepository(db_session_factory)
    queue = queue_repo.create_queue(CallQueueRecord(position_id=position_id, name="Second queue"))
    item = queue_repo.add_item(QueueItemRecord(queue_id=queue.id, candidate_id=candidate.id))
    return candidate.id, item.id


def _invitation(
    candidate_id: int, position_id: int, queue_item_id: int, **overrides: object,
) -> InterviewInvitationRecord:
    defaults = dict(
        candidate_id=candidate_id,
        position_id=position_id,
        queue_item_id=queue_item_id,
        token_hash="a" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=168),
    )
    defaults.update(overrides)
    return InterviewInvitationRecord(**defaults)


def test_create_and_get_by_token_hash_round_trip(
    repo: SQLAlchemyInterviewInvitationRepository, candidate_id: int, position_id: int, queue_item_id: int,
) -> None:
    created = repo.create(_invitation(candidate_id, position_id, queue_item_id, token_hash="b" * 64))
    found = repo.get_by_token_hash("b" * 64)
    assert found is not None
    assert found.id == created.id
    assert found.status is InvitationStatus.ACTIVE


def test_get_by_token_hash_returns_none_for_an_unknown_hash(
    repo: SQLAlchemyInterviewInvitationRepository,
) -> None:
    assert repo.get_by_token_hash("does-not-exist" * 4) is None


def test_get_active_for_candidate_ignores_a_revoked_row(
    repo: SQLAlchemyInterviewInvitationRepository, candidate_id: int, position_id: int, queue_item_id: int,
) -> None:
    created = repo.create(_invitation(candidate_id, position_id, queue_item_id, token_hash="c" * 64))
    repo.revoke(created.id, now=datetime.now(timezone.utc))
    assert repo.get_active_for_candidate(candidate_id) is None


def test_revoke_is_idempotent(
    repo: SQLAlchemyInterviewInvitationRepository, candidate_id: int, position_id: int, queue_item_id: int,
) -> None:
    created = repo.create(_invitation(candidate_id, position_id, queue_item_id, token_hash="d" * 64))
    now = datetime.now(timezone.utc)
    repo.revoke(created.id, now=now)
    repo.revoke(created.id, now=now + timedelta(minutes=5))  # must not raise


def test_revoke_missing_invitation_raises(repo: SQLAlchemyInterviewInvitationRepository) -> None:
    with pytest.raises(InterviewInvitationNotFoundError):
        repo.revoke(999999, now=datetime.now(timezone.utc))


def test_mark_opened_sets_first_opened_at_only_once(
    repo: SQLAlchemyInterviewInvitationRepository, candidate_id: int, position_id: int, queue_item_id: int,
) -> None:
    created = repo.create(_invitation(candidate_id, position_id, queue_item_id, token_hash="e" * 64))
    first_open = datetime.now(timezone.utc)
    repo.mark_opened(created.id, now=first_open)
    repo.mark_opened(created.id, now=first_open + timedelta(hours=1))

    reloaded = repo.get_by_token_hash("e" * 64)
    assert reloaded is not None
    assert reloaded.first_opened_at is not None
    assert abs((reloaded.first_opened_at - first_open).total_seconds()) < 1


def test_only_one_active_invitation_per_candidate_at_the_database_level(
    repo: SQLAlchemyInterviewInvitationRepository, candidate_id: int, position_id: int, queue_item_id: int,
) -> None:
    repo.create(_invitation(candidate_id, position_id, queue_item_id, token_hash="f" * 64))
    with pytest.raises(IntegrityError):
        repo.create(_invitation(candidate_id, position_id, queue_item_id, token_hash="g" * 64))


def test_a_revoked_and_a_new_active_invitation_can_coexist(
    repo: SQLAlchemyInterviewInvitationRepository, candidate_id: int, position_id: int, queue_item_id: int,
) -> None:
    """Rotation's actual shape: revoke the old row, then insert a new one --
    never update in place -- so the partial unique index only ever sees at
    most one ACTIVE row, never two, even mid-rotation."""
    first = repo.create(_invitation(candidate_id, position_id, queue_item_id, token_hash="h" * 64))
    repo.revoke(first.id, now=datetime.now(timezone.utc))
    second = repo.create(_invitation(candidate_id, position_id, queue_item_id, token_hash="i" * 64))
    assert second.id is not None
    active = repo.get_active_for_candidate(candidate_id)
    assert active is not None
    assert active.id == second.id


# --- expire_stale (P7 phase 3) ----------------------------------------------

def test_expire_stale_flips_a_past_expiry_active_row_to_expired(
    repo: SQLAlchemyInterviewInvitationRepository, candidate_id: int, position_id: int, queue_item_id: int,
) -> None:
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    repo.create(_invitation(
        candidate_id, position_id, queue_item_id, token_hash="j" * 64, expires_at=past,
    ))

    expired = repo.expire_stale(now=datetime.now(timezone.utc))

    assert len(expired) == 1
    assert expired[0].status is InvitationStatus.EXPIRED
    reloaded = repo.get_by_token_hash("j" * 64)
    assert reloaded is not None
    assert reloaded.status is InvitationStatus.EXPIRED


def test_expire_stale_leaves_a_not_yet_expired_active_row_alone(
    repo: SQLAlchemyInterviewInvitationRepository, candidate_id: int, position_id: int, queue_item_id: int,
) -> None:
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    repo.create(_invitation(
        candidate_id, position_id, queue_item_id, token_hash="k" * 64, expires_at=future,
    ))

    expired = repo.expire_stale(now=datetime.now(timezone.utc))

    assert expired == []
    reloaded = repo.get_by_token_hash("k" * 64)
    assert reloaded is not None
    assert reloaded.status is InvitationStatus.ACTIVE


def test_expire_stale_only_touches_the_row_that_is_actually_due(
    repo: SQLAlchemyInterviewInvitationRepository, candidate_id: int, position_id: int, queue_item_id: int,
    second_candidate_and_item: tuple[int, int],
) -> None:
    second_candidate_id, second_queue_item_id = second_candidate_and_item
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    repo.create(_invitation(
        candidate_id, position_id, queue_item_id, token_hash="l" * 64, expires_at=past,
    ))
    repo.create(_invitation(
        second_candidate_id, position_id, second_queue_item_id, token_hash="m" * 64, expires_at=future,
    ))

    expired = repo.expire_stale(now=datetime.now(timezone.utc))

    assert [e.token_hash for e in expired] == ["l" * 64]
    assert repo.get_by_token_hash("m" * 64).status is InvitationStatus.ACTIVE


def test_expire_stale_run_twice_is_a_harmless_no_op_the_second_time(
    repo: SQLAlchemyInterviewInvitationRepository, candidate_id: int, position_id: int, queue_item_id: int,
) -> None:
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    repo.create(_invitation(
        candidate_id, position_id, queue_item_id, token_hash="n" * 64, expires_at=past,
    ))

    now = datetime.now(timezone.utc)
    first_sweep = repo.expire_stale(now=now)
    second_sweep = repo.expire_stale(now=now)

    assert len(first_sweep) == 1
    assert second_sweep == []
