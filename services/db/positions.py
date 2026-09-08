"""Position repository: interface + PostgreSQL-backed implementation.

pass_score_threshold and rubric_profile are persisted only. services/scoring.py
does not read them yet -- this prepares per-position screening thresholds without
changing evaluation behavior prematurely.
"""
from abc import ABC, abstractmethod

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from models.common import PositionStatus
from models.platform import Position
from services.db.orm_models import PositionRow


class PositionRepositoryError(Exception):
    """Base class for position repository failures."""


class PositionNotFoundError(PositionRepositoryError):
    """Raised when a requested position id does not exist."""


class PositionSlugConflictError(PositionRepositoryError):
    """Raised when a public_slug collides with another position's (UNIQUE).

    Vanishingly unlikely given the random suffix PositionPublishingService mints,
    but never impossible -- the caller regenerates and retries rather than this
    layer silently overwriting someone else's live application page.
    """


class PositionRepository(ABC):
    @abstractmethod
    def create(self, position: Position) -> Position: ...

    @abstractmethod
    def get(self, position_id: int) -> Position: ...

    @abstractmethod
    def get_by_slug(self, slug: str) -> Position | None: ...

    @abstractmethod
    def list(self, *, status: PositionStatus | None = None, owner_id: int | None = None) -> list[Position]: ...

    @abstractmethod
    def update(self, position: Position) -> Position: ...

    @abstractmethod
    def delete(self, position_id: int) -> None: ...


class SQLAlchemyPositionRepository(PositionRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create(self, position: Position) -> Position:
        with self._session_factory() as session:
            row = PositionRow(
                owner_id=position.owner_id,
                company_name=position.company_name,
                title=position.title,
                description=position.description,
                experience_level=position.experience_level,
                pass_score_threshold=position.pass_score_threshold,
                rubric_profile=position.rubric_profile,
                status=position.status.value,
                public_slug=position.public_slug,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _to_model(row)

    def get(self, position_id: int) -> Position:
        with self._session_factory() as session:
            row = session.get(PositionRow, position_id)
            if row is None:
                raise PositionNotFoundError(f"Position '{position_id}' was not found")
            return _to_model(row)

    def get_by_slug(self, slug: str) -> Position | None:
        with self._session_factory() as session:
            row = session.scalar(select(PositionRow).where(PositionRow.public_slug == slug))
            return _to_model(row) if row is not None else None

    def list(self, *, status: PositionStatus | None = None, owner_id: int | None = None) -> list[Position]:
        with self._session_factory() as session:
            stmt = select(PositionRow).order_by(PositionRow.id)
            if status is not None:
                stmt = stmt.where(PositionRow.status == status.value)
            if owner_id is not None:
                stmt = stmt.where(PositionRow.owner_id == owner_id)
            rows = session.scalars(stmt).all()
            return [_to_model(row) for row in rows]

    def update(self, position: Position) -> Position:
        if position.id is None:
            raise ValueError("Cannot update a position without an id")
        with self._session_factory() as session:
            row = session.get(PositionRow, position.id)
            if row is None:
                raise PositionNotFoundError(f"Position '{position.id}' was not found")
            # owner_id is intentionally not updated here -- ownership is immutable
            # after creation; there is no transfer-ownership use case in P3.
            row.company_name = position.company_name
            row.title = position.title
            row.description = position.description
            row.experience_level = position.experience_level
            row.pass_score_threshold = position.pass_score_threshold
            row.rubric_profile = position.rubric_profile
            row.status = position.status.value
            row.public_slug = position.public_slug
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise PositionSlugConflictError(
                    f"public_slug '{position.public_slug}' is already in use"
                ) from exc
            session.refresh(row)
            return _to_model(row)

    def delete(self, position_id: int) -> None:
        with self._session_factory() as session:
            row = session.get(PositionRow, position_id)
            if row is None:
                raise PositionNotFoundError(f"Position '{position_id}' was not found")
            session.delete(row)
            session.commit()


def _to_model(row: PositionRow) -> Position:
    return Position(
        id=row.id,
        owner_id=row.owner_id,
        company_name=row.company_name,
        title=row.title,
        description=row.description,
        experience_level=row.experience_level,
        pass_score_threshold=row.pass_score_threshold,
        rubric_profile=row.rubric_profile,
        status=PositionStatus(row.status),
        public_slug=row.public_slug,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
