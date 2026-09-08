"""Email outbox enqueue + drain: the async half of invitation delivery.

Enqueue is called synchronously from the two callers that mint a plaintext
invitation token -- ApplicationPipelineService (automatic, after Apply) and
PipelineService (recruiter-triggered resend). They are the only places the
plaintext token exists at all: interview_invitations stores only its SHA-256
hash, so the rendered payload is built here, while the token is still in
memory, not reconstructed later. Draining runs one row at a time on a
QueueWorker-adjacent background pass (see worker.py), mirroring
QueueWorker.process_next's shape -- including reusing RetryPolicy for the
exact same deterministic backoff, rather than inventing a second one.
"""
import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from application.interview_invitation_service import IssuedInvitation
from application.queue_worker import RetryPolicy
from config.settings import PROJECT_ROOT, Settings
from models.platform import EmailOutboxRecord, JobApplicationRecord, Position
from services.db.email_outbox import EmailOutboxRepository
from services.email.base import EmailConfigurationError, EmailMessage, EmailPort, EmailProviderError
from services.logging_service import Event, log_event

_logger = logging.getLogger("interview_agent.email_dispatch")

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EmailDispatchServiceError(Exception):
    """Base class for email dispatch use-case failures."""


class TemplateRenderError(EmailDispatchServiceError):
    """Raised when a template's placeholders don't match its payload. This is
    a deterministic authoring bug, never a transient send failure -- retrying
    the same payload against the same template can never succeed, so
    process_next settles it as failed on the first attempt instead of
    burning the retry budget."""


_TEMPLATES_DIR = PROJECT_ROOT / "templates" / "email"

#: One subject line per template, kept in code rather than a third template
#: file since each is a single formatted line.
_SUBJECTS: dict[str, str] = {
    "interview_invitation": "Your interview for {position_title} at {company_name}",
}


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    text_body: str
    html_body: str | None


def render_template(template: str, payload: dict) -> RenderedEmail:
    subject_format = _SUBJECTS.get(template)
    if subject_format is None:
        raise TemplateRenderError(f"No subject configured for template '{template}'")
    text_path = _TEMPLATES_DIR / f"{template}.txt"
    html_path = _TEMPLATES_DIR / f"{template}.html"
    try:
        subject = subject_format.format(**payload)
        text_body = text_path.read_text(encoding="utf-8").format(**payload)
        html_body = html_path.read_text(encoding="utf-8").format(**payload) if html_path.exists() else None
    except KeyError as exc:
        raise TemplateRenderError(f"Template '{template}' is missing payload key {exc}") from exc
    return RenderedEmail(subject=subject, text_body=text_body, html_body=html_body)


class EmailDispatchService:
    def __init__(
        self,
        outbox_repo: EmailOutboxRepository,
        email_service: EmailPort,
        settings: Settings,
        clock: Clock = utc_now,
    ) -> None:
        self._outbox_repo = outbox_repo
        self._email_service = email_service
        self._max_attempts = settings.email_outbox_max_attempts
        self._retry_policy = RetryPolicy(
            base_seconds=settings.queue_retry_base_seconds,
            factor=settings.queue_retry_factor,
            max_seconds=settings.queue_retry_max_seconds,
        )
        self._clock = clock

    def enqueue_invitation_email(
        self,
        *,
        idempotency_key: str,
        to_email: str,
        candidate_first_name: str,
        company_name: str,
        position_title: str,
        interview_url: str,
        expires_at: datetime,
    ) -> EmailOutboxRecord:
        """Idempotent: calling this twice with the same idempotency_key (a
        retried apply(), a duplicate resend click) returns the existing row
        instead of queueing a second email -- see
        SQLAlchemyEmailOutboxRepository.enqueue."""
        payload = {
            "candidate_first_name": candidate_first_name,
            "company_name": company_name,
            "position_title": position_title,
            "interview_url": interview_url,
            # strftime's no-leading-zero day flag differs by platform (%-d on
            # Linux/Mac, %#d on Windows) -- built by hand instead so this
            # renders identically everywhere.
            "expires_at_display": f"{expires_at:%B} {expires_at.day}, {expires_at:%Y}",
        }
        record = self._outbox_repo.enqueue(EmailOutboxRecord(
            idempotency_key=idempotency_key,
            to_email=to_email,
            template="interview_invitation",
            payload=payload,
            max_attempts=self._max_attempts,
        ))
        log_event(Event.EMAIL_QUEUED, template=record.template, outbox_id=record.id)
        return record

    def enqueue_invitation_email_for(
        self,
        application: JobApplicationRecord,
        position: Position,
        issued: IssuedInvitation,
        *,
        base_url: str,
    ) -> EmailOutboxRecord:
        """Convenience wrapper for the two callers that already hold these
        three domain objects (ApplicationPipelineService's automatic path,
        PipelineService's recruiter-triggered issue/resend) -- keeps
        first-name extraction and idempotency-key derivation in one place
        instead of duplicated in both."""
        first_name = application.full_name.strip().split(" ")[0] if application.full_name.strip() else "there"
        # Same SHA-256 a fresh invitation's own token_hash would be -- a
        # stable, collision-free outbox key per distinct issued token, with
        # no need to thread the invitation row's id back out of
        # issue_or_rotate just for this.
        token_fingerprint = hashlib.sha256(issued.token.encode("ascii")).hexdigest()
        return self.enqueue_invitation_email(
            idempotency_key=f"invite:{token_fingerprint}",
            to_email=application.email_normalized,
            candidate_first_name=first_name,
            company_name=position.company_name,
            position_title=position.title,
            interview_url=f"{base_url}/interview/{issued.token}",
            expires_at=issued.expires_at,
        )

    def process_next(self, *, reserve_seconds: int = 60) -> EmailOutboxRecord | None:
        """Claim and settle one due row. Never raises for a per-row failure --
        the same justification as QueueWorker.process_next: one bad template
        payload or one down SMTP relay must not stall every other queued
        email."""
        claimed = self._outbox_repo.claim_due(now=self._clock(), reserve_seconds=reserve_seconds)
        if claimed is None:
            return None

        try:
            rendered = render_template(claimed.template, claimed.payload)
        except TemplateRenderError as exc:
            _logger.error("email_outbox_render_failed outbox_id=%s error=%s", claimed.id, exc)
            log_event(Event.EMAIL_FAILED, outbox_id=claimed.id, reason="render_error")
            return self._outbox_repo.mark_failed(claimed.id, error=str(exc))

        try:
            result = self._email_service.send(EmailMessage(
                to_email=claimed.to_email,
                subject=rendered.subject,
                text_body=rendered.text_body,
                html_body=rendered.html_body,
            ))
        except (EmailProviderError, EmailConfigurationError) as exc:
            message = str(exc)
            if claimed.attempts < claimed.max_attempts:
                next_attempt_at = self._clock() + self._retry_policy.delay_for(claimed.attempts)
                _logger.warning(
                    "email_outbox_retry_scheduled outbox_id=%s attempt=%s/%s",
                    claimed.id, claimed.attempts, claimed.max_attempts,
                )
                log_event(Event.EMAIL_FAILED, outbox_id=claimed.id, attempt=claimed.attempts, will_retry=True)
                return self._outbox_repo.mark_retry(
                    claimed.id, next_attempt_at=next_attempt_at, error=message,
                )
            _logger.error(
                "email_outbox_exhausted outbox_id=%s attempts=%s", claimed.id, claimed.attempts,
            )
            log_event(Event.EMAIL_FAILED, outbox_id=claimed.id, attempt=claimed.attempts, will_retry=False)
            return self._outbox_repo.mark_failed(claimed.id, error=message)

        return self._outbox_repo.mark_sent(
            claimed.id, now=self._clock(), provider_message_id=result.provider_message_id,
        )
