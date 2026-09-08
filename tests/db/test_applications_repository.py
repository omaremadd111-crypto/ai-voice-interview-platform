"""JobApplicationRepository: CRUD, the (position, email) idempotency guard,
and the rate-limit count queries."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session, sessionmaker

from models.common import ApplicationState
from models.platform import CandidateRecord, HRUser, JobApplicationRecord, Position
from services.db.applications import (
    DuplicateApplicationError,
    JobApplicationNotFoundError,
    SQLAlchemyJobApplicationRepository,
)
from services.db.candidates import SQLAlchemyCandidateRepository
from services.db.hr_users import SQLAlchemyHRUserRepository
from services.db.positions import SQLAlchemyPositionRepository

pytestmark = pytest.mark.usefixtures("pg_engine")


@pytest.fixture()
def repo(db_session_factory: sessionmaker[Session]) -> SQLAlchemyJobApplicationRepository:
    return SQLAlchemyJobApplicationRepository(db_session_factory)


@pytest.fixture()
def owner_id(db_session_factory: sessionmaker[Session]) -> int:
    return SQLAlchemyHRUserRepository(db_session_factory).create(HRUser(
        email="owner-applications@acme.example", password_hash="hashed", full_name="Owner",
    )).id


@pytest.fixture()
def position_id(db_session_factory: sessionmaker[Session], owner_id: int) -> int:
    return SQLAlchemyPositionRepository(db_session_factory).create(Position(
        owner_id=owner_id, company_name="Acme", title="Junior AI Engineer",
    )).id


def _application(position_id: int, **overrides: object) -> JobApplicationRecord:
    defaults = dict(
        position_id=position_id, email_normalized="jordan@example.com", full_name="Jordan Rivera",
    )
    defaults.update(overrides)
    return JobApplicationRecord(**defaults)


def test_create_assigns_id_and_defaults_to_received(
    repo: SQLAlchemyJobApplicationRepository, position_id: int,
) -> None:
    created = repo.create(_application(position_id))
    assert created.id is not None
    assert created.pipeline_state is ApplicationState.RECEIVED
    assert created.candidate_id is None


def test_get_for_position_and_email_finds_the_row(
    repo: SQLAlchemyJobApplicationRepository, position_id: int,
) -> None:
    repo.create(_application(position_id))
    found = repo.get_for_position_and_email(position_id, "jordan@example.com")
    assert found is not None
    assert found.full_name == "Jordan Rivera"


def test_get_for_position_and_email_returns_none_when_absent(
    repo: SQLAlchemyJobApplicationRepository, position_id: int,
) -> None:
    assert repo.get_for_position_and_email(position_id, "nobody@example.com") is None


def test_duplicate_position_and_email_raises(
    repo: SQLAlchemyJobApplicationRepository, position_id: int,
) -> None:
    repo.create(_application(position_id))
    with pytest.raises(DuplicateApplicationError):
        repo.create(_application(position_id))


def test_same_email_at_a_different_position_is_not_a_duplicate(
    repo: SQLAlchemyJobApplicationRepository, position_id: int,
    db_session_factory: sessionmaker[Session], owner_id: int,
) -> None:
    other_position_id = SQLAlchemyPositionRepository(db_session_factory).create(Position(
        owner_id=owner_id, company_name="Acme", title="Senior AI Engineer",
    )).id
    repo.create(_application(position_id))
    second = repo.create(_application(other_position_id))
    assert second.id is not None


def test_update_changes_pipeline_state_and_candidate_id(
    repo: SQLAlchemyJobApplicationRepository, position_id: int,
    db_session_factory: sessionmaker[Session],
) -> None:
    # candidate_id is a real foreign key (SET NULL on delete) -- a fake id
    # correctly raises rather than silently accepting an orphaned reference.
    candidate = SQLAlchemyCandidateRepository(db_session_factory).create(CandidateRecord(
        position_id=position_id, full_name="Jordan Rivera",
    ))
    created = repo.create(_application(position_id))
    updated = repo.update(created.model_copy(update={
        "candidate_id": candidate.id, "pipeline_state": ApplicationState.INVITED,
    }))
    assert updated.candidate_id == candidate.id
    assert updated.pipeline_state is ApplicationState.INVITED
    refetched = repo.get(created.id)
    assert refetched.candidate_id == candidate.id


def test_get_missing_application_raises(repo: SQLAlchemyJobApplicationRepository) -> None:
    with pytest.raises(JobApplicationNotFoundError):
        repo.get(999999)


def test_list_for_position_orders_most_recent_first(
    repo: SQLAlchemyJobApplicationRepository, position_id: int,
) -> None:
    first = repo.create(_application(position_id, email_normalized="first@example.com"))
    second = repo.create(_application(position_id, email_normalized="second@example.com"))
    listed = repo.list_for_position(position_id)
    assert [row.id for row in listed] == [second.id, first.id]


def test_count_recent_for_position_only_counts_within_the_window(
    repo: SQLAlchemyJobApplicationRepository, position_id: int,
) -> None:
    repo.create(_application(position_id))
    now = datetime.now(timezone.utc)
    assert repo.count_recent_for_position(position_id, now - timedelta(hours=1)) == 1
    assert repo.count_recent_for_position(position_id, now + timedelta(hours=1)) == 0


def test_count_recent_for_ip_only_counts_that_ip(
    repo: SQLAlchemyJobApplicationRepository, position_id: int,
) -> None:
    repo.create(_application(position_id, email_normalized="a@example.com", submitter_ip_hash="hash-a"))
    repo.create(_application(position_id, email_normalized="b@example.com", submitter_ip_hash="hash-b"))
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    assert repo.count_recent_for_ip("hash-a", since) == 1
    assert repo.count_recent_for_ip("hash-c", since) == 0
