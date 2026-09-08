"""Calling-queue request/response schemas.

Timestamps and worker bookkeeping (claimed_by, lease_expires_at) are exposed
because a recruiter needs to see whether a screening is genuinely in flight or
merely stuck. Nothing evaluative crosses this boundary: no scores, no screening
outcomes, no transcript content -- those already have their own endpoints, and a
queue is about scheduling, not assessment.
"""
from datetime import datetime

from pydantic import BaseModel, Field

from models.common import QueueItemStatus, QueueKind, QueueStatus


class QueueCreateRequest(BaseModel):
    position_id: int
    name: str = Field(min_length=1, max_length=255)


class QueueUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class QueueResponse(BaseModel):
    id: int
    position_id: int
    name: str
    status: QueueStatus
    #: 'manual' (recruiter-built) or 'auto' (the one queue the automated
    #: screening pipeline creates per published position). The dashboard uses
    #: this to keep the two apart rather than mixing an unmanaged auto queue
    #: into a recruiter's own batch list.
    kind: QueueKind = QueueKind.MANUAL
    created_at: datetime | None = None
    updated_at: datetime | None = None


class QueueItemCreateRequest(BaseModel):
    candidate_id: int
    # Bounded: an unbounded retry count would let a queue call the same person
    # indefinitely. Three is the schema default everywhere else too.
    max_attempts: int = Field(default=3, ge=1, le=10)


class QueueItemResponse(BaseModel):
    id: int
    queue_id: int
    candidate_id: int
    status: QueueItemStatus
    attempts: int
    max_attempts: int
    claimed_by: str | None
    claimed_at: datetime | None
    lease_expires_at: datetime | None
    next_attempt_at: datetime | None
    last_error: str | None
    interview_session_id: str | None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class QueueProgressResponse(BaseModel):
    queue_id: int
    status: QueueStatus
    total: int
    #: Count per QueueItemStatus, including zeros, so the dashboard never has to
    #: guess whether a missing key means "none" or "not reported".
    counts: dict[QueueItemStatus, int]
    finished: int
    in_flight: int
