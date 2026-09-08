"""Calling-queue use cases: ownership-enforced CRUD, membership, and run control.

Everything a recruiter can do to a queue lives here. The background worker does
NOT use this service -- it works from the repository's lease primitives directly
(see application/queue_worker.py), because a worker has no logged-in user and
must never inherit a recruiter's permissions by accident.

Two rules are enforced at this boundary and nowhere else:

  * Tenant isolation. A queue is reached only through its position, and the
    position is fetched via PositionService, which raises ForbiddenError for
    anyone but the owner. There is no code path to a queue by id alone.
  * The human-approval gate. A candidate cannot be enqueued until a recruiter has
    approved their interview plan (SPEC 7). The worker re-checks this at call
    time, because a plan can be edited back to draft after enqueueing.
"""
# `QueueService.list` shadows the builtin inside the class body, so every
# annotation written after it -- list[QueueItemRecord] and friends -- would be
# evaluated against the method. Deferring annotation evaluation keeps the method
# name consistent with PositionService.list/AgentConfigService.list.
from __future__ import annotations

from models.common import CandidateStatus, QueueItemStatus, QueueStatus
from models.platform import CallQueueRecord, CandidateRecord, HRUser, QueueItemRecord, QueueProgress
from application.position_service import PositionService
from services.db.candidates import CandidateRepository
from services.db.interview_plans import InterviewPlanRepository
from services.db.queues import QueueRepository


class QueueServiceError(Exception):
    """Base class for calling-queue use-case failures."""


class CandidatePositionMismatchError(QueueServiceError):
    """Raised when a candidate is added to a queue belonging to a different position."""


class PlanNotApprovedError(QueueServiceError):
    """Raised when a candidate is enqueued without a recruiter-approved interview plan.

    Not a technical constraint -- it is the human-in-the-loop gate. Nobody gets
    called with questions no person signed off on.
    """


class QueueItemInFlightError(QueueServiceError):
    """Raised when a recruiter edits an item a worker is currently processing.

    Removing or cancelling mid-call would leave the worker writing to a row that
    no longer means anything, so the caller is told to wait instead.
    """


class ItemNotInQueueError(QueueServiceError):
    """Raised when an item id does not belong to the queue named in the request."""


class QueueService:
    def __init__(
        self,
        queue_repo: QueueRepository,
        position_service: PositionService,
        candidate_repo: CandidateRepository,
        plan_repo: InterviewPlanRepository,
    ) -> None:
        self._queue_repo = queue_repo
        self._position_service = position_service
        self._candidate_repo = candidate_repo
        self._plan_repo = plan_repo

    # --- queue CRUD -------------------------------------------------------

    def create(self, owner: HRUser, position_id: int, *, name: str) -> CallQueueRecord:
        self._position_service.get(owner, position_id)
        return self._queue_repo.create_queue(CallQueueRecord(position_id=position_id, name=name))

    def get(self, owner: HRUser, queue_id: int) -> CallQueueRecord:
        queue = self._queue_repo.get_queue(queue_id)
        # Proves ownership via the queue's own position; raises ForbiddenError
        # for anyone else before any queue data is returned.
        self._position_service.get(owner, queue.position_id)
        return queue

    def list(self, owner: HRUser, *, position_id: int | None = None) -> list[CallQueueRecord]:
        if position_id is not None:
            self._position_service.get(owner, position_id)
            return self._queue_repo.list_queues(position_id=position_id)
        owned_position_ids = {position.id for position in self._position_service.list(owner)}
        return [
            queue
            for queue in self._queue_repo.list_queues()
            if queue.position_id in owned_position_ids
        ]

    def rename(self, owner: HRUser, queue_id: int, *, name: str) -> CallQueueRecord:
        queue = self.get(owner, queue_id)
        return self._queue_repo.update_queue(queue.model_copy(update={"name": name}))

    def delete(self, owner: HRUser, queue_id: int) -> None:
        self.get(owner, queue_id)
        self._queue_repo.delete_queue(queue_id)

    # --- run control ------------------------------------------------------

    def start(self, owner: HRUser, queue_id: int) -> CallQueueRecord:
        """Make the queue's pending items claimable by the background worker."""
        return self._set_status(owner, queue_id, QueueStatus.RUNNING)

    def pause(self, owner: HRUser, queue_id: int) -> CallQueueRecord:
        """Stop handing out new items.

        Deliberately does not interrupt an in-flight screening: cutting off a
        conversation already under way would lose the transcript for no benefit.
        Pausing takes effect from the next claim.
        """
        return self._set_status(owner, queue_id, QueueStatus.PAUSED)

    def resume(self, owner: HRUser, queue_id: int) -> CallQueueRecord:
        return self._set_status(owner, queue_id, QueueStatus.RUNNING)

    def _set_status(self, owner: HRUser, queue_id: int, status: QueueStatus) -> CallQueueRecord:
        queue = self.get(owner, queue_id)
        if queue.status is status:
            return queue
        return self._queue_repo.update_queue(queue.model_copy(update={"status": status}))

    # --- membership -------------------------------------------------------

    def add_candidate(
        self,
        owner: HRUser,
        queue_id: int,
        candidate_id: int,
        *,
        max_attempts: int = 3,
        # AWAITING_CANDIDATE for the automated pipeline (see
        # application/application_pipeline_service.py): the item exists but is
        # deliberately not yet claimable -- see QueueItemStatus's docstring.
        # PENDING (default) preserves every existing manual-queue caller unchanged.
        initial_status: QueueItemStatus = QueueItemStatus.PENDING,
    ) -> QueueItemRecord:
        queue = self.get(owner, queue_id)
        candidate = self._candidate_repo.get(candidate_id)
        if candidate.position_id != queue.position_id:
            raise CandidatePositionMismatchError(
                f"Candidate {candidate_id} belongs to position {candidate.position_id}, "
                f"but queue {queue_id} screens for position {queue.position_id}"
            )
        self._require_approved_plan(candidate)

        item = self._queue_repo.add_item(QueueItemRecord(
            queue_id=queue_id, candidate_id=candidate_id, max_attempts=max_attempts,
            status=initial_status,
        ))
        # QUEUED is a pipeline position, not an assessment of the person. Covers
        # both a manually-added candidate (NEW) and a self-applied one (APPLIED,
        # see CandidateStatus's docstring) -- either way, this is the first time
        # they have entered a queue.
        if candidate.status in (CandidateStatus.NEW, CandidateStatus.APPLIED):
            self._candidate_repo.update(candidate.model_copy(update={"status": CandidateStatus.QUEUED}))
        return item

    def list_items(self, owner: HRUser, queue_id: int) -> list[QueueItemRecord]:
        self.get(owner, queue_id)
        return self._queue_repo.list_items(queue_id)

    def get_item(self, owner: HRUser, queue_id: int, item_id: int) -> QueueItemRecord:
        """Return one item only after proving it belongs to an owned queue."""
        return self._require_item_in_queue(owner, queue_id, item_id)

    def remove_candidate(self, owner: HRUser, queue_id: int, item_id: int) -> None:
        item = self._require_item_in_queue(owner, queue_id, item_id)
        _reject_if_in_flight(item, "removed")
        self._queue_repo.delete_item(item_id)

    def cancel_item(self, owner: HRUser, queue_id: int, item_id: int) -> QueueItemRecord:
        """Take an item out of consideration while keeping its history on file."""
        item = self._require_item_in_queue(owner, queue_id, item_id)
        _reject_if_in_flight(item, "cancelled")
        return self._queue_repo.update_item(item.model_copy(update={
            "status": QueueItemStatus.CANCELLED,
            "next_attempt_at": None,
            "last_error": None,
        }))

    def retry_item(self, owner: HRUser, queue_id: int, item_id: int) -> QueueItemRecord:
        """Give an exhausted or cancelled item a fresh set of attempts.

        An explicit recruiter action, never automatic: the worker's own retries
        are bounded by max_attempts and stop there.
        """
        item = self._require_item_in_queue(owner, queue_id, item_id)
        _reject_if_in_flight(item, "retried")
        if item.status is QueueItemStatus.COMPLETED:
            raise QueueItemInFlightError(
                f"Queue item {item_id} already completed; add the candidate to a new queue "
                f"to screen them again."
            )
        return self._queue_repo.update_item(item.model_copy(update={
            "status": QueueItemStatus.PENDING,
            "attempts": 0,
            "claimed_by": None,
            "claimed_at": None,
            "lease_expires_at": None,
            "next_attempt_at": None,
            "last_error": None,
        }))

    # --- progress ---------------------------------------------------------

    def progress(self, owner: HRUser, queue_id: int) -> QueueProgress:
        self.get(owner, queue_id)
        return self._queue_repo.progress(queue_id)

    # --- internals --------------------------------------------------------

    def _require_item_in_queue(self, owner: HRUser, queue_id: int, item_id: int) -> QueueItemRecord:
        self.get(owner, queue_id)
        item = self._queue_repo.get_item(item_id)
        if item.queue_id != queue_id:
            # Checked rather than trusted: without it, an owner of queue A could
            # reach an item in someone else's queue B by guessing its id.
            raise ItemNotInQueueError(f"Queue item {item_id} does not belong to queue {queue_id}")
        return item

    def _require_approved_plan(self, candidate: CandidateRecord) -> None:
        plan = self._plan_repo.get_for_candidate(candidate.id)
        if plan is None or not plan.is_approved or not plan.questions:
            raise PlanNotApprovedError(
                f"{candidate.full_name} has no approved interview plan. Review and approve "
                f"their plan before adding them to a calling queue."
            )


def _reject_if_in_flight(item: QueueItemRecord, action: str) -> None:
    if item.status in (QueueItemStatus.CLAIMED, QueueItemStatus.IN_PROGRESS):
        raise QueueItemInFlightError(
            f"Queue item {item.id} is currently being screened and cannot be {action}. "
            f"Pause the queue and try again once the call has finished."
        )
