import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.db_safety import assert_test_database_is_isolated


@pytest.fixture()
def isolated_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the test against a private copy of os.environ.

    load_settings() (config/settings.py) and load_api_settings() (api/settings.py)
    call load_dotenv(..., override=False), which permanently injects every
    previously-absent key from the loaded file into os.environ. MonkeyPatch
    cannot undo that on its own: delenv(..., raising=False) records nothing for
    keys that were absent, so dotenv's later injection survives teardown and
    leaks across tests -- the order-dependent failure class behind the red TTS
    provider suite. Swapping in a copy makes every mutation, including dotenv's,
    disappear when the original environ object is restored.
    """
    monkeypatch.setattr(os, "environ", os.environ.copy())


@pytest.fixture(scope="session")
def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def fixtures_dir(project_root: Path) -> Path:
    return project_root / "tests" / "fixtures"


# --- Shared PostgreSQL-backed test infrastructure (tests/db/, tests/api/) ---
#
# Requires a real database and is SKIPPED (not failed) when TEST_DATABASE_URL is
# unset, so `pytest` on its own never needs a database. To run this subset:
#
#     TEST_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/scratch_db pytest
#
# Use a disposable/scratch database -- pg_engine drops and recreates the entire
# schema via the real Alembic migration at the start of the run. Never point
# this at a database holding real data.


def _test_database_url() -> str | None:
    return os.getenv("TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def pg_engine(project_root: Path) -> Iterator[Engine]:
    database_url = _test_database_url()
    if not database_url:
        pytest.skip("TEST_DATABASE_URL not set -- skipping PostgreSQL-backed tests")

    # Fail closed, hard, BEFORE anything below touches a connection or runs a
    # migration: if TEST_DATABASE_URL resolves to the same database as the
    # ambient DATABASE_URL (env var, shell, or .env -- whatever load_settings()
    # would actually resolve today), refuse outright rather than skip. A skip
    # would let this exact misconfiguration through silently, which is how real
    # test data ended up in the development database. See tests/db_safety.py.
    assert_test_database_is_isolated(database_url, os.environ.get("DATABASE_URL"))

    # load_settings() (used by migrations/env.py) reads DATABASE_URL via
    # load_dotenv(..., override=False), so setting it here first makes the test
    # database win over anything in a checked-in .env for the rest of this process.
    # Set directly on the real os.environ (not inside the isolation window below)
    # so it legitimately persists for the rest of the session.
    os.environ["DATABASE_URL"] = database_url

    alembic_cfg = Config(str(project_root / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(project_root / "migrations"))
    # migrations/env.py calls load_settings() with NO env_file argument, which
    # defaults to the real project .env -- and that file pins MOCK_MODE=false,
    # VOICE_TTS_PROVIDER=cartesia, LIVEKIT_TTS_MODEL=cartesia/sonic-3, etc.
    # load_dotenv(override=False) injects every one of those into the REAL
    # os.environ the first time this session-scoped fixture runs (typically far
    # earlier than any function-scoped test using `isolated_environ`), and that
    # fixture cannot undo contamination that happened before it was ever
    # invoked -- it only stops a test's OWN mutations from leaking forward.
    # The same private-copy technique as `isolated_environ`, applied around the
    # migration invocation instead of a single test, contains it here instead:
    # anything migrations/env.py's load_settings() injects lands in the copy
    # and is discarded when the window closes, while the DATABASE_URL set above
    # (assigned to the real object beforehand) survives untouched.
    with pytest.MonkeyPatch.context() as isolate_migration_env:
        isolate_migration_env.setattr(os, "environ", os.environ.copy())
        # Idempotent: leaves a known-clean schema regardless of what a prior run
        # left behind, and doubles as a real test that the migration applies AND
        # rolls back.
        command.downgrade(alembic_cfg, "base")
        command.upgrade(alembic_cfg, "head")

    engine = create_engine(database_url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session_factory(pg_engine: Engine) -> Iterator[sessionmaker[Session]]:
    connection = pg_engine.connect()
    outer_transaction = connection.begin()
    factory = sessionmaker(bind=connection, join_transaction_mode="create_savepoint", future=True)
    try:
        yield factory
    finally:
        outer_transaction.rollback()
        connection.close()
