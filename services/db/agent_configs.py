"""Agent config repository: interface + PostgreSQL-backed implementation.

Config is a free-form JSONB blob validated as ``VoiceAgentPersona`` by the
application layer. Keeping it provider-neutral lets LiveKit and future transports
consume the same identity and delivery settings without provider columns here.
"""
from abc import ABC, abstractmethod

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from models.platform import AgentConfigRecord
from services.db.orm_models import AgentConfigRow


class AgentConfigRepositoryError(Exception):
    """Base class for agent config repository failures."""


class AgentConfigNotFoundError(AgentConfigRepositoryError):
    """Raised when a requested agent config id does not exist."""


class AgentConfigRepository(ABC):
    @abstractmethod
    def create(self, config: AgentConfigRecord) -> AgentConfigRecord: ...

    @abstractmethod
    def get(self, config_id: int) -> AgentConfigRecord: ...

    @abstractmethod
    def list(
        self, *, position_id: int | None = None, owner_id: int | None = None,
    ) -> list[AgentConfigRecord]: ...

    @abstractmethod
    def update(self, config: AgentConfigRecord) -> AgentConfigRecord: ...

    @abstractmethod
    def delete(self, config_id: int) -> None: ...


class SQLAlchemyAgentConfigRepository(AgentConfigRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create(self, config: AgentConfigRecord) -> AgentConfigRecord:
        with self._session_factory() as session:
            row = AgentConfigRow(
                owner_id=config.owner_id,
                position_id=config.position_id,
                name=config.name,
                config=config.config,
                is_active=config.is_active,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _to_model(row)

    def get(self, config_id: int) -> AgentConfigRecord:
        with self._session_factory() as session:
            row = session.get(AgentConfigRow, config_id)
            if row is None:
                raise AgentConfigNotFoundError(f"Agent config '{config_id}' was not found")
            return _to_model(row)

    def list(
        self, *, position_id: int | None = None, owner_id: int | None = None,
    ) -> list[AgentConfigRecord]:
        with self._session_factory() as session:
            stmt = select(AgentConfigRow).order_by(AgentConfigRow.id)
            if position_id is not None:
                stmt = stmt.where(AgentConfigRow.position_id == position_id)
            if owner_id is not None:
                stmt = stmt.where(AgentConfigRow.owner_id == owner_id)
            rows = session.scalars(stmt).all()
            return [_to_model(row) for row in rows]

    def update(self, config: AgentConfigRecord) -> AgentConfigRecord:
        if config.id is None:
            raise ValueError("Cannot update an agent config without an id")
        with self._session_factory() as session:
            row = session.get(AgentConfigRow, config.id)
            if row is None:
                raise AgentConfigNotFoundError(f"Agent config '{config.id}' was not found")
            # owner_id is intentionally not updated here -- see positions.py's update().
            row.position_id = config.position_id
            row.name = config.name
            row.config = config.config
            row.is_active = config.is_active
            session.commit()
            session.refresh(row)
            return _to_model(row)

    def delete(self, config_id: int) -> None:
        with self._session_factory() as session:
            row = session.get(AgentConfigRow, config_id)
            if row is None:
                raise AgentConfigNotFoundError(f"Agent config '{config_id}' was not found")
            session.delete(row)
            session.commit()


def _to_model(row: AgentConfigRow) -> AgentConfigRecord:
    return AgentConfigRecord(
        id=row.id,
        owner_id=row.owner_id,
        position_id=row.position_id,
        name=row.name,
        config=dict(row.config),
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
