"""EmailDispatchService against a real outbox: the full enqueue -> claim ->
send -> settle cycle, retry-then-succeed, and exhaustion -- using a
controllable fake EmailPort so failure modes are deterministic rather than
depending on a real SMTP relay."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session, sessionmaker

from application.email_dispatch_service import EmailDispatchService
from application.queue_worker import RetryPolicy
from config.settings import Settings
from models.common import EmailOutboxStatus
from models.platform import EmailOutboxRecord
from services.db.email_outbox import SQLAlchemyEmailOutboxRepository
from services.email.base import EmailMessage, EmailPort, EmailProviderError, SendResult

pytestmark = pytest.mark.usefixtures("pg_engine")


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class FakeEmailService(EmailPort):
    """Fails until `fail_for` sends have been attempted, then succeeds.
    fail_for=0 always succeeds."""

    provider_name = "fake"

    def __init__(self, *, fail_for: int = 0) -> None:
        self.fail_for = fail_for
        self.attempts = 0
        self.sent: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> SendResult:
        self.attempts += 1
        if self.attempts <= self.fail_for:
            raise EmailProviderError(f"simulated failure #{self.attempts}")
        self.sent.append(message)
        return SendResult(provider_message_id=f"mid-{self.attempts}")


@pytest.fixture()
def outbox_repo(db_session_factory: sessionmaker[Session]) -> SQLAlchemyEmailOutboxRepository:
    return SQLAlchemyEmailOutboxRepository(db_session_factory)


def _build_service(
    outbox_repo: SQLAlchemyEmailOutboxRepository, email_service: EmailPort, clock: FakeClock,
    *, max_attempts: int = 5,
) -> EmailDispatchService:
    settings = Settings(
        mock_mode=True, email_outbox_max_attempts=max_attempts,
        queue_retry_base_seconds=30, queue_retry_factor=2, queue_retry_max_seconds=900,
    )
    return EmailDispatchService(outbox_repo, email_service, settings, clock=clock)


def _enqueue(service: EmailDispatchService, *, idempotency_key: str = "invite:1") -> None:
    service.enqueue_invitation_email(
        idempotency_key=idempotency_key,
        to_email="jordan@example.com",
        candidate_first_name="Jordan",
        company_name="Acme",
        position_title="Junior AI Engineer",
        interview_url="https://interviews.example/interview/tok",
        expires_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
    )


def test_process_next_returns_none_when_nothing_is_due(
    outbox_repo: SQLAlchemyEmailOutboxRepository,
) -> None:
    clock = FakeClock(datetime.now(timezone.utc))
    service = _build_service(outbox_repo, FakeEmailService(), clock)
    assert service.process_next() is None


def test_enqueue_then_process_next_sends_and_settles(
    outbox_repo: SQLAlchemyEmailOutboxRepository,
) -> None:
    clock = FakeClock(datetime.now(timezone.utc))
    email_service = FakeEmailService()
    service = _build_service(outbox_repo, email_service, clock)
    _enqueue(service)

    settled = service.process_next()

    assert settled is not None
    assert settled.status is EmailOutboxStatus.SENT
    assert settled.provider_message_id == "mid-1"
    assert len(email_service.sent) == 1
    assert email_service.sent[0].to_email == "jordan@example.com"
    assert email_service.sent[0].subject == "Your interview for Junior AI Engineer at Acme"
    assert "https://interviews.example/interview/tok" in email_service.sent[0].text_body

    assert service.process_next() is None  # nothing left to do


def test_enqueue_is_idempotent_across_two_calls(outbox_repo: SQLAlchemyEmailOutboxRepository) -> None:
    clock = FakeClock(datetime.now(timezone.utc))
    service = _build_service(outbox_repo, FakeEmailService(), clock)
    _enqueue(service, idempotency_key="invite:dup")
    _enqueue(service, idempotency_key="invite:dup")

    first_send = service.process_next()
    assert first_send is not None
    assert service.process_next() is None  # the "duplicate" enqueue was a no-op, not a second row


def test_a_failed_send_is_retried_with_deterministic_backoff(
    outbox_repo: SQLAlchemyEmailOutboxRepository,
) -> None:
    clock = FakeClock(datetime.now(timezone.utc))
    email_service = FakeEmailService(fail_for=1)
    service = _build_service(outbox_repo, email_service, clock)
    _enqueue(service)

    first_attempt = service.process_next()
    assert first_attempt is not None
    assert first_attempt.status is EmailOutboxStatus.PENDING
    assert first_attempt.attempts == 1
    expected_next = clock.now + RetryPolicy(base_seconds=30, factor=2, max_seconds=900).delay_for(1)
    assert abs((first_attempt.next_attempt_at - expected_next).total_seconds()) < 1

    # Not due yet.
    assert service.process_next() is None

    clock.advance(timedelta(seconds=31))
    second_attempt = service.process_next()
    assert second_attempt is not None
    assert second_attempt.status is EmailOutboxStatus.SENT
    assert len(email_service.sent) == 1


def test_exhausting_all_attempts_settles_as_failed(outbox_repo: SQLAlchemyEmailOutboxRepository) -> None:
    clock = FakeClock(datetime.now(timezone.utc))
    email_service = FakeEmailService(fail_for=99)
    service = _build_service(outbox_repo, email_service, clock, max_attempts=2)
    _enqueue(service)

    first = service.process_next()
    assert first.status is EmailOutboxStatus.PENDING
    clock.advance(timedelta(seconds=61))
    second = service.process_next()

    assert second.status is EmailOutboxStatus.FAILED
    assert second.payload == {}
    assert second.last_error is not None
    assert email_service.sent == []


def test_a_render_failure_settles_as_failed_on_the_first_attempt(
    outbox_repo: SQLAlchemyEmailOutboxRepository,
) -> None:
    """A missing payload key can never succeed on retry -- it is a
    deterministic authoring bug, not a transient send failure -- so it must
    not burn the retry budget."""
    clock = FakeClock(datetime.now(timezone.utc))
    service = _build_service(outbox_repo, FakeEmailService(), clock)
    outbox_repo.enqueue(EmailOutboxRecord(
        idempotency_key="invite:bad-payload",
        to_email="jordan@example.com",
        template="interview_invitation",
        payload={"candidate_first_name": "Jordan"},  # missing every other key
    ))

    settled = service.process_next()

    assert settled.status is EmailOutboxStatus.FAILED
    assert settled.attempts == 1
