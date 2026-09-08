"""Rate limiting for the public application-intake endpoint.

DB-backed, not in-process: the API may run as more than one uvicorn worker
process (the audit's own recommendation once concurrent public traffic is
real), and an in-memory counter would only ever see its own process's share of
requests. The applications already on file are the ground truth this counts
against -- see JobApplicationRepository.count_recent_for_ip /
count_recent_for_position, which read job_applications directly rather than
keeping a second, driftable counter structure.
"""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from services.db.applications import JobApplicationRepository

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RateLimitExceededError(Exception):
    """Raised when a public application would exceed a configured limit.
    The message is deliberately generic -- it must never reveal how close to
    the limit a position or IP actually is."""


@dataclass(frozen=True)
class RateLimitSettings:
    #: Applications accepted from one submitter IP in a rolling hour.
    per_ip_per_hour: int = 5

    def __post_init__(self) -> None:
        if self.per_ip_per_hour < 1:
            raise ValueError("per_ip_per_hour must be at least 1")


class ApplicationRateLimiter:
    def __init__(
        self,
        application_repo: JobApplicationRepository,
        settings: RateLimitSettings,
        clock: Clock = utc_now,
    ) -> None:
        self._application_repo = application_repo
        self._settings = settings
        self._clock = clock

    def check(
        self, *, position_id: int, ip_hash: str | None, max_applications_per_day: int | None,
    ) -> None:
        """Raises RateLimitExceededError if this NEW application would exceed
        either limit. Only ever called for a genuinely new (position, email)
        pair -- see ApplicationPipelineService.apply(): a resubmission of an
        application already on file never counts against either limit."""
        now = self._clock()
        if ip_hash is not None:
            since = now - timedelta(hours=1)
            if self._application_repo.count_recent_for_ip(ip_hash, since) >= self._settings.per_ip_per_hour:
                raise RateLimitExceededError(
                    "Too many applications submitted recently. Please try again later."
                )
        if max_applications_per_day is not None:
            since = now - timedelta(days=1)
            if (
                self._application_repo.count_recent_for_position(position_id, since)
                >= max_applications_per_day
            ):
                raise RateLimitExceededError(
                    "This position is not accepting new applications right now. Please try again later."
                )
