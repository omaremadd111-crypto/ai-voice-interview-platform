"""SQLAlchemy engine and session-factory construction.

The only place a DATABASE_URL becomes a live connection. Callers receive a
sessionmaker; nothing outside this module handles a connection string or a raw
Engine/Connection.
"""
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from config.settings import Settings


class DatabaseNotConfiguredError(Exception):
    """Raised when a PostgreSQL-backed component is used without DATABASE_URL set."""


def build_engine(settings: Settings) -> Engine:
    if not settings.database_url:
        raise DatabaseNotConfiguredError(
            "DATABASE_URL is not configured; set it in .env to use PostgreSQL persistence."
        )
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        # Managed Postgres (Neon in particular) can close a connection server-side
        # after a period of idleness without telling the client. pool_pre_ping
        # already detects and transparently replaces a dead connection, but that
        # detect-then-reconnect happens INLINE in whichever request draws the
        # short straw. Recycling proactively, before Neon's own idle window is
        # likely to have closed it, trades a rare extra reconnect for fewer
        # surprise ones landing on a real request.
        pool_recycle=180,
        future=True,
    )


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
