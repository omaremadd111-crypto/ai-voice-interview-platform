"""Call queue and queue item repository: interface + PostgreSQL-backed implementation.

Beyond CRUD, this module owns the *atomicity* primitives the background worker
depends on, and nothing else:

  - ``claim_next_item`` takes exactly one pending item using
    ``SELECT ... FOR UPDATE SKIP LOCKED``, so N workers racing on the same queue
    each get a different candidate instead of blocking or double-calling one.
  - ``release_for_retry`` / ``mark_terminal`` / ``mark_in_progress`` /
    ``extend_lease`` are lease-guarded: a worker that lost its lease (because the
    lease expired and another worker recovered the item) cannot write to it.
  - ``recover_expired_leases`` puts a crashed worker's items back in the pool.

The *policy* built on those primitives -- how long to back off, when an attempt
counts as a failure, what a completed screening means -- deliberately lives in
application/queue_worker.py. This layer never decides anything about a candidate.
"""
from abc import ABC, abstractmethod
from datetime import datetime

from sqlalchemy import Select, and_, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from models.common import (
    IN_FLIGHT_QUEUE_ITEM_STATUSES,
    TERMINAL_QUEUE_ITEM_STATUSES,
    QueueItemStatus,
    QueueKind,
    QueueStatus,
)
from models.platform import CallQueueRecord, QueueItemRecord, QueueProgress
from services.db.orm_models import CallQueueRow, QueueItemRow


class QueueRepositoryError(Exception):
    """Base class for queue repository failures."""


class QueueNotFoundError(QueueRepositoryError):
    """Raised when a requested call_queues id does not exist."""


class QueueItemNotFoundError(QueueRepositoryError):
    """Raised when a requested queue_items id does not exist."""


class DuplicateQueueItemError(QueueRepositoryError):
    """Raised when a candidate is already queued in this queue (UNIQUE(queue_id, candidate_id))."""


class LeaseLostError(QueueRepositoryError):
    """Raised when a worker writes to an item whose lease it no longer holds.

    Means another worker recovered the item after this one's lease expired. The
    losing worker must abandon its attempt: the item is someone else's now.
    """


_IN_FLIGHT = tuple(status.value for status in sorted(IN_FLIGHT_QUEUE_ITEM_STATUSES))


class QueueRepository(ABC):
    @abstractmethod
    def create_queue(self, queue: CallQueueRecord) -> CallQueueRecord: ...

    @abstractmethod
    def get_queue(self, queue_id: int) -> CallQueueRecord: ...

    @abstractmethod
    def list_queues(self, *, position_id: int | None = None) -> list[CallQueueRecord]: ...

    @abstractmethod
    def update_queue(self, queue: CallQueueRecord) -> CallQueueRecord: ...

    @abstractmethod
    def delete_queue(self, queue_id: int) -> None: ...

    @abstractmethod
    def add_item(self, item: QueueItemRecord) -> QueueItemRecord: ...

    @abstractmethod
    def get_item(self, item_id: int) -> QueueItemRecord: ...

    @abstractmethod
    def list_items(self, queue_id: int) -> list[QueueItemRecord]: ...

    @abstractmethod
    def get_item_for_candidate(self, queue_id: int, candidate_id: int) -> QueueItemRecord | None: ...

    @abstractmethod
    def update_item(self, item: QueueItemRecord) -> QueueItemRecord: ...

    @abstractmethod
    def delete_item(self, item_id: int) -> None: ...

    @abstractmethod
    def progress(self, queue_id: int) -> QueueProgress: ...

    @abstractmethod
    def claim_next_item(
        self, *, worker_id: str, now: datetime, lease_expires_at: datetime, queue_id: int | None = None,
    ) -> QueueItemRecord | None: ...

    @abstractmethod
    def mark_in_progress(self, item_id: int, *, worker_id: str) -> QueueItemRecord: ...

    @abstractmethod
    def attach_session(
        self, item_id: int, *, worker_id: str, interview_session_id: str,
    ) -> QueueItemRecord: ...

    @abstractmethod
    def extend_lease(self, item_id: int, *, worker_id: str, lease_expires_at: datetime) -> QueueItemRecord: ...

    @abstractmethod
    def release_for_retry(
        self, item_id: int, *, worker_id: str, next_attempt_at: datetime, error: str,
    ) -> QueueItemRecord: ...

    @abstractmethod
    def release_to_awaiting_candidate(
        self, item_id: int, *, worker_id: str, error: str,
    ) -> QueueItemRecord: ...

    @abstractmethod
    def arm_item(self, item_id: int) -> QueueItemRecord: ...

    @abstractmethod
    def claim_for_prewarm(
        self, *, now: datetime, queue_id: int | None = None,
    ) -> QueueItemRecord | None: ...

    @abstractmethod
    def attach_prepared_session(
        self, item_id: int, *, interview_session_id: str,
    ) -> QueueItemRecord: ...

    @abstractmethod
    def mark_terminal(
        self,
        item_id: int,
        *,
        worker_id: str,
        status: QueueItemStatus,
        error: str | None = None,
        interview_session_id: str | None = None,
    ) -> QueueItemRecord: ...

    @abstractmethod
    def recover_expired_leases(self, now: datetime) -> list[QueueItemRecord]: ...


class SQLAlchemyQueueRepository(QueueRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    # --- queue CRUD -------------------------------------------------------

    def create_queue(self, queue: CallQueueRecord) -> CallQueueRecord:
        with self._session_factory() as session:
            row = CallQueueRow(
                position_id=queue.position_id,
                name=queue.name,
                status=queue.status.value,
                kind=queue.kind.value,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _queue_to_model(row)

    def get_queue(self, queue_id: int) -> CallQueueRecord:
        with self._session_factory() as session:
            return _queue_to_model(_require_queue(session, queue_id))

    def list_queues(self, *, position_id: int | None = None) -> list[CallQueueRecord]:
        with self._session_factory() as session:
            stmt = select(CallQueueRow).order_by(CallQueueRow.id)
            if position_id is not None:
                stmt = stmt.where(CallQueueRow.position_id == position_id)
            rows = session.scalars(stmt).all()
            return [_queue_to_model(row) for row in rows]

    def update_queue(self, queue: CallQueueRecord) -> CallQueueRecord:
        if queue.id is None:
            raise ValueError("Cannot update a queue without an id")
        with self._session_factory() as session:
            row = _require_queue(session, queue.id)
            row.name = queue.name
            row.status = queue.status.value
            row.kind = queue.kind.value
            session.commit()
            session.refresh(row)
            return _queue_to_model(row)

    def delete_queue(self, queue_id: int) -> None:
        with self._session_factory() as session:
            session.delete(_require_queue(session, queue_id))
            session.commit()

    # --- item CRUD --------------------------------------------------------

    def add_item(self, item: QueueItemRecord) -> QueueItemRecord:
        with self._session_factory() as session:
            row = QueueItemRow(
                queue_id=item.queue_id,
                candidate_id=item.candidate_id,
                status=item.status.value,
                attempts=item.attempts,
                max_attempts=item.max_attempts,
                claimed_by=item.claimed_by,
                claimed_at=item.claimed_at,
                lease_expires_at=item.lease_expires_at,
                next_attempt_at=item.next_attempt_at,
                last_error=item.last_error,
                interview_session_id=item.interview_session_id,
                prewarm_claimed_at=item.prewarm_claimed_at,
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise DuplicateQueueItemError(
                    f"Candidate '{item.candidate_id}' is already queued in queue '{item.queue_id}'"
                ) from exc
            session.refresh(row)
            return _item_to_model(row)

    def get_item(self, item_id: int) -> QueueItemRecord:
        with self._session_factory() as session:
            return _item_to_model(_require_item(session, item_id))

    def list_items(self, queue_id: int) -> list[QueueItemRecord]:
        with self._session_factory() as session:
            stmt = select(QueueItemRow).where(QueueItemRow.queue_id == queue_id).order_by(QueueItemRow.id)
            return [_item_to_model(row) for row in session.scalars(stmt).all()]

    def get_item_for_candidate(self, queue_id: int, candidate_id: int) -> QueueItemRecord | None:
        """Direct lookup via the same (queue_id, candidate_id) pair uq_queue_candidate
        already indexes -- used by the invitation service to find what a durable
        invitation should point at, without scanning a queue's full item list."""
        with self._session_factory() as session:
            row = session.scalar(
                select(QueueItemRow).where(
                    QueueItemRow.queue_id == queue_id, QueueItemRow.candidate_id == candidate_id,
                )
            )
            return _item_to_model(row) if row is not None else None

    def update_item(self, item: QueueItemRecord) -> QueueItemRecord:
        if item.id is None:
            raise ValueError("Cannot update a queue item without an id")
        with self._session_factory() as session:
            row = _require_item(session, item.id)
            row.status = item.status.value
            row.attempts = item.attempts
            row.max_attempts = item.max_attempts
            row.claimed_by = item.claimed_by
            row.claimed_at = item.claimed_at
            row.lease_expires_at = item.lease_expires_at
            row.next_attempt_at = item.next_attempt_at
            row.last_error = item.last_error
            row.interview_session_id = item.interview_session_id
            row.prewarm_claimed_at = item.prewarm_claimed_at
            session.commit()
            session.refresh(row)
            return _item_to_model(row)

    def delete_item(self, item_id: int) -> None:
        with self._session_factory() as session:
            session.delete(_require_item(session, item_id))
            session.commit()

    def progress(self, queue_id: int) -> QueueProgress:
        with self._session_factory() as session:
            queue = _require_queue(session, queue_id)
            rows = session.execute(
                select(QueueItemRow.status, func.count())
                .where(QueueItemRow.queue_id == queue_id)
                .group_by(QueueItemRow.status)
            ).all()
            counts = {QueueItemStatus(status): count for status, count in rows}
            return QueueProgress(
                queue_id=queue_id,
                status=QueueStatus(queue.status),
                total=sum(counts.values()),
                counts=counts,
            )

    # --- worker primitives ------------------------------------------------

    def claim_next_item(
        self, *, worker_id: str, now: datetime, lease_expires_at: datetime, queue_id: int | None = None,
    ) -> QueueItemRecord | None:
        """Atomically take ownership of the next eligible item, or return None.

        Everything happens in one transaction: the row is locked with
        FOR UPDATE SKIP LOCKED, then flipped to CLAIMED with this worker's lease.
        A concurrent worker running the identical statement skips the locked row
        and takes the next one, so no candidate is ever called twice.

        Claiming an item consumes an attempt (``attempts += 1``) up front rather
        than on completion, so a worker that dies mid-call cannot retry forever.
        """
        with self._session_factory() as session:
            row = session.scalars(_claimable_query(now, queue_id)).first()
            if row is None:
                return None
            row.status = QueueItemStatus.CLAIMED.value
            row.attempts += 1
            row.claimed_by = worker_id
            row.claimed_at = now
            row.lease_expires_at = lease_expires_at
            row.next_attempt_at = None
            session.commit()
            session.refresh(row)
            return _item_to_model(row)

    def mark_in_progress(self, item_id: int, *, worker_id: str) -> QueueItemRecord:
        return self._lease_guarded_update(
            item_id, worker_id, {"status": QueueItemStatus.IN_PROGRESS.value},
        )

    def attach_session(
        self, item_id: int, *, worker_id: str, interview_session_id: str,
    ) -> QueueItemRecord:
        """Expose the prepared session while the candidate invitation is active."""
        return self._lease_guarded_update(
            item_id, worker_id, {"interview_session_id": interview_session_id},
        )

    def extend_lease(self, item_id: int, *, worker_id: str, lease_expires_at: datetime) -> QueueItemRecord:
        return self._lease_guarded_update(item_id, worker_id, {"lease_expires_at": lease_expires_at})

    def release_for_retry(
        self, item_id: int, *, worker_id: str, next_attempt_at: datetime, error: str,
    ) -> QueueItemRecord:
        """Hand the item back to the pool with a backoff deadline.

        ``attempts`` is left untouched -- it was already incremented when the item
        was claimed, so the spent attempt stays spent.
        """
        return self._lease_guarded_update(item_id, worker_id, {
            "status": QueueItemStatus.PENDING.value,
            "claimed_by": None,
            "claimed_at": None,
            "lease_expires_at": None,
            "next_attempt_at": next_attempt_at,
            "last_error": error,
            "interview_session_id": None,
        })

    def release_to_awaiting_candidate(
        self, item_id: int, *, worker_id: str, error: str,
    ) -> QueueItemRecord:
        """The auto-pipeline counterpart to release_for_retry: hand a failed
        attempt back to AWAITING_CANDIDATE instead of PENDING, so the worker
        never re-dispatches a room on its own timer. The candidate must press
        Start again (QueueRepository.arm_item) before this item is claimable.
        next_attempt_at is left null -- there is no time-based retry to wait
        for here, only a candidate action. attempts stays spent, exactly like
        release_for_retry.
        """
        return self._lease_guarded_update(item_id, worker_id, {
            "status": QueueItemStatus.AWAITING_CANDIDATE.value,
            "claimed_by": None,
            "claimed_at": None,
            "lease_expires_at": None,
            "next_attempt_at": None,
            "last_error": error,
            "interview_session_id": None,
            # Cleared alongside the session it was claimed for, so a retried
            # attempt is eligible for pre-warming again on its next Start --
            # see claim_for_prewarm.
            "prewarm_claimed_at": None,
        })

    def arm_item(self, item_id: int) -> QueueItemRecord:
        """Flip AWAITING_CANDIDATE -> PENDING so the worker may claim it.

        A conditional update guarded on the current status, not a lease --
        nothing holds a lease on an item still in AWAITING_CANDIDATE. Matches
        no row (already armed, claimed, or settled) is a deliberate no-op: the
        caller (the public "Start interview" endpoint) always re-reads and
        returns the item's current state either way, so a double-click or a
        retried request can never re-arm an item a worker already has, and
        never raises for what is, from the candidate's side, just "already
        started".
        """
        with self._session_factory() as session:
            session.execute(
                update(QueueItemRow)
                .where(
                    QueueItemRow.id == item_id,
                    QueueItemRow.status == QueueItemStatus.AWAITING_CANDIDATE.value,
                )
                .values(status=QueueItemStatus.PENDING.value, next_attempt_at=None)
            )
            session.commit()
            return _item_to_model(_require_item(session, item_id))

    def claim_for_prewarm(
        self, *, now: datetime, queue_id: int | None = None,
    ) -> QueueItemRecord | None:
        """Atomically take ownership of one AWAITING_CANDIDATE item with no
        session yet, for background preparation -- the pre-warming counterpart
        to claim_next_item. Uses the same FOR UPDATE SKIP LOCKED primitive so
        two workers never prepare the same candidate twice, but sets no
        status, lease, or claimed_by: those all mean "an interview attempt is
        in flight", which pre-warming never is. The item stays
        AWAITING_CANDIDATE throughout -- unclaimable by claim_next_item, and
        reported as "not_started" by InterviewLandingService -- so no LiveKit
        room opens and no join timeout starts from this.

        If pre-warming is interrupted before attach_prepared_session runs,
        this item is simply never retried: QueueWorker._run_item() prepares it
        live at claim time instead, exactly as it did before pre-warming
        existed. That fallback is what makes the marker safe to set-and-forget
        with no lease or recovery of its own.
        """
        with self._session_factory() as session:
            row = session.scalars(_prewarmable_query(queue_id)).first()
            if row is None:
                return None
            row.prewarm_claimed_at = now
            session.commit()
            session.refresh(row)
            return _item_to_model(row)

    def attach_prepared_session(
        self, item_id: int, *, interview_session_id: str,
    ) -> QueueItemRecord:
        """Store a pre-warmed session on an item the candidate has not
        started yet (or has just armed but no worker has claimed). No lease is
        required -- nothing holds one this early -- so this is a plain
        conditional update, guarded on interview_session_id still being unset
        so it can never overwrite a session a live attempt
        (QueueWorker._run_item) already attached itself.

        AWAITING_CANDIDATE or PENDING both qualify: PENDING covers the narrow
        window where the candidate has already pressed Start but no worker has
        claimed the item yet. Matching no row (a worker already claimed and
        attached its own session, or the item settled) is a deliberate no-op --
        see QueueWorker.prewarm_next(), which treats the session it just
        prepared as a harmless, never-referenced orphan in that rare race
        rather than raising.
        """
        with self._session_factory() as session:
            session.execute(
                update(QueueItemRow)
                .where(
                    QueueItemRow.id == item_id,
                    QueueItemRow.status.in_((
                        QueueItemStatus.AWAITING_CANDIDATE.value, QueueItemStatus.PENDING.value,
                    )),
                    QueueItemRow.interview_session_id.is_(None),
                )
                .values(interview_session_id=interview_session_id)
            )
            session.commit()
            return _item_to_model(_require_item(session, item_id))

    def mark_terminal(
        self,
        item_id: int,
        *,
        worker_id: str,
        status: QueueItemStatus,
        error: str | None = None,
        interview_session_id: str | None = None,
    ) -> QueueItemRecord:
        if status not in TERMINAL_QUEUE_ITEM_STATUSES:
            raise ValueError(f"'{status.value}' is not a terminal queue item status")
        values: dict[str, object] = {
            "status": status.value,
            "claimed_by": None,
            "claimed_at": None,
            "lease_expires_at": None,
            "next_attempt_at": None,
            "last_error": error,
            "interview_session_id": interview_session_id,
        }
        return self._lease_guarded_update(item_id, worker_id, values)

    def recover_expired_leases(self, now: datetime) -> list[QueueItemRecord]:
        """Return items whose worker stopped renewing its lease to the pending pool.

        This is what makes a crashed (or network-partitioned) worker survivable:
        the item is not stuck IN_PROGRESS forever, it simply becomes claimable
        again -- unless its attempts are already exhausted, in which case it
        settles as FAILED so nobody retries it silently.
        """
        with self._session_factory() as session:
            expired = session.scalars(
                select(QueueItemRow)
                .where(
                    QueueItemRow.status.in_(_IN_FLIGHT),
                    QueueItemRow.lease_expires_at.is_not(None),
                    QueueItemRow.lease_expires_at < now,
                )
                .order_by(QueueItemRow.id)
                .with_for_update(skip_locked=True)
            ).all()
            recovered: list[QueueItemRecord] = []
            for row in expired:
                exhausted = row.attempts >= row.max_attempts
                row.status = (
                    QueueItemStatus.FAILED.value if exhausted else QueueItemStatus.PENDING.value
                )
                row.claimed_by = None
                row.claimed_at = None
                row.lease_expires_at = None
                row.next_attempt_at = None if exhausted else now
                row.last_error = (
                    f"Worker lease expired after {row.attempts} attempt(s); no attempts remaining."
                    if exhausted
                    else "Worker lease expired; the item was returned to the queue."
                )
                row.interview_session_id = None
                recovered.append(row)
            session.commit()
            return [_item_to_model(row) for row in recovered]

    def _lease_guarded_update(
        self, item_id: int, worker_id: str, values: dict[str, object],
    ) -> QueueItemRecord:
        with self._session_factory() as session:
            result = session.execute(
                update(QueueItemRow)
                .where(
                    QueueItemRow.id == item_id,
                    QueueItemRow.claimed_by == worker_id,
                    QueueItemRow.status.in_(_IN_FLIGHT),
                )
                .values(**values)
            )
            if result.rowcount == 0:
                session.rollback()
                row = session.get(QueueItemRow, item_id)
                if row is None:
                    raise QueueItemNotFoundError(f"Queue item '{item_id}' was not found")
                raise LeaseLostError(
                    f"Worker '{worker_id}' no longer holds the lease on queue item '{item_id}' "
                    f"(status '{row.status}')"
                )
            session.commit()
            return _item_to_model(_require_item(session, item_id))


def _claimable_query(now: datetime, queue_id: int | None) -> Select[tuple[QueueItemRow]]:
    """The one definition of "which item may a worker take next".

    Four conditions, all enforced in SQL so they hold under concurrency:
      1. the item is pending and its backoff deadline (if any) has passed;
      2. its queue is RUNNING -- a paused queue yields nothing;
      3. the candidate is not already in flight in ANY queue, which is what stops
         the same person being called twice at once;
      4. the row is not already locked by another worker (SKIP LOCKED).

    Ordering is by id: strictly first-in, first-out, with no randomness anywhere.
    """
    other = QueueItemRow.__table__.alias("other_item")
    candidate_busy_elsewhere = exists(
        select(other.c.id).where(
            and_(
                other.c.candidate_id == QueueItemRow.candidate_id,
                other.c.id != QueueItemRow.id,
                other.c.status.in_(_IN_FLIGHT),
            )
        )
    )
    stmt = (
        select(QueueItemRow)
        .join(CallQueueRow, CallQueueRow.id == QueueItemRow.queue_id)
        .where(
            QueueItemRow.status == QueueItemStatus.PENDING.value,
            or_(QueueItemRow.next_attempt_at.is_(None), QueueItemRow.next_attempt_at <= now),
            CallQueueRow.status == QueueStatus.RUNNING.value,
            ~candidate_busy_elsewhere,
        )
        .order_by(QueueItemRow.id)
        .limit(1)
        # of=QueueItemRow: lock only the work item. Locking the joined call_queues
        # row too would serialize every worker on the same queue, defeating the
        # point of SKIP LOCKED.
        .with_for_update(skip_locked=True, of=QueueItemRow)
    )
    return stmt


def _prewarmable_query(queue_id: int | None = None) -> Select[tuple[QueueItemRow]]:
    """Which AWAITING_CANDIDATE item may a worker pre-warm next.

    Deliberately narrower than _claimable_query: only items nobody has started
    preparing (prewarm_claimed_at IS NULL) that don't already have a session
    (interview_session_id IS NULL), in a RUNNING, auto-pipeline queue. No
    next_attempt_at/backoff concept applies -- pre-warming is a one-shot,
    best-effort optimization, never a retried attempt -- and no "candidate
    busy elsewhere" guard either: preparing a session touches no LiveKit room
    and competes with nothing, unlike claiming an item to actually call.
    """
    stmt = (
        select(QueueItemRow)
        .join(CallQueueRow, CallQueueRow.id == QueueItemRow.queue_id)
        .where(
            QueueItemRow.status == QueueItemStatus.AWAITING_CANDIDATE.value,
            QueueItemRow.interview_session_id.is_(None),
            QueueItemRow.prewarm_claimed_at.is_(None),
            CallQueueRow.status == QueueStatus.RUNNING.value,
            CallQueueRow.kind == QueueKind.AUTO.value,
        )
        .order_by(QueueItemRow.id)
        .limit(1)
        .with_for_update(skip_locked=True, of=QueueItemRow)
    )
    if queue_id is not None:
        stmt = stmt.where(QueueItemRow.queue_id == queue_id)
    return stmt


def _require_queue(session: Session, queue_id: int) -> CallQueueRow:
    row = session.get(CallQueueRow, queue_id)
    if row is None:
        raise QueueNotFoundError(f"Queue '{queue_id}' was not found")
    return row


def _require_item(session: Session, item_id: int) -> QueueItemRow:
    row = session.get(QueueItemRow, item_id)
    if row is None:
        raise QueueItemNotFoundError(f"Queue item '{item_id}' was not found")
    return row


def _queue_to_model(row: CallQueueRow) -> CallQueueRecord:
    return CallQueueRecord(
        id=row.id,
        position_id=row.position_id,
        name=row.name,
        status=QueueStatus(row.status),
        kind=QueueKind(row.kind),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _item_to_model(row: QueueItemRow) -> QueueItemRecord:
    return QueueItemRecord(
        id=row.id,
        queue_id=row.queue_id,
        candidate_id=row.candidate_id,
        status=QueueItemStatus(row.status),
        attempts=row.attempts,
        max_attempts=row.max_attempts,
        claimed_by=row.claimed_by,
        claimed_at=row.claimed_at,
        lease_expires_at=row.lease_expires_at,
        next_attempt_at=row.next_attempt_at,
        last_error=row.last_error,
        interview_session_id=row.interview_session_id,
        prewarm_claimed_at=row.prewarm_claimed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
