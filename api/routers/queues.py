"""Calling-queue endpoints: thin transport only.

Ownership checks, the approved-plan gate, the in-flight guard, and every status
transition live in application/queue_service.py. Nothing here decides anything.

Note what is absent: there is no endpoint that runs a screening. Start/Pause/
Resume only change whether the background worker may claim this queue's items --
the work itself happens in a separate process, so a closed browser tab never
interrupts a candidate's interview.
"""
from fastapi import APIRouter, Depends

from api.dependencies import get_current_user, get_queue_service, get_screening_result_service
from api.schemas.queue import (
    QueueCreateRequest,
    QueueItemCreateRequest,
    QueueItemResponse,
    QueueProgressResponse,
    QueueResponse,
    QueueUpdateRequest,
)
from api.schemas.screening_result import ScreeningResultResponse
from application.queue_service import QueueService
from application.screening_result_service import ScreeningResultService
from models.common import QueueItemStatus
from models.platform import CallQueueRecord, HRUser, QueueItemRecord, QueueProgress

router = APIRouter(prefix="/api/v1/queues", tags=["queues"])


@router.post("", response_model=QueueResponse, status_code=201)
def create_queue(
    payload: QueueCreateRequest,
    current_user: HRUser = Depends(get_current_user),
    service: QueueService = Depends(get_queue_service),
) -> QueueResponse:
    return _to_queue(service.create(current_user, payload.position_id, name=payload.name))


@router.get("", response_model=list[QueueResponse])
def list_queues(
    position_id: int | None = None,
    current_user: HRUser = Depends(get_current_user),
    service: QueueService = Depends(get_queue_service),
) -> list[QueueResponse]:
    return [_to_queue(queue) for queue in service.list(current_user, position_id=position_id)]


@router.get("/{queue_id}", response_model=QueueResponse)
def get_queue(
    queue_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: QueueService = Depends(get_queue_service),
) -> QueueResponse:
    return _to_queue(service.get(current_user, queue_id))


@router.patch("/{queue_id}", response_model=QueueResponse)
def rename_queue(
    queue_id: int,
    payload: QueueUpdateRequest,
    current_user: HRUser = Depends(get_current_user),
    service: QueueService = Depends(get_queue_service),
) -> QueueResponse:
    return _to_queue(service.rename(current_user, queue_id, name=payload.name))


@router.delete("/{queue_id}", status_code=204)
def delete_queue(
    queue_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: QueueService = Depends(get_queue_service),
) -> None:
    service.delete(current_user, queue_id)


@router.post("/{queue_id}/start", response_model=QueueResponse)
def start_queue(
    queue_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: QueueService = Depends(get_queue_service),
) -> QueueResponse:
    return _to_queue(service.start(current_user, queue_id))


@router.post("/{queue_id}/pause", response_model=QueueResponse)
def pause_queue(
    queue_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: QueueService = Depends(get_queue_service),
) -> QueueResponse:
    return _to_queue(service.pause(current_user, queue_id))


@router.post("/{queue_id}/resume", response_model=QueueResponse)
def resume_queue(
    queue_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: QueueService = Depends(get_queue_service),
) -> QueueResponse:
    return _to_queue(service.resume(current_user, queue_id))


@router.get("/{queue_id}/progress", response_model=QueueProgressResponse)
def queue_progress(
    queue_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: QueueService = Depends(get_queue_service),
) -> QueueProgressResponse:
    return _to_progress(service.progress(current_user, queue_id))


@router.get("/{queue_id}/items", response_model=list[QueueItemResponse])
def list_items(
    queue_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: QueueService = Depends(get_queue_service),
) -> list[QueueItemResponse]:
    return [_to_item(item) for item in service.list_items(current_user, queue_id)]


@router.get(
    "/{queue_id}/items/{item_id}/result",
    response_model=ScreeningResultResponse | None,
)
def get_item_result(
    queue_id: int,
    item_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: ScreeningResultService = Depends(get_screening_result_service),
) -> ScreeningResultResponse | None:
    report = service.for_queue_item(current_user, queue_id, item_id)
    return ScreeningResultResponse.from_report(report) if report is not None else None


@router.post("/{queue_id}/items", response_model=QueueItemResponse, status_code=201)
def add_item(
    queue_id: int,
    payload: QueueItemCreateRequest,
    current_user: HRUser = Depends(get_current_user),
    service: QueueService = Depends(get_queue_service),
) -> QueueItemResponse:
    item = service.add_candidate(
        current_user, queue_id, payload.candidate_id, max_attempts=payload.max_attempts,
    )
    return _to_item(item)


@router.delete("/{queue_id}/items/{item_id}", status_code=204)
def remove_item(
    queue_id: int,
    item_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: QueueService = Depends(get_queue_service),
) -> None:
    service.remove_candidate(current_user, queue_id, item_id)


@router.post("/{queue_id}/items/{item_id}/cancel", response_model=QueueItemResponse)
def cancel_item(
    queue_id: int,
    item_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: QueueService = Depends(get_queue_service),
) -> QueueItemResponse:
    return _to_item(service.cancel_item(current_user, queue_id, item_id))


@router.post("/{queue_id}/items/{item_id}/retry", response_model=QueueItemResponse)
def retry_item(
    queue_id: int,
    item_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: QueueService = Depends(get_queue_service),
) -> QueueItemResponse:
    return _to_item(service.retry_item(current_user, queue_id, item_id))


def _to_queue(queue: CallQueueRecord) -> QueueResponse:
    return QueueResponse(
        id=queue.id,
        position_id=queue.position_id,
        name=queue.name,
        status=queue.status,
        kind=queue.kind,
        created_at=queue.created_at,
        updated_at=queue.updated_at,
    )


def _to_item(item: QueueItemRecord) -> QueueItemResponse:
    return QueueItemResponse(
        id=item.id,
        queue_id=item.queue_id,
        candidate_id=item.candidate_id,
        status=item.status,
        attempts=item.attempts,
        max_attempts=item.max_attempts,
        claimed_by=item.claimed_by,
        claimed_at=item.claimed_at,
        lease_expires_at=item.lease_expires_at,
        next_attempt_at=item.next_attempt_at,
        last_error=item.last_error,
        interview_session_id=item.interview_session_id,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _to_progress(progress: QueueProgress) -> QueueProgressResponse:
    # Every status is filled in, including the zeros: a dashboard rendering
    # "0 failed" and one rendering nothing at all are different messages.
    counts = {status: progress.counts.get(status, 0) for status in QueueItemStatus}
    return QueueProgressResponse(
        queue_id=progress.queue_id,
        status=progress.status,
        total=progress.total,
        counts=counts,
        finished=progress.finished,
        in_flight=progress.in_flight,
    )
