"""SessionStore contract parity: identical behavior proven against BOTH backends.

The "memory" parametrization always runs as part of the normal test suite (no
database required). The "postgres" parametrization exercises the exact same
assertions against PostgresSessionStore and is skipped cleanly when
TEST_DATABASE_URL is not set -- see tests/db/conftest.py.
"""
import pytest

from services.db.postgres_session_store import PostgresSessionStore
from services.session_service import (
    InMemorySessionStore,
    SessionAlreadyExistsError,
    SessionNotFoundError,
    SessionStore,
    StaleSessionVersionError,
)
from tests.db.session_factories import minimal_session


@pytest.fixture(params=["memory", "postgres"])
def store(request: pytest.FixtureRequest) -> SessionStore:
    if request.param == "memory":
        return InMemorySessionStore()
    db_session_factory = request.getfixturevalue("db_session_factory")
    return PostgresSessionStore(db_session_factory)


def test_store_implements_session_store_contract(store: SessionStore) -> None:
    assert isinstance(store, SessionStore)


def test_create_and_get_round_trip(store: SessionStore) -> None:
    store.create(minimal_session("parity-1"))
    fetched = store.get("parity-1")
    assert fetched.id == "parity-1"
    assert fetched.company == "Acme"
    assert fetched.candidate_input.full_name == "Jordan Rivera"
    assert fetched.version == 0


def test_get_returns_independent_copies(store: SessionStore) -> None:
    store.create(minimal_session("parity-2"))
    first = store.get("parity-2")
    second = store.get("parity-2")
    first.company = "Mutated on first copy only"
    assert second.company == "Acme"


def test_mutations_do_not_persist_until_save(store: SessionStore) -> None:
    store.create(minimal_session("parity-3"))
    fetched = store.get("parity-3")
    fetched.company = "Not yet saved"

    unchanged = store.get("parity-3")
    assert unchanged.company == "Acme"

    store.save(fetched)
    persisted = store.get("parity-3")
    assert persisted.company == "Not yet saved"


def test_duplicate_create_is_rejected(store: SessionStore) -> None:
    store.create(minimal_session("parity-4"))
    with pytest.raises(SessionAlreadyExistsError):
        store.create(minimal_session("parity-4"))


def test_get_missing_session_raises(store: SessionStore) -> None:
    with pytest.raises(SessionNotFoundError):
        store.get("parity-does-not-exist")


def test_save_unknown_session_raises(store: SessionStore) -> None:
    with pytest.raises(SessionNotFoundError):
        store.save(minimal_session("parity-never-created"))


def test_save_rejects_a_stale_version(store: SessionStore) -> None:
    store.create(minimal_session("parity-stale"))
    reader_a = store.get("parity-stale")
    reader_b = store.get("parity-stale")

    reader_a.company = "First writer"
    store.save(reader_a)

    reader_b.company = "Second writer, stale"
    with pytest.raises(StaleSessionVersionError):
        store.save(reader_b)

    assert store.get("parity-stale").company == "First writer"


def test_successful_save_increments_version(store: SessionStore) -> None:
    store.create(minimal_session("parity-version"))
    fetched = store.get("parity-version")
    assert fetched.version == 0
    store.save(fetched)
    assert store.get("parity-version").version == 1


def test_multiple_sessions_never_share_nested_candidate_state(store: SessionStore) -> None:
    from models.candidate import CandidateInput

    store.create(minimal_session("parity-iso-a", candidate_input=CandidateInput(
        full_name="Alpha", cv_text="Alpha-only detail.",
    )))
    store.create(minimal_session("parity-iso-b", candidate_input=CandidateInput(
        full_name="Beta", cv_text="Beta-only detail.",
    )))

    session_a = store.get("parity-iso-a")
    session_a.candidate_input.cv_text = "Alpha mutation"
    store.save(session_a)

    persisted_b = store.get("parity-iso-b")
    assert "Beta-only" in persisted_b.candidate_input.cv_text
    assert "Alpha" not in persisted_b.candidate_input.cv_text
