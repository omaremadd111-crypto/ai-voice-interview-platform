"""CandidateRepository CRUD and CV-optional persistence."""
import pytest
from sqlalchemy.orm import Session, sessionmaker

from models.common import CandidateStatus
from models.platform import CandidateRecord, HRUser, Position
from services.db.candidates import CandidateNotFoundError, SQLAlchemyCandidateRepository
from services.db.hr_users import SQLAlchemyHRUserRepository
from services.db.positions import SQLAlchemyPositionRepository

pytestmark = pytest.mark.usefixtures("pg_engine")


@pytest.fixture()
def candidate_repo(db_session_factory: sessionmaker[Session]) -> SQLAlchemyCandidateRepository:
    return SQLAlchemyCandidateRepository(db_session_factory)


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


def _candidate(position_id: int, **overrides: object) -> CandidateRecord:
    defaults = dict(position_id=position_id, full_name="Jordan Rivera")
    defaults.update(overrides)
    return CandidateRecord(**defaults)


def test_create_without_cv_persists_null_cv_fields(
    candidate_repo: SQLAlchemyCandidateRepository, position_id: int,
) -> None:
    created = candidate_repo.create(_candidate(position_id))
    fetched = candidate_repo.get(created.id)
    assert fetched.full_name == "Jordan Rivera"
    assert fetched.cv_text is None
    assert fetched.cv_filename is None
    assert fetched.status == CandidateStatus.NEW


def test_create_with_cv_persists_cv_text_and_filename(
    candidate_repo: SQLAlchemyCandidateRepository, position_id: int,
) -> None:
    created = candidate_repo.create(_candidate(
        position_id, cv_text="Built a Python API.", cv_filename="resume.pdf",
    ))
    fetched = candidate_repo.get(created.id)
    assert fetched.cv_text == "Built a Python API."
    assert fetched.cv_filename == "resume.pdf"


def test_email_and_phone_are_optional(
    candidate_repo: SQLAlchemyCandidateRepository, position_id: int,
) -> None:
    created = candidate_repo.create(_candidate(position_id, email=None, phone=None))
    fetched = candidate_repo.get(created.id)
    assert fetched.email is None
    assert fetched.phone is None


def test_email_and_phone_persist_when_provided(
    candidate_repo: SQLAlchemyCandidateRepository, position_id: int,
) -> None:
    created = candidate_repo.create(_candidate(position_id, email="jordan@example.com", phone="+1-555-0100"))
    fetched = candidate_repo.get(created.id)
    assert fetched.email == "jordan@example.com"
    assert fetched.phone == "+1-555-0100"


def test_list_for_position_returns_only_that_positions_candidates(
    candidate_repo: SQLAlchemyCandidateRepository, owner_id: int, db_session_factory: sessionmaker[Session],
) -> None:
    position_repo = SQLAlchemyPositionRepository(db_session_factory)
    position_a = position_repo.create(Position(owner_id=owner_id, company_name="Acme", title="Role A"))
    position_b = position_repo.create(Position(owner_id=owner_id, company_name="Acme", title="Role B"))
    candidate_repo.create(_candidate(position_a.id, full_name="Alpha Candidate"))
    candidate_repo.create(_candidate(position_b.id, full_name="Beta Candidate"))

    listed = candidate_repo.list_for_position(position_a.id)
    assert [c.full_name for c in listed] == ["Alpha Candidate"]


def test_update_changes_status_and_cv(
    candidate_repo: SQLAlchemyCandidateRepository, position_id: int,
) -> None:
    created = candidate_repo.create(_candidate(position_id))
    created.status = CandidateStatus.SCREENED
    created.cv_text = "Added later."
    updated = candidate_repo.update(created)
    assert updated.status == CandidateStatus.SCREENED
    assert candidate_repo.get(created.id).cv_text == "Added later."


def test_delete_removes_candidate(
    candidate_repo: SQLAlchemyCandidateRepository, position_id: int,
) -> None:
    created = candidate_repo.create(_candidate(position_id))
    candidate_repo.delete(created.id)
    with pytest.raises(CandidateNotFoundError):
        candidate_repo.get(created.id)


def test_get_missing_candidate_raises(candidate_repo: SQLAlchemyCandidateRepository) -> None:
    with pytest.raises(CandidateNotFoundError):
        candidate_repo.get(999999)
