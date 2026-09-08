"""QueueService: the recruiter-facing rules around a calling queue.

Ownership, the approved-plan gate, the in-flight guard, and the Start/Pause/Resume
transitions. Concurrency lives in test_queue_claiming.py; this module is about
what a person is and is not allowed to do.
"""
import pytest
from sqlalchemy.orm import Session, sessionmaker

from application.auth_service import ForbiddenError
from application.queue_service import (
    CandidatePositionMismatchError,
    ItemNotInQueueError,
    PlanNotApprovedError,
    QueueItemInFlightError,
    QueueService,
)
from application.position_service import PositionService
from models.common import CandidateStatus, QueueItemStatus, QueueStatus
from models.platform import CandidateRecord, HRUser, Position
from services.db.candidates import SQLAlchemyCandidateRepository
from services.db.hr_users import SQLAlchemyHRUserRepository
from services.db.interview_plans import SQLAlchemyInterviewPlanRepository
from services.db.positions import SQLAlchemyPositionRepository
from services.db.queues import DuplicateQueueItemError, SQLAlchemyQueueRepository
from tests.db.queue_fixtures import approve_plan, unique_email

pytestmark = pytest.mark.usefixtures("pg_engine")


@pytest.fixture()
def service(db_session_factory: sessionmaker[Session]) -> QueueService:
    return QueueService(
        SQLAlchemyQueueRepository(db_session_factory),
        PositionService(SQLAlchemyPositionRepository(db_session_factory)),
        SQLAlchemyCandidateRepository(db_session_factory),
        SQLAlchemyInterviewPlanRepository(db_session_factory),
    )


@pytest.fixture()
def owner(db_session_factory: sessionmaker[Session]) -> HRUser:
    return SQLAlchemyHRUserRepository(db_session_factory).create(HRUser(
        email=unique_email("owner"), password_hash="hashed", full_name="Queue Owner",
    ))


@pytest.fixture()
def stranger(db_session_factory: sessionmaker[Session]) -> HRUser:
    return SQLAlchemyHRUserRepository(db_session_factory).create(HRUser(
        email=unique_email("stranger"), password_hash="hashed", full_name="Someone Else",
    ))


@pytest.fixture()
def position(db_session_factory: sessionmaker[Session], owner: HRUser) -> Position:
    return SQLAlchemyPositionRepository(db_session_factory).create(Position(
        owner_id=owner.id, company_name="FlairsTech", title="Junior AI Engineer",
    ))


@pytest.fixture()
def candidate(db_session_factory: sessionmaker[Session], position: Position) -> CandidateRecord:
    created = SQLAlchemyCandidateRepository(db_session_factory).create(CandidateRecord(
        position_id=position.id, full_name="Demo Candidate",
    ))
    approve_plan(SQLAlchemyInterviewPlanRepository(db_session_factory), created.id)
    return created


def test_a_new_queue_starts_idle(service: QueueService, owner: HRUser, position: Position) -> None:
    queue = service.create(owner, position.id, name="Screening round 1")
    assert queue.status is QueueStatus.IDLE


def test_start_pause_and_resume_move_the_queue_between_states(
    service: QueueService, owner: HRUser, position: Position,
) -> None:
    queue = service.create(owner, position.id, name="Screening round 1")
    assert service.start(owner, queue.id).status is QueueStatus.RUNNING
    assert service.pause(owner, queue.id).status is QueueStatus.PAUSED
    assert service.resume(owner, queue.id).status is QueueStatus.RUNNING


def test_starting_an_already_running_queue_is_harmless(
    service: QueueService, owner: HRUser, position: Position,
) -> None:
    queue = service.create(owner, position.id, name="Screening round 1")
    service.start(owner, queue.id)
    assert service.start(owner, queue.id).status is QueueStatus.RUNNING


def test_adding_a_candidate_marks_them_queued(
    service: QueueService,
    owner: HRUser,
    position: Position,
    candidate: CandidateRecord,
    db_session_factory: sessionmaker[Session],
) -> None:
    queue = service.create(owner, position.id, name="Screening round 1")
    item = service.add_candidate(owner, queue.id, candidate.id)

    assert item.status is QueueItemStatus.PENDING
    assert item.attempts == 0
    reloaded = SQLAlchemyCandidateRepository(db_session_factory).get(candidate.id)
    assert reloaded.status is CandidateStatus.QUEUED


def test_a_candidate_without_an_approved_plan_cannot_be_queued(
    service: QueueService,
    owner: HRUser,
    position: Position,
    db_session_factory: sessionmaker[Session],
) -> None:
    """The human-in-the-loop gate: nobody is called with questions no recruiter
    signed off on."""
    unreviewed = SQLAlchemyCandidateRepository(db_session_factory).create(CandidateRecord(
        position_id=position.id, full_name="Unreviewed Candidate",
    ))
    queue = service.create(owner, position.id, name="Screening round 1")

    with pytest.raises(PlanNotApprovedError):
        service.add_candidate(owner, queue.id, unreviewed.id)


def test_a_draft_plan_is_not_an_approved_plan(
    service: QueueService,
    owner: HRUser,
    position: Position,
    db_session_factory: sessionmaker[Session],
) -> None:
    plan_repo = SQLAlchemyInterviewPlanRepository(db_session_factory)
    drafted = SQLAlchemyCandidateRepository(db_session_factory).create(CandidateRecord(
        position_id=position.id, full_name="Drafted Candidate",
    ))
    approve_plan(plan_repo, drafted.id, approved=False)
    queue = service.create(owner, position.id, name="Screening round 1")

    with pytest.raises(PlanNotApprovedError):
        service.add_candidate(owner, queue.id, drafted.id)


def test_a_candidate_from_another_position_is_rejected(
    service: QueueService,
    owner: HRUser,
    position: Position,
    db_session_factory: sessionmaker[Session],
) -> None:
    other_position = SQLAlchemyPositionRepository(db_session_factory).create(Position(
        owner_id=owner.id, company_name="FlairsTech", title="Data Analyst",
    ))
    other_candidate = SQLAlchemyCandidateRepository(db_session_factory).create(CandidateRecord(
        position_id=other_position.id, full_name="Other Candidate",
    ))
    approve_plan(SQLAlchemyInterviewPlanRepository(db_session_factory), other_candidate.id)
    queue = service.create(owner, position.id, name="Screening round 1")

    with pytest.raises(CandidatePositionMismatchError):
        service.add_candidate(owner, queue.id, other_candidate.id)


def test_the_same_candidate_cannot_be_added_to_one_queue_twice(
    service: QueueService, owner: HRUser, position: Position, candidate: CandidateRecord,
) -> None:
    queue = service.create(owner, position.id, name="Screening round 1")
    service.add_candidate(owner, queue.id, candidate.id)
    with pytest.raises(DuplicateQueueItemError):
        service.add_candidate(owner, queue.id, candidate.id)


def test_removing_a_candidate_takes_them_out_of_the_queue(
    service: QueueService, owner: HRUser, position: Position, candidate: CandidateRecord,
) -> None:
    queue = service.create(owner, position.id, name="Screening round 1")
    item = service.add_candidate(owner, queue.id, candidate.id)
    service.remove_candidate(owner, queue.id, item.id)
    assert service.list_items(owner, queue.id) == []


def test_an_in_flight_item_cannot_be_removed_or_cancelled(
    service: QueueService,
    owner: HRUser,
    position: Position,
    candidate: CandidateRecord,
    db_session_factory: sessionmaker[Session],
) -> None:
    from datetime import datetime, timedelta, timezone

    queue = service.create(owner, position.id, name="Screening round 1")
    service.start(owner, queue.id)
    item = service.add_candidate(owner, queue.id, candidate.id)

    now = datetime.now(timezone.utc)
    SQLAlchemyQueueRepository(db_session_factory).claim_next_item(
        worker_id="worker-1", now=now, lease_expires_at=now + timedelta(minutes=5), queue_id=queue.id,
    )

    with pytest.raises(QueueItemInFlightError):
        service.remove_candidate(owner, queue.id, item.id)
    with pytest.raises(QueueItemInFlightError):
        service.cancel_item(owner, queue.id, item.id)


def test_cancelling_keeps_the_item_but_stops_the_calls(
    service: QueueService, owner: HRUser, position: Position, candidate: CandidateRecord,
) -> None:
    queue = service.create(owner, position.id, name="Screening round 1")
    item = service.add_candidate(owner, queue.id, candidate.id)
    cancelled = service.cancel_item(owner, queue.id, item.id)

    assert cancelled.status is QueueItemStatus.CANCELLED
    assert cancelled.next_attempt_at is None
    assert len(service.list_items(owner, queue.id)) == 1


def test_retrying_a_cancelled_item_gives_it_fresh_attempts(
    service: QueueService, owner: HRUser, position: Position, candidate: CandidateRecord,
) -> None:
    queue = service.create(owner, position.id, name="Screening round 1")
    item = service.add_candidate(owner, queue.id, candidate.id)
    service.cancel_item(owner, queue.id, item.id)

    retried = service.retry_item(owner, queue.id, item.id)
    assert retried.status is QueueItemStatus.PENDING
    assert retried.attempts == 0
    assert retried.last_error is None


def test_an_item_from_another_queue_cannot_be_reached_through_this_one(
    service: QueueService, owner: HRUser, position: Position, candidate: CandidateRecord,
) -> None:
    first = service.create(owner, position.id, name="Queue A")
    second = service.create(owner, position.id, name="Queue B")
    item = service.add_candidate(owner, first.id, candidate.id)

    with pytest.raises(ItemNotInQueueError):
        service.remove_candidate(owner, second.id, item.id)


def test_progress_reports_the_queue_and_its_items(
    service: QueueService, owner: HRUser, position: Position, candidate: CandidateRecord,
) -> None:
    queue = service.create(owner, position.id, name="Screening round 1")
    service.add_candidate(owner, queue.id, candidate.id)
    service.start(owner, queue.id)

    progress = service.progress(owner, queue.id)
    assert progress.total == 1
    assert progress.status is QueueStatus.RUNNING
    assert progress.counts[QueueItemStatus.PENDING] == 1
    assert progress.finished == 0


def test_another_recruiter_can_neither_see_nor_touch_the_queue(
    service: QueueService,
    owner: HRUser,
    stranger: HRUser,
    position: Position,
    candidate: CandidateRecord,
) -> None:
    """Tenant isolation: a queue is reachable only through its owning position."""
    queue = service.create(owner, position.id, name="Screening round 1")
    item = service.add_candidate(owner, queue.id, candidate.id)

    for call in (
        lambda: service.get(stranger, queue.id),
        lambda: service.rename(stranger, queue.id, name="Hijacked"),
        lambda: service.delete(stranger, queue.id),
        lambda: service.start(stranger, queue.id),
        lambda: service.pause(stranger, queue.id),
        lambda: service.list_items(stranger, queue.id),
        lambda: service.progress(stranger, queue.id),
        lambda: service.add_candidate(stranger, queue.id, candidate.id),
        lambda: service.remove_candidate(stranger, queue.id, item.id),
        lambda: service.cancel_item(stranger, queue.id, item.id),
    ):
        with pytest.raises(ForbiddenError):
            call()


def test_listing_returns_only_the_callers_own_queues(
    service: QueueService,
    owner: HRUser,
    stranger: HRUser,
    position: Position,
    db_session_factory: sessionmaker[Session],
) -> None:
    service.create(owner, position.id, name="Mine")
    stranger_position = SQLAlchemyPositionRepository(db_session_factory).create(Position(
        owner_id=stranger.id, company_name="Other Co", title="Engineer",
    ))
    service.create(stranger, stranger_position.id, name="Theirs")

    names = {queue.name for queue in service.list(owner)}
    assert "Mine" in names
    assert "Theirs" not in names
