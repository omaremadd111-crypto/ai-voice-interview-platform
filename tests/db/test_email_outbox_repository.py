"""EmailOutboxRepository: enqueue idempotency, the claim/reserve primitive
that stands in for a lease (see models/common.py EmailOutboxStatus for why no
CLAIMED status is needed), and settling a row sent/retried/failed."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session, sessionmaker

from models.common import EmailOutboxStatus
from models.platform import EmailOutboxRecord
from services.db.email_outbox import EmailOutboxNotFoundError, SQLAlchemyEmailOutboxRepository

pytestmark = pytest.mark.usefixtures("pg_engine")


@pytest.fixture()
def repo(db_session_factory: sessionmaker[Session]) -> SQLAlchemyEmailOutboxRepository:
    return SQLAlchemyEmailOutboxRepository(db_session_factory)


def _record(**overrides: object) -> EmailOutboxRecord:
    defaults = dict(
        idempotency_key="invite:test-key",
        to_email="jordan@example.com",
        template="interview_invitation",
        payload={"interview_url": "https://example.com/interview/tok"},
    )
    defaults.update(overrides)
    return EmailOutboxRecord(**defaults)


def test_enqueue_creates_a_pending_row(repo: SQLAlchemyEmailOutboxRepository) -> None:
    created = repo.enqueue(_record())
    assert created.id is not None
    assert created.status is EmailOutboxStatus.PENDING
    assert created.attempts == 0


def test_enqueue_is_idempotent_on_the_same_key(repo: SQLAlchemyEmailOutboxRepository) -> None:
    first = repo.enqueue(_record(idempotency_key="invite:dup"))
    second = repo.enqueue(_record(idempotency_key="invite:dup", to_email="someone-else@example.com"))
    assert second.id == first.id
    assert second.to_email == "jordan@example.com"  # the original row, untouched


def test_get_by_idempotency_key_returns_none_when_absent(repo: SQLAlchemyEmailOutboxRepository) -> None:
    assert repo.get_by_idempotency_key("invite:does-not-exist") is None


def test_get_missing_row_raises(repo: SQLAlchemyEmailOutboxRepository) -> None:
    with pytest.raises(EmailOutboxNotFoundError):
        repo.get(999999)


def test_claim_due_takes_a_fresh_pending_row_and_consumes_an_attempt(
    repo: SQLAlchemyEmailOutboxRepository,
) -> None:
    created = repo.enqueue(_record(idempotency_key="invite:claim-1"))
    now = datetime.now(timezone.utc)

    claimed = repo.claim_due(now=now, reserve_seconds=60)

    assert claimed is not None
    assert claimed.id == created.id
    assert claimed.attempts == 1
    assert claimed.status is EmailOutboxStatus.PENDING  # stays pending -- no CLAIMED status
    assert claimed.next_attempt_at is not None
    assert claimed.next_attempt_at >= now + timedelta(seconds=59)


def test_claim_due_does_not_return_a_row_still_within_its_reservation(
    repo: SQLAlchemyEmailOutboxRepository,
) -> None:
    repo.enqueue(_record(idempotency_key="invite:claim-2"))
    now = datetime.now(timezone.utc)
    first = repo.claim_due(now=now, reserve_seconds=60)
    assert first is not None

    again = repo.claim_due(now=now, reserve_seconds=60)
    assert again is None


def test_claim_due_recovers_a_row_whose_reservation_has_expired(
    repo: SQLAlchemyEmailOutboxRepository,
) -> None:
    """Simulates a worker that claimed a row and then crashed mid-send: no
    lease/heartbeat machinery needed, the row just becomes due again once its
    reservation window passes."""
    created = repo.enqueue(_record(idempotency_key="invite:claim-3"))
    now = datetime.now(timezone.utc)
    first = repo.claim_due(now=now, reserve_seconds=30)
    assert first is not None

    later = now + timedelta(seconds=31)
    recovered = repo.claim_due(now=later, reserve_seconds=30)
    assert recovered is not None
    assert recovered.id == created.id
    assert recovered.attempts == 2  # the crashed attempt still counts


def test_claim_due_ignores_rows_that_are_not_pending(repo: SQLAlchemyEmailOutboxRepository) -> None:
    created = repo.enqueue(_record(idempotency_key="invite:claim-4"))
    repo.mark_sent(created.id, now=datetime.now(timezone.utc), provider_message_id="mid-1")
    assert repo.claim_due(now=datetime.now(timezone.utc), reserve_seconds=60) is None


def test_mark_sent_settles_and_clears_the_payload(repo: SQLAlchemyEmailOutboxRepository) -> None:
    created = repo.enqueue(_record(idempotency_key="invite:sent-1"))
    now = datetime.now(timezone.utc)
    repo.claim_due(now=now, reserve_seconds=60)

    settled = repo.mark_sent(created.id, now=now, provider_message_id="mid-abc")

    assert settled.status is EmailOutboxStatus.SENT
    assert settled.sent_at is not None
    assert settled.provider_message_id == "mid-abc"
    assert settled.next_attempt_at is None
    assert settled.payload == {}


def test_mark_retry_keeps_status_pending_and_schedules_the_next_attempt(
    repo: SQLAlchemyEmailOutboxRepository,
) -> None:
    created = repo.enqueue(_record(idempotency_key="invite:retry-1"))
    repo.claim_due(now=datetime.now(timezone.utc), reserve_seconds=60)

    next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=30)
    retried = repo.mark_retry(created.id, next_attempt_at=next_attempt_at, error="SMTP timed out")

    assert retried.status is EmailOutboxStatus.PENDING
    assert retried.last_error == "SMTP timed out"
    assert abs((retried.next_attempt_at - next_attempt_at).total_seconds()) < 1
    # The payload survives a retry -- it is needed again for the next attempt.
    assert retried.payload == {"interview_url": "https://example.com/interview/tok"}


def test_mark_failed_settles_and_clears_the_payload(repo: SQLAlchemyEmailOutboxRepository) -> None:
    created = repo.enqueue(_record(idempotency_key="invite:failed-1"))
    repo.claim_due(now=datetime.now(timezone.utc), reserve_seconds=60)

    failed = repo.mark_failed(created.id, error="Attempts exhausted")

    assert failed.status is EmailOutboxStatus.FAILED
    assert failed.last_error == "Attempts exhausted"
    assert failed.next_attempt_at is None
    assert failed.payload == {}
