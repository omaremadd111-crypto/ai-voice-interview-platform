"""PositionRepository CRUD, including pass_score_threshold and owner_id persistence."""
import pytest
from sqlalchemy.orm import Session, sessionmaker

from models.common import PositionStatus
from models.platform import HRUser, Position
from services.db.hr_users import SQLAlchemyHRUserRepository
from services.db.positions import (
    PositionNotFoundError,
    PositionSlugConflictError,
    SQLAlchemyPositionRepository,
)

pytestmark = pytest.mark.usefixtures("pg_engine")


@pytest.fixture()
def repo(db_session_factory: sessionmaker[Session]) -> SQLAlchemyPositionRepository:
    return SQLAlchemyPositionRepository(db_session_factory)


@pytest.fixture()
def owner_id(db_session_factory: sessionmaker[Session]) -> int:
    user_repo = SQLAlchemyHRUserRepository(db_session_factory)
    return user_repo.create(HRUser(
        email="owner@acme.example", password_hash="hashed", full_name="Owner One",
    )).id


def _position(owner_id: int, **overrides: object) -> Position:
    defaults = dict(
        owner_id=owner_id,
        company_name="Acme",
        title="Junior AI Engineer",
        description="Build RAG systems.",
        experience_level="Junior",
        pass_score_threshold=65,
        rubric_profile="technical",
        status=PositionStatus.DRAFT,
    )
    defaults.update(overrides)
    return Position(**defaults)


def test_create_assigns_id_and_persists_all_fields(repo: SQLAlchemyPositionRepository, owner_id: int) -> None:
    created = repo.create(_position(owner_id))
    assert created.id is not None
    fetched = repo.get(created.id)
    assert fetched.owner_id == owner_id
    assert fetched.company_name == "Acme"
    assert fetched.title == "Junior AI Engineer"
    assert fetched.description == "Build RAG systems."
    assert fetched.experience_level == "Junior"
    assert fetched.status == PositionStatus.DRAFT
    assert fetched.created_at is not None
    assert fetched.updated_at is not None


def test_pass_score_threshold_round_trips_exactly(repo: SQLAlchemyPositionRepository, owner_id: int) -> None:
    created = repo.create(_position(owner_id, pass_score_threshold=72))
    assert repo.get(created.id).pass_score_threshold == 72


def test_pass_score_threshold_defaults_to_null(repo: SQLAlchemyPositionRepository, owner_id: int) -> None:
    created = repo.create(_position(owner_id, pass_score_threshold=None))
    fetched = repo.get(created.id)
    assert fetched.pass_score_threshold is None


def test_rubric_profile_reference_persists(repo: SQLAlchemyPositionRepository, owner_id: int) -> None:
    created = repo.create(_position(owner_id, rubric_profile="general"))
    assert repo.get(created.id).rubric_profile == "general"


def test_get_missing_position_raises(repo: SQLAlchemyPositionRepository) -> None:
    with pytest.raises(PositionNotFoundError):
        repo.get(999999)


def test_list_returns_created_positions_ordered_by_id(repo: SQLAlchemyPositionRepository, owner_id: int) -> None:
    first = repo.create(_position(owner_id, title="Role A"))
    second = repo.create(_position(owner_id, title="Role B"))
    listed = repo.list()
    ids = [p.id for p in listed]
    assert ids.index(first.id) < ids.index(second.id)


def test_list_filters_by_status(repo: SQLAlchemyPositionRepository, owner_id: int) -> None:
    draft = repo.create(_position(owner_id, title="Draft Role", status=PositionStatus.DRAFT))
    active = repo.create(_position(owner_id, title="Active Role", status=PositionStatus.ACTIVE))
    active_only = repo.list(status=PositionStatus.ACTIVE)
    ids = {p.id for p in active_only}
    assert active.id in ids
    assert draft.id not in ids


def test_list_filters_by_owner_for_tenant_isolation(
    repo: SQLAlchemyPositionRepository, owner_id: int, db_session_factory: sessionmaker[Session],
) -> None:
    user_repo = SQLAlchemyHRUserRepository(db_session_factory)
    other_owner_id = user_repo.create(HRUser(
        email="other-owner@acme.example", password_hash="hashed", full_name="Owner Two",
    )).id
    mine = repo.create(_position(owner_id, title="My Role"))
    repo.create(_position(other_owner_id, title="Their Role"))

    only_mine = repo.list(owner_id=owner_id)
    assert [p.id for p in only_mine] == [mine.id]


def test_update_changes_persisted_fields(repo: SQLAlchemyPositionRepository, owner_id: int) -> None:
    created = repo.create(_position(owner_id))
    created.title = "Senior AI Engineer"
    created.status = PositionStatus.ACTIVE
    created.pass_score_threshold = 80
    updated = repo.update(created)
    assert updated.title == "Senior AI Engineer"
    assert updated.status == PositionStatus.ACTIVE
    refetched = repo.get(created.id)
    assert refetched.title == "Senior AI Engineer"
    assert refetched.pass_score_threshold == 80


def test_update_missing_position_raises(repo: SQLAlchemyPositionRepository, owner_id: int) -> None:
    ghost = _position(owner_id)
    ghost.id = 999999
    with pytest.raises(PositionNotFoundError):
        repo.update(ghost)


def test_delete_removes_position(repo: SQLAlchemyPositionRepository, owner_id: int) -> None:
    created = repo.create(_position(owner_id))
    repo.delete(created.id)
    with pytest.raises(PositionNotFoundError):
        repo.get(created.id)


def test_delete_missing_position_raises(repo: SQLAlchemyPositionRepository) -> None:
    with pytest.raises(PositionNotFoundError):
        repo.delete(999999)


def test_public_slug_defaults_to_null(repo: SQLAlchemyPositionRepository, owner_id: int) -> None:
    created = repo.create(_position(owner_id))
    assert created.public_slug is None
    assert repo.get(created.id).public_slug is None


def test_public_slug_round_trips_through_update(
    repo: SQLAlchemyPositionRepository, owner_id: int,
) -> None:
    created = repo.create(_position(owner_id))
    created.public_slug = "junior-ai-engineer-ab12cd34"
    updated = repo.update(created)
    assert updated.public_slug == "junior-ai-engineer-ab12cd34"
    assert repo.get(created.id).public_slug == "junior-ai-engineer-ab12cd34"


def test_get_by_slug_finds_the_matching_position(
    repo: SQLAlchemyPositionRepository, owner_id: int,
) -> None:
    created = repo.create(_position(owner_id))
    created.public_slug = "senior-ai-engineer-ff00ff00"
    repo.update(created)
    found = repo.get_by_slug("senior-ai-engineer-ff00ff00")
    assert found is not None
    assert found.id == created.id


def test_get_by_slug_returns_none_for_an_unknown_slug(repo: SQLAlchemyPositionRepository) -> None:
    assert repo.get_by_slug("does-not-exist-00000000") is None


def test_duplicate_public_slug_raises_slug_conflict(
    repo: SQLAlchemyPositionRepository, owner_id: int,
) -> None:
    first = repo.create(_position(owner_id, title="Role A"))
    first.public_slug = "shared-slug-00000000"
    repo.update(first)

    second = repo.create(_position(owner_id, title="Role B"))
    second.public_slug = "shared-slug-00000000"
    with pytest.raises(PositionSlugConflictError):
        repo.update(second)
