"""Fail-closed guard: a DB-backed test run must never target the real database.

Not a test module itself -- imported by tests/conftest.py's `pg_engine` fixture
and unit-tested directly (no real database needed) in
tests/test_db_isolation_safety.py.

Why this exists: `pg_engine` used to trust TEST_DATABASE_URL unconditionally --
skip if it was unset, otherwise wipe the target schema (`downgrade base` +
`upgrade head`) and run the whole suite against it. Nothing ever checked that
TEST_DATABASE_URL actually pointed somewhere different from DATABASE_URL. If
the two ever resolved to the same server (a copy-pasted connection string, a
stale shell export, a machine-level environment variable shadowing the
project's own .env -- exactly the class of bug found live on this machine
during the P0 performance audit, where a Windows user-level DATABASE_URL
silently overrode every value declared in .env on every process, tests
included), the migration step alone would have dropped and rebuilt the real
schema, and every test's writes would have landed in it permanently. That is
the exact shape of "claim-1@queue-tests.example ... claim-7" turning up in the
development database.
"""

from __future__ import annotations

from sqlalchemy.engine import make_url


class UnsafeTestDatabaseError(Exception):
    """Raised when TEST_DATABASE_URL resolves to the same database as DATABASE_URL.

    A hard failure, not a skip: silently skipping would let exactly this
    misconfiguration through unnoticed, which is the failure mode this guard
    exists to close. A developer who sees this error has a real, immediate
    problem to fix -- TEST_DATABASE_URL is not pointing at a scratch database.
    """


def _target(url: str) -> tuple[str, str]:
    """(host, database name) for a connection string, ignoring driver/query params.

    Two URLs that differ only in `postgresql://` vs `postgresql+psycopg://`, or
    in `sslmode`/`channel_binding` query parameters, still name the same
    physical database and must still be caught -- comparing raw strings alone
    would miss that.
    """
    parsed = make_url(url)
    return (parsed.host or "", parsed.database or "")


def assert_test_database_is_isolated(test_database_url: str, real_database_url: str | None) -> None:
    """Raise UnsafeTestDatabaseError if the test DB is the real one. Otherwise no-op.

    ``real_database_url`` may be None (DATABASE_URL unset entirely) -- with
    nothing real configured, there is nothing to collide with, and PostgreSQL
    dev/prod components using it would already be non-functional regardless of
    what tests do.
    """
    if not real_database_url:
        return

    if test_database_url.strip() == real_database_url.strip():
        raise UnsafeTestDatabaseError(
            "TEST_DATABASE_URL is byte-identical to DATABASE_URL. Refusing to run "
            "DB-backed tests: this would migrate-reset and write test data into "
            "the real database. Point TEST_DATABASE_URL at a separate, disposable "
            "database."
        )

    try:
        test_target = _target(test_database_url)
        real_target = _target(real_database_url)
    except Exception as exc:
        # An unparsable URL is exactly the kind of thing that should stop the
        # run rather than be silently ignored -- fail closed here too.
        raise UnsafeTestDatabaseError(
            f"Could not parse TEST_DATABASE_URL or DATABASE_URL to compare them safely: {exc}"
        ) from exc

    if test_target == real_target:
        raise UnsafeTestDatabaseError(
            f"TEST_DATABASE_URL and DATABASE_URL both resolve to host={test_target[0]!r} "
            f"database={test_target[1]!r}. Refusing to run DB-backed tests against what "
            "appears to be the real database, even though the connection strings differ "
            "textually (e.g. driver prefix or query parameters). Point TEST_DATABASE_URL "
            "at a separate, disposable database."
        )
