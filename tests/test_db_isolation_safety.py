"""Regression tests for the DB test-isolation guard (tests/db_safety.py).

These test the guard function directly -- no real database, no TEST_DATABASE_URL
required, so they run unconditionally on every plain `pytest` invocation. That
matters: the whole point of the guard is to catch a misconfiguration BEFORE any
connection is attempted, so its own tests must not depend on the thing it
protects against being correctly configured.

Root cause this closes: a real, live example found during this audit --
DATABASE_URL was silently shadowed by a stale Windows user-level environment
variable, so every process (including pytest) connected to a database that
matched neither the project's checked-in .env DATABASE_URL nor its
TEST_DATABASE_URL. `pg_engine` had no check that TEST_DATABASE_URL actually
differed from whatever DATABASE_URL resolves to at runtime -- it only checked
that TEST_DATABASE_URL was non-empty. Had TEST_DATABASE_URL and the shadowed
DATABASE_URL ever been equal, `pg_engine` would have run `alembic downgrade
base` + `upgrade head` against the real database and then written the entire
suite's test data into it. That is the exact shape of the leak this audit found
(claim-1@queue-tests.example ... claim-7).
"""

from __future__ import annotations

import pytest

from tests.db_safety import UnsafeTestDatabaseError, assert_test_database_is_isolated

REAL_DEV_URL = "postgresql+psycopg://neondb_owner:secret@ep-real-dev-abc123.c-6.eu-central-1.aws.neon.tech/neondb?sslmode=require"
TEST_URL = "postgresql+psycopg://neondb_owner:secret@ep-scratch-test-xyz789.c-6.eu-central-1.aws.neon.tech/neondb?sslmode=require"

# The literal shape of the real incident: two URLs on different hosts/regions/
# projects entirely, one shadowed in via a machine-level env var never declared
# in any project file.
SHADOWED_REAL_URL = "postgresql://neondb_owner:secret@ep-shiny-sky-axis08wl-pooler.c-4.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
DOTENV_DATABASE_URL = "postgresql+psycopg://neondb_owner:secret@ep-wild-water-b26sxnuy.c-6.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
DOTENV_TEST_DATABASE_URL = "postgresql+psycopg://neondb_owner:secret@ep-misty-sea-b21xg0d1.c-6.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"


def test_passes_when_test_and_real_databases_genuinely_differ() -> None:
    assert_test_database_is_isolated(TEST_URL, REAL_DEV_URL)  # must not raise


def test_passes_when_database_url_is_unset() -> None:
    """Nothing real is configured -- there is nothing to collide with."""
    assert_test_database_is_isolated(TEST_URL, None) is None
    assert_test_database_is_isolated(TEST_URL, "") is None


def test_fails_closed_on_byte_identical_urls() -> None:
    """The simplest, most likely accidental mistake: the same string twice."""
    with pytest.raises(UnsafeTestDatabaseError, match="byte-identical"):
        assert_test_database_is_isolated(REAL_DEV_URL, REAL_DEV_URL)


def test_fails_closed_when_only_the_driver_prefix_differs() -> None:
    """Same host and database, spelled with/without the +psycopg driver suffix.

    A naive string-equality check alone would miss this -- the guard must
    compare parsed connection targets, not raw text.
    """
    without_driver = REAL_DEV_URL.replace("postgresql+psycopg://", "postgresql://")
    with pytest.raises(UnsafeTestDatabaseError, match="both resolve to"):
        assert_test_database_is_isolated(without_driver, REAL_DEV_URL)


def test_fails_closed_when_only_query_parameters_differ() -> None:
    """Same host and database, different sslmode/channel_binding query string."""
    with_extra_params = REAL_DEV_URL + "&channel_binding=require"
    with pytest.raises(UnsafeTestDatabaseError, match="both resolve to"):
        assert_test_database_is_isolated(with_extra_params, REAL_DEV_URL)


def test_unparsable_url_fails_closed_rather_than_being_ignored() -> None:
    with pytest.raises(UnsafeTestDatabaseError, match="Could not parse"):
        assert_test_database_is_isolated("not a connection string", REAL_DEV_URL)


def test_reproduces_the_actual_incident_shape() -> None:
    """The exact three URLs found live on this machine during the audit.

    TEST_DATABASE_URL (declared, eu-central-1) vs. the ambient shadowed
    DATABASE_URL (undeclared anywhere in the project, us-east-2): different
    hosts entirely, so this specific combination must pass. This is a
    regression guard on the guard itself -- it must not flag the legitimate
    .env configuration as unsafe.
    """
    assert_test_database_is_isolated(DOTENV_TEST_DATABASE_URL, SHADOWED_REAL_URL)


def test_would_have_caught_test_database_url_pointed_at_the_shadowed_real_db() -> None:
    """The counterfactual: if TEST_DATABASE_URL had ever been set to the
    shadowed value instead of the real .env one, this is what should have
    stopped the run before any migration touched it."""
    with pytest.raises(UnsafeTestDatabaseError):
        assert_test_database_is_isolated(SHADOWED_REAL_URL, SHADOWED_REAL_URL)


def test_declared_dotenv_pair_is_correctly_isolated() -> None:
    """The project's own committed .env: DATABASE_URL and TEST_DATABASE_URL are
    two different Neon endpoints. This must pass -- it is the intended,
    correctly-isolated configuration."""
    assert_test_database_is_isolated(DOTENV_TEST_DATABASE_URL, DOTENV_DATABASE_URL)
