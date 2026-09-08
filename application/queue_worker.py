"""The reliable background queue worker.

Runs in its own process (see worker.py at the repo root), completely independent
of any browser session: closing the dashboard does not stop a screening, and
restarting the worker does not lose one.

One cycle is:

    recover expired leases
      -> claim one item atomically (FOR UPDATE SKIP LOCKED, in the repository)
      -> re-check the recruiter's plan approval
      -> prepare the interview via InterviewPreparationService
      -> hand the session to the transport (null/mock today, LiveKit later)
      -> evaluate through InterviewAgentService
      -> settle the item, move to the next candidate

What this module deliberately does NOT contain: any interview logic. It never
asks a question, never decides on a follow-up, never scores anything. Preparation
goes through InterviewPreparationService (which already reuses
InterviewAgentService.prepare_from_position), the conversation goes through the
transport, and the evaluation is InterviewAgentService's, unchanged. The worker
only decides *which candidate is next* and *what a failed attempt means*.
"""
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from application.interview_preparation_service import InterviewPreparationService
from application.interview_agent_service import InterviewAgentService
from models.common import CandidateStatus, QueueItemStatus, QueueKind
from models.platform import HRUser, QueueItemRecord
from services.db.candidates import CandidateRepository
from services.db.hr_users import HRUserRepository
from services.db.interview_plans import InterviewPlanRepository
from services.db.positions import PositionRepository
from services.db.queues import LeaseLostError, QueueRepository
from services.interview_transport.base import (
    InterviewTransport,
    InterviewTransportContext,
    TransportOutcome,
)

_logger = logging.getLogger("interview_agent.queue_worker")

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class RetryPolicy:
    """Deterministic exponential backoff.

    No jitter and no randomness: two workers computing the delay for the same
    attempt number always agree, and a test can assert the exact schedule. The
    thundering-herd problem jitter solves does not arise here, because
    SKIP LOCKED already guarantees only one worker takes any given item.
    """

    base_seconds: int = 30
    factor: int = 2
    max_seconds: int = 900

    def __post_init__(self) -> None:
        if self.base_seconds <= 0:
            raise ValueError("base_seconds must be positive")
        if self.factor < 1:
            raise ValueError("factor must be at least 1")
        if self.max_seconds < self.base_seconds:
            raise ValueError("max_seconds must not be smaller than base_seconds")

    def delay_for(self, attempts: int) -> timedelta:
        """Delay before the attempt following ``attempts`` spent attempts."""
        if attempts < 1:
            raise ValueError("attempts must be at least 1 when computing a backoff delay")
        seconds = min(self.base_seconds * (self.factor ** (attempts - 1)), self.max_seconds)
        return timedelta(seconds=seconds)


@dataclass(frozen=True)
class WorkerSettings:
    worker_id: str
    lease_seconds: int = 300
    poll_interval_seconds: float = 5.0
    retry_policy: RetryPolicy = RetryPolicy()


@dataclass(frozen=True)
class ProcessedItem:
    """What one cycle did, for logs, tests, and the demo -- never for the candidate.

    Carries ids and a queue status only: no answers, no scores, no names.
    """

    item_id: int
    candidate_id: int
    queue_id: int
    status: QueueItemStatus
    attempts: int
    session_id: str | None = None
    detail: str | None = None


class QueueWorker:
    def __init__(
        self,
        queue_repo: QueueRepository,
        candidate_repo: CandidateRepository,
        hr_user_repo: HRUserRepository,
        position_repo: PositionRepository,
        plan_repo: InterviewPlanRepository,
        preparation_service: InterviewPreparationService,
        agent_service: InterviewAgentService,
        transport: InterviewTransport,
        settings: WorkerSettings,
        clock: Clock = utc_now,
    ) -> None:
        self._queue_repo = queue_repo
        self._candidate_repo = candidate_repo
        self._hr_user_repo = hr_user_repo
        self._position_repo = position_repo
        self._plan_repo = plan_repo
        self._preparation_service = preparation_service
        self._agent_service = agent_service
        self._transport = transport
        self._settings = settings
        self._clock = clock

    @property
    def worker_id(self) -> str:
        return self._settings.worker_id

    def recover_expired_leases(self) -> list[QueueItemRecord]:
        recovered = self._queue_repo.recover_expired_leases(self._clock())
        for item in recovered:
            _logger.warning(
                "queue_item_lease_recovered item_id=%s queue_id=%s status=%s attempts=%s",
                item.id, item.queue_id, item.status.value, item.attempts,
            )
        return recovered

    def process_next(self) -> ProcessedItem | None:
        """Claim and fully process one item. Returns None when nothing is claimable.

        Never raises for a per-item failure: an unexpected error is recorded on the
        item and retried or settled, so one bad candidate record cannot take the
        worker down and stall every other queue.
        """
        self.recover_expired_leases()

        now = self._clock()
        item = self._queue_repo.claim_next_item(
            worker_id=self.worker_id,
            now=now,
            lease_expires_at=now + timedelta(seconds=self._settings.lease_seconds),
        )
        if item is None:
            return None

        _logger.info(
            "queue_item_claimed item_id=%s queue_id=%s candidate_id=%s attempt=%s/%s worker=%s",
            item.id, item.queue_id, item.candidate_id, item.attempts, item.max_attempts, self.worker_id,
        )
        try:
            return self._run_item(item)
        except LeaseLostError as exc:
            # Another worker recovered this item mid-flight. Abandon it silently:
            # the new owner is authoritative, and writing here would corrupt it.
            _logger.warning("queue_item_lease_lost item_id=%s detail=%s", item.id, exc)
            return ProcessedItem(
                item_id=item.id, candidate_id=item.candidate_id, queue_id=item.queue_id,
                status=QueueItemStatus.PENDING, attempts=item.attempts,
                detail="Lease lost to another worker; attempt abandoned.",
            )
        except Exception as exc:  # noqa: BLE001 -- see docstring: never kill the loop
            _logger.error(
                "queue_item_unexpected_error item_id=%s error=%s", item.id, type(exc).__name__,
            )
            return self._settle_failure(
                item, TransportOutcome.FAILED, f"{type(exc).__name__}: {exc}",
            )

    def run_until_idle(self, *, max_items: int | None = None) -> list[ProcessedItem]:
        """Drain everything currently claimable, then stop.

        This is what the demo and the tests drive; ``run_forever`` is the same
        cycle with a sleep between passes.
        """
        processed: list[ProcessedItem] = []
        while max_items is None or len(processed) < max_items:
            result = self.process_next()
            if result is None:
                break
            processed.append(result)
        return processed

    def run_forever(self, sleep: Callable[[float], None], *, should_continue: Callable[[], bool]) -> None:
        """The long-running loop. ``sleep`` and ``should_continue`` are injected so
        the process entry point owns signal handling and this stays testable.

        Real interview attempts (process_next) always take priority; pre-
        warming (prewarm_next) only runs on a pass with no live attempt to
        process, so a busy worker never delays a candidate who is already
        waiting in order to prepare one who has not pressed Start yet."""
        while should_continue():
            if self.process_next() is not None:
                continue
            if self.prewarm_next() is None:
                sleep(self._settings.poll_interval_seconds)

    def prewarm_next(self) -> QueueItemRecord | None:
        """Run the expensive, Start-independent part of preparation for one
        auto-pipeline candidate who has applied but not started yet.

        JobAnalyzer/CandidateAnalyzer/FitAnalyzer and creating the
        InterviewSession are the slow part of an interview attempt and depend
        on nothing the candidate does at Start -- they only need the position
        and the candidate's own CV, both already on file at Apply time. Doing
        that work here, while the item is still AWAITING_CANDIDATE, means
        _run_item() can skip straight to dispatch once the candidate actually
        presses Start.

        This never opens a LiveKit room or starts a join timeout: the item's
        status stays AWAITING_CANDIDATE the entire time (claim_for_prewarm
        never changes it), and InterviewLandingService._stage_for() only ever
        reports "ready" -- the one stage that mints a voice invitation -- from
        IN_PROGRESS, which only arm_item() (Start) followed by a real claim can
        reach. A pre-warming failure is only ever a missed optimization, never
        a hard failure: it is logged and the item is left exactly as
        claim_for_prewarm found it, so the live attempt after Start prepares
        it itself, exactly as it did before pre-warming existed.
        """
        item = self._queue_repo.claim_for_prewarm(now=self._clock())
        if item is None:
            return None
        try:
            candidate = self._candidate_repo.get(item.candidate_id)
            plan = self._plan_repo.get_for_candidate(candidate.id)
            if plan is None or not plan.is_approved or not plan.questions:
                # Nothing to prepare with yet -- the live attempt after Start
                # will hit this exact check and fail with the same message.
                return item
            owner = self._owner_for(candidate.position_id)
            prepared = self._preparation_service.prepare(owner, candidate.position_id, candidate.id)
            self._agent_service.approve_plan(prepared.session_id)
            attached = self._queue_repo.attach_prepared_session(
                item.id, interview_session_id=prepared.session_id,
            )
            _logger.info(
                "queue_item_prewarmed item_id=%s candidate_id=%s session_id=%s",
                item.id, item.candidate_id, prepared.session_id,
            )
            return attached
        except Exception as exc:  # noqa: BLE001 -- a pre-warm failure must never block Start
            _logger.warning(
                "queue_item_prewarm_failed item_id=%s error=%s", item.id, type(exc).__name__,
            )
            return item

    # --- one item ---------------------------------------------------------

    def _run_item(self, item: QueueItemRecord) -> ProcessedItem:
        candidate = self._candidate_repo.get(item.candidate_id)

        # Re-checked here even though QueueService checked it at enqueue time: a
        # recruiter can edit a plan after queueing, which returns it to draft.
        # Unapproved questions must never reach a candidate.
        plan = self._plan_repo.get_for_candidate(candidate.id)
        if plan is None or not plan.is_approved or not plan.questions:
            return self._settle_terminal(
                item,
                QueueItemStatus.FAILED,
                "The candidate's interview plan is not approved, so no call was made.",
            )

        owner = self._owner_for(candidate.position_id)
        self._queue_repo.mark_in_progress(item.id, worker_id=self.worker_id)
        self._candidate_repo.update(
            candidate.model_copy(update={"status": CandidateStatus.SCREENING_IN_PROGRESS}),
        )

        if item.interview_session_id is not None:
            # Pre-warmed at Apply time (see prewarm_next()): prepare() already
            # ran and approve_plan() already moved the session to READY.
            # Calling either again here would be wrong, not just wasteful --
            # approve_plan() is only valid from CREATED, so a second call would
            # raise on a session that is already READY. Reuse it as is.
            session_id = item.interview_session_id
        else:
            prepared = self._preparation_service.prepare(owner, candidate.position_id, candidate.id)
            self._agent_service.approve_plan(prepared.session_id)
            session_id = prepared.session_id
            self._queue_repo.attach_session(
                item.id, worker_id=self.worker_id, interview_session_id=session_id,
            )

        def heartbeat() -> None:
            now = self._clock()
            self._queue_repo.extend_lease(
                item.id,
                worker_id=self.worker_id,
                lease_expires_at=now + timedelta(seconds=self._settings.lease_seconds),
            )

        result = self._transport.run(
            session_id,
            self._agent_service,
            context=InterviewTransportContext(
                queue_item_id=item.id,
                candidate_id=candidate.id,
                position_id=candidate.position_id,
                heartbeat=heartbeat,
            ),
        )
        if result.outcome is not TransportOutcome.COMPLETED:
            return self._settle_failure(item, result.outcome, result.detail)

        # Evaluation is InterviewAgentService's alone -- deterministic scoring and
        # transcript-traceable evidence stay exactly where they already live.
        self._agent_service.evaluate_interview(session_id)
        # A completed platform interview always has a persisted recruiter report.
        # InterviewAgentService remains the sole implementation of report logic.
        self._agent_service.generate_report(session_id)

        self._candidate_repo.update(candidate.model_copy(update={"status": CandidateStatus.SCREENED}))
        settled = self._queue_repo.mark_terminal(
            item.id,
            worker_id=self.worker_id,
            status=QueueItemStatus.COMPLETED,
            interview_session_id=session_id,
        )
        _logger.info(
            "queue_item_completed item_id=%s candidate_id=%s session_id=%s transport=%s",
            item.id, item.candidate_id, session_id, self._transport.name,
        )
        return ProcessedItem(
            item_id=settled.id, candidate_id=settled.candidate_id, queue_id=settled.queue_id,
            status=settled.status, attempts=settled.attempts, session_id=session_id,
        )

    def _settle_failure(
        self, item: QueueItemRecord, outcome: TransportOutcome, detail: str | None,
    ) -> ProcessedItem:
        """Retry with backoff while attempts remain; otherwise settle terminally.

        A NO_ANSWER that runs out of attempts stays NO_ANSWER rather than becoming
        FAILED, so the dashboard can tell "we could not reach them" apart from
        "something broke". Neither is a statement about the candidate.
        """
        message = detail or f"The call ended as '{outcome.value}'."
        self._release_candidate(item.candidate_id)
        if item.attempts < item.max_attempts:
            # Auto-pipeline items (see models/common.py QueueKind) never get a
            # timer-based retry: re-opening a room on our own schedule makes no
            # sense when nothing but the candidate pressing Start again can put
            # someone in it. They go back to AWAITING_CANDIDATE instead, exactly
            # where a fresh application starts -- see QueueRepository.arm_item.
            if self._queue_repo.get_queue(item.queue_id).kind is QueueKind.AUTO:
                released = self._queue_repo.release_to_awaiting_candidate(
                    item.id, worker_id=self.worker_id, error=message,
                )
                _logger.info(
                    "queue_item_returned_to_awaiting_candidate item_id=%s attempt=%s/%s",
                    item.id, released.attempts, released.max_attempts,
                )
                return ProcessedItem(
                    item_id=released.id, candidate_id=released.candidate_id, queue_id=released.queue_id,
                    status=released.status, attempts=released.attempts, detail=message,
                )

            next_attempt_at = self._clock() + self._settings.retry_policy.delay_for(item.attempts)
            released = self._queue_repo.release_for_retry(
                item.id,
                worker_id=self.worker_id,
                next_attempt_at=next_attempt_at,
                error=message,
            )
            _logger.info(
                "queue_item_retry_scheduled item_id=%s attempt=%s/%s next_attempt_at=%s",
                item.id, released.attempts, released.max_attempts, next_attempt_at.isoformat(),
            )
            return ProcessedItem(
                item_id=released.id, candidate_id=released.candidate_id, queue_id=released.queue_id,
                status=released.status, attempts=released.attempts, detail=message,
            )

        terminal = (
            QueueItemStatus.NO_ANSWER
            if outcome is TransportOutcome.NO_ANSWER
            else QueueItemStatus.FAILED
        )
        return self._settle_terminal(
            item, terminal, f"{message} No attempts remaining ({item.attempts}/{item.max_attempts}).",
        )

    def _settle_terminal(
        self, item: QueueItemRecord, status: QueueItemStatus, message: str,
    ) -> ProcessedItem:
        self._release_candidate(item.candidate_id)
        settled = self._queue_repo.mark_terminal(
            item.id, worker_id=self.worker_id, status=status, error=message,
        )
        _logger.info(
            "queue_item_settled item_id=%s status=%s attempts=%s",
            settled.id, settled.status.value, settled.attempts,
        )
        return ProcessedItem(
            item_id=settled.id, candidate_id=settled.candidate_id, queue_id=settled.queue_id,
            status=settled.status, attempts=settled.attempts, detail=message,
        )

    def _release_candidate(self, candidate_id: int) -> None:
        """Take a candidate out of SCREENING_IN_PROGRESS when the attempt ends badly.

        Every unsuccessful exit routes through here -- including the catch-all in
        process_next() -- so a crash mid-preparation cannot leave someone showing
        as "screening in progress" forever. QUEUED, not SCREENED: no conversation
        happened, so nothing about them has been established.
        """
        candidate = self._candidate_repo.get(candidate_id)
        if candidate.status is CandidateStatus.SCREENING_IN_PROGRESS:
            self._candidate_repo.update(
                candidate.model_copy(update={"status": CandidateStatus.QUEUED}),
            )

    def _owner_for(self, position_id: int) -> HRUser:
        """Act on behalf of the recruiter who owns the position.

        The worker has no session of its own. Rather than giving it an
        ownership-free back door, it loads the owning user and passes that user
        into the same tenant-isolated services the API calls -- so a worker can
        only ever touch data that recruiter could touch themselves.
        """
        position = self._position_repo.get(position_id)
        return self._hr_user_repo.get(position.owner_id)
