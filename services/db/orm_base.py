"""Shared SQLAlchemy declarative base for all ORM table classes."""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
