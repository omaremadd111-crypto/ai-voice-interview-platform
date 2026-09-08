"""One full cycle of every background pass this process runs: real interview
attempts and pre-warming (QueueWorker), draining the email outbox
(EmailDispatchService), and sweeping stale invitations to expired.

A composed class rather than folding email/invitation concerns into
QueueWorker itself, so neither needs to import the other --
EmailDispatchService already depends on QueueWorker's RetryPolicy, so the
reverse dependency would be circular. This is the only module that imports
both.
"""
from collections.abc import Callable
from datetime import datetime, timezone

from application.email_dispatch_service import EmailDispatchService
from application.queue_worker import QueueWorker
from services.db.invitations import InterviewInvitationRepository

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorkerCycle:
    def __init__(
        self,
        queue_worker: QueueWorker,
        email_dispatch: EmailDispatchService,
        invitation_repo: InterviewInvitationRepository,
        *,
        poll_interval_seconds: float = 5.0,
        invitation_sweep_interval_seconds: float = 300.0,
        clock: Clock = utc_now,
    ) -> None:
        self._queue_worker = queue_worker
        self._email_dispatch = email_dispatch
        self._invitation_repo = invitation_repo
        self._poll_interval_seconds = poll_interval_seconds
        self._invitation_sweep_interval_seconds = invitation_sweep_interval_seconds
        self._clock = clock
        self._last_invitation_sweep: datetime | None = None

    @property
    def worker_id(self) -> str:
        return self._queue_worker.worker_id

    def run_once(self) -> bool:
        """One ordered pass, cheapest/most time-sensitive first: a real
        interview attempt always takes priority, then pre-warming the next
        applicant, then draining one due email, then -- at most once per
        invitation_sweep_interval_seconds -- sweeping expired invitations.
        Returns True if any pass did real work, so run_forever knows whether
        to sleep or immediately loop again."""
        if self._queue_worker.process_next() is not None:
            return True
        if self._queue_worker.prewarm_next() is not None:
            return True
        if self._email_dispatch.process_next() is not None:
            return True
        return self._sweep_invitations_if_due()

    def _sweep_invitations_if_due(self) -> bool:
        now = self._clock()
        if (
            self._last_invitation_sweep is not None
            and (now - self._last_invitation_sweep).total_seconds()
            < self._invitation_sweep_interval_seconds
        ):
            return False
        self._last_invitation_sweep = now
        expired = self._invitation_repo.expire_stale(now=now)
        return bool(expired)

    def run_forever(self, sleep: Callable[[float], None], *, should_continue: Callable[[], bool]) -> None:
        while should_continue():
            if not self.run_once():
                sleep(self._poll_interval_seconds)
