"""HR user repository: interface + PostgreSQL-backed implementation.

password_hash is stored and returned as-is (already hashed by services/password_hashing.py
before it ever reaches this layer) -- this repository never hashes, verifies, or logs it.
"""
from abc import ABC, abstractmethod

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from models.platform import HRUser
from services.db.orm_models import HRUserRow


class HRUserRepositoryError(Exception):
    """Base class for HR user repository failures."""


class HRUserNotFoundError(HRUserRepositoryError):
    """Raised when a requested HR user id does not exist."""


class HRUserRepository(ABC):
    @abstractmethod
    def create(self, user: HRUser) -> HRUser: ...

    @abstractmethod
    def get(self, user_id: int) -> HRUser: ...

    @abstractmethod
    def get_by_email(self, email: str) -> HRUser | None: ...

    @abstractmethod
    def update(self, user: HRUser) -> HRUser: ...


class SQLAlchemyHRUserRepository(HRUserRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create(self, user: HRUser) -> HRUser:
        with self._session_factory() as session:
            row = HRUserRow(
                email=user.email,
                password_hash=user.password_hash,
                full_name=user.full_name,
                role=user.role,
                is_active=user.is_active,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _to_model(row)

    def get(self, user_id: int) -> HRUser:
        with self._session_factory() as session:
            row = session.get(HRUserRow, user_id)
            if row is None:
                raise HRUserNotFoundError(f"HR user '{user_id}' was not found")
            return _to_model(row)

    def get_by_email(self, email: str) -> HRUser | None:
        normalized = email.strip().lower()
        with self._session_factory() as session:
            row = session.scalar(select(HRUserRow).where(HRUserRow.email == normalized))
            return _to_model(row) if row is not None else None

    def update(self, user: HRUser) -> HRUser:
        if user.id is None:
            raise ValueError("Cannot update an HR user without an id")
        with self._session_factory() as session:
            row = session.get(HRUserRow, user.id)
            if row is None:
                raise HRUserNotFoundError(f"HR user '{user.id}' was not found")
            row.full_name = user.full_name
            row.role = user.role
            row.is_active = user.is_active
            # email and password_hash are intentionally not updated here -- changing
            # either is a distinct, deliberately out-of-scope use case for P3
            # (no email-change or password-reset flow yet).
            session.commit()
            session.refresh(row)
            return _to_model(row)


def _to_model(row: HRUserRow) -> HRUser:
    return HRUser(
        id=row.id,
        email=row.email,
        password_hash=row.password_hash,
        full_name=row.full_name,
        role=row.role,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
