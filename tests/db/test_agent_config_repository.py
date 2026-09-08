"""AgentConfigRepository CRUD, the free-form config JSONB blob, and owner_id isolation."""
import pytest
from sqlalchemy.orm import Session, sessionmaker

from models.platform import AgentConfigRecord, HRUser, Position
from services.db.agent_configs import AgentConfigNotFoundError, SQLAlchemyAgentConfigRepository
from services.db.hr_users import SQLAlchemyHRUserRepository
from services.db.positions import SQLAlchemyPositionRepository

pytestmark = pytest.mark.usefixtures("pg_engine")


@pytest.fixture()
def config_repo(db_session_factory: sessionmaker[Session]) -> SQLAlchemyAgentConfigRepository:
    return SQLAlchemyAgentConfigRepository(db_session_factory)


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


def test_create_without_position_is_allowed(
    config_repo: SQLAlchemyAgentConfigRepository, owner_id: int,
) -> None:
    created = config_repo.create(AgentConfigRecord(owner_id=owner_id, position_id=None, name="Default"))
    fetched = config_repo.get(created.id)
    assert fetched.position_id is None
    assert fetched.owner_id == owner_id
    assert fetched.is_active is True


def test_config_blob_round_trips(
    config_repo: SQLAlchemyAgentConfigRepository, owner_id: int, position_id: int,
) -> None:
    blob = {"voice": "warm", "max_follow_ups": 2, "nested": {"a": [1, 2, 3]}}
    created = config_repo.create(
        AgentConfigRecord(owner_id=owner_id, position_id=position_id, name="Custom", config=blob),
    )
    fetched = config_repo.get(created.id)
    assert fetched.config == blob


def test_list_filters_by_position(
    config_repo: SQLAlchemyAgentConfigRepository, owner_id: int, db_session_factory: sessionmaker[Session],
) -> None:
    position_repo = SQLAlchemyPositionRepository(db_session_factory)
    position_a = position_repo.create(Position(owner_id=owner_id, company_name="Acme", title="Role A"))
    position_b = position_repo.create(Position(owner_id=owner_id, company_name="Acme", title="Role B"))
    config_repo.create(AgentConfigRecord(owner_id=owner_id, position_id=position_a.id, name="A config"))
    config_repo.create(AgentConfigRecord(owner_id=owner_id, position_id=position_b.id, name="B config"))

    only_a = config_repo.list(position_id=position_a.id)
    assert [c.name for c in only_a] == ["A config"]


def test_list_filters_by_owner_for_tenant_isolation(
    config_repo: SQLAlchemyAgentConfigRepository, owner_id: int, db_session_factory: sessionmaker[Session],
) -> None:
    user_repo = SQLAlchemyHRUserRepository(db_session_factory)
    other_owner_id = user_repo.create(HRUser(
        email="other-owner@acme.example", password_hash="hashed", full_name="Owner Two",
    )).id
    mine = config_repo.create(AgentConfigRecord(owner_id=owner_id, name="Mine"))
    config_repo.create(AgentConfigRecord(owner_id=other_owner_id, name="Theirs"))

    only_mine = config_repo.list(owner_id=owner_id)
    assert [c.id for c in only_mine] == [mine.id]


def test_update_toggles_is_active(
    config_repo: SQLAlchemyAgentConfigRepository, owner_id: int, position_id: int,
) -> None:
    created = config_repo.create(AgentConfigRecord(owner_id=owner_id, position_id=position_id, name="Custom"))
    created.is_active = False
    updated = config_repo.update(created)
    assert updated.is_active is False
    assert config_repo.get(created.id).is_active is False


def test_delete_removes_config(
    config_repo: SQLAlchemyAgentConfigRepository, owner_id: int, position_id: int,
) -> None:
    created = config_repo.create(AgentConfigRecord(owner_id=owner_id, position_id=position_id, name="Custom"))
    config_repo.delete(created.id)
    with pytest.raises(AgentConfigNotFoundError):
        config_repo.get(created.id)


def test_get_missing_config_raises(config_repo: SQLAlchemyAgentConfigRepository) -> None:
    with pytest.raises(AgentConfigNotFoundError):
        config_repo.get(999999)
