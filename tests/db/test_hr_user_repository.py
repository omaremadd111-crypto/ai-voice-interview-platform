"""HRUserRepository CRUD: email lookup, uniqueness, and password_hash handling."""
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from models.platform import HRUser
from services.db.hr_users import HRUserNotFoundError, SQLAlchemyHRUserRepository

pytestmark = pytest.mark.usefixtures("pg_engine")


@pytest.fixture()
def repo(db_session_factory: sessionmaker[Session]) -> SQLAlchemyHRUserRepository:
    return SQLAlchemyHRUserRepository(db_session_factory)


def _user(**overrides: object) -> HRUser:
    defaults = dict(email="jordan@acme.example", password_hash="pbkdf2$...", full_name="Jordan Rivera")
    defaults.update(overrides)
    return HRUser(**defaults)


def test_create_assigns_id_and_persists_fields(repo: SQLAlchemyHRUserRepository) -> None:
    created = repo.create(_user())
    assert created.id is not None
    fetched = repo.get(created.id)
    assert fetched.email == "jordan@acme.example"
    assert fetched.full_name == "Jordan Rivera"
    assert fetched.password_hash == "pbkdf2$..."
    assert fetched.role == "recruiter"
    assert fetched.is_active is True


def test_email_is_normalized_to_lowercase(repo: SQLAlchemyHRUserRepository) -> None:
    created = repo.create(_user(email="Jordan@ACME.example"))
    assert created.email == "jordan@acme.example"
    assert repo.get_by_email("JORDAN@acme.EXAMPLE") is not None


def test_get_by_email_returns_none_when_absent(repo: SQLAlchemyHRUserRepository) -> None:
    assert repo.get_by_email("does-not-exist@acme.example") is None


def test_get_by_email_finds_created_user(repo: SQLAlchemyHRUserRepository) -> None:
    created = repo.create(_user())
    found = repo.get_by_email("jordan@acme.example")
    assert found is not None
    assert found.id == created.id


def test_duplicate_email_is_rejected(
    repo: SQLAlchemyHRUserRepository, db_session_factory: sessionmaker[Session],
) -> None:
    repo.create(_user())
    with pytest.raises(IntegrityError):
        with db_session_factory() as session:
            from services.db.orm_models import HRUserRow
            session.add(HRUserRow(email="jordan@acme.example", password_hash="x", full_name="Someone Else"))
            session.commit()


def test_get_missing_user_raises(repo: SQLAlchemyHRUserRepository) -> None:
    with pytest.raises(HRUserNotFoundError):
        repo.get(999999)


def test_update_changes_full_name_role_and_active_flag(repo: SQLAlchemyHRUserRepository) -> None:
    created = repo.create(_user())
    created.full_name = "Jordan R."
    created.role = "admin"
    created.is_active = False
    updated = repo.update(created)
    assert updated.full_name == "Jordan R."
    assert updated.role == "admin"
    assert updated.is_active is False


def test_update_does_not_change_email_or_password_hash(repo: SQLAlchemyHRUserRepository) -> None:
    created = repo.create(_user())
    created.email = "different@acme.example"
    created.password_hash = "a-different-hash"
    repo.update(created)
    refetched = repo.get(created.id)
    assert refetched.email == "jordan@acme.example"
    assert refetched.password_hash == "pbkdf2$..."


def test_update_missing_user_raises(repo: SQLAlchemyHRUserRepository) -> None:
    ghost = _user()
    ghost.id = 999999
    with pytest.raises(HRUserNotFoundError):
        repo.update(ghost)
