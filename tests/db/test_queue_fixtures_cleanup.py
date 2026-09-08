"""Regression test for QueueScenario.cleanup() leaking its owner HRUser row.

Root cause of "we found real test data inside the development Neon database"
(claim-1@queue-tests.example ... claim-7): cleanup() deleted only the Position
it created. Candidates/plans/queues/items cascade from that FK, but the owning
HRUser does not (an owner outliving their positions is correct in the real
product), so every real run of the queue-claiming/queue-worker suites against
any database left a permanent orphaned HRUser row behind. Combined with a
misconfigured TEST_DATABASE_URL ever pointing at a real database (the second
half of this incident -- see tests/test_db_isolation_safety.py for the guard
against that), repeated runs accumulate exactly the observed claim-N... trail.
"""

from __future__ import annotations

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

import pytest

from services.db.hr_users import HRUserNotFoundError, SQLAlchemyHRUserRepository
from services.db.orm_models import HRUserRow
from tests.db.queue_fixtures import autonomous_factory, build_scenario

pytestmark = pytest.mark.usefixtures("pg_engine")


@pytest.fixture()
def factory(pg_engine: Engine) -> sessionmaker[Session]:
    return autonomous_factory(pg_engine)


def test_cleanup_deletes_the_owner_it_created(factory: sessionmaker[Session]) -> None:
    scenario = build_scenario(factory, label="cleanup-owner-check", candidates=1)
    owner_id = scenario.owner.id
    user_repo = SQLAlchemyHRUserRepository(factory)

    # Sanity: the owner really was persisted before cleanup runs.
    assert user_repo.get(owner_id).id == owner_id

    scenario.cleanup()

    with pytest.raises(HRUserNotFoundError):
        user_repo.get(owner_id)


def test_cleanup_leaves_no_queue_tests_example_rows_behind(
    factory: sessionmaker[Session],
) -> None:
    """The exact shape of the incident: no row whose email matches the
    fixture's own naming scheme should survive cleanup()."""
    scenario = build_scenario(factory, label="no-leftovers", candidates=2)
    scenario.cleanup()

    with factory() as session:
        leftover = session.scalars(
            select(HRUserRow).where(HRUserRow.email.like("no-leftovers-%@queue-tests.example"))
        ).all()
    assert leftover == []


def test_repeated_scenario_and_cleanup_cycles_leave_the_database_as_found(
    factory: sessionmaker[Session],
) -> None:
    """Simulates what actually happened: the same test running many times in
    a session. Row counts before and after N full cycles must be identical --
    if they are not, something is leaking on every single run, exactly like
    the original bug."""
    with factory() as session:
        before = session.scalar(select(HRUserRow.id).limit(1).order_by(HRUserRow.id.desc()))
        before_count = len(session.scalars(select(HRUserRow.id)).all())

    for i in range(3):
        scenario = build_scenario(factory, label=f"cycle-{i}", candidates=2)
        scenario.cleanup()

    with factory() as session:
        after_count = len(session.scalars(select(HRUserRow.id)).all())

    assert after_count == before_count, (
        f"expected the same row count after 3 build+cleanup cycles, "
        f"before={before_count} after={after_count} (before max id={before})"
    )
