"""Candidate endpoints: thin transport only. Ownership enforcement and
persistence orchestration live in application/candidate_service.py.

GET /candidates requires a position_id query parameter -- candidates are always
browsed within a position (matching the HR-review workflow); no cross-position
aggregation endpoint exists in P3.
"""
from fastapi import APIRouter, Depends, File, UploadFile

from api.dependencies import get_candidate_service, get_current_user, get_screening_result_service
from api.schemas.candidate import CandidateCreateRequest, CandidateResponse, CandidateUpdateRequest
from api.schemas.screening_result import ScreeningResultResponse
from application.candidate_service import CandidateService
from application.screening_result_service import ScreeningResultService
from models.platform import CandidateRecord, HRUser

router = APIRouter(prefix="/api/v1/candidates", tags=["candidates"])


@router.post("", response_model=CandidateResponse, status_code=201)
def create_candidate(
    payload: CandidateCreateRequest,
    current_user: HRUser = Depends(get_current_user),
    service: CandidateService = Depends(get_candidate_service),
) -> CandidateResponse:
    fields = payload.model_dump(exclude={"position_id"})
    candidate = service.create(current_user, payload.position_id, **fields)
    return _to_response(candidate)


@router.get("", response_model=list[CandidateResponse])
def list_candidates(
    position_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: CandidateService = Depends(get_candidate_service),
) -> list[CandidateResponse]:
    return [_to_response(c) for c in service.list_for_position(current_user, position_id)]


@router.get("/{candidate_id}", response_model=CandidateResponse)
def get_candidate(
    candidate_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: CandidateService = Depends(get_candidate_service),
) -> CandidateResponse:
    return _to_response(service.get(current_user, candidate_id))


@router.get("/{candidate_id}/result", response_model=ScreeningResultResponse | None)
def get_candidate_result(
    candidate_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: ScreeningResultService = Depends(get_screening_result_service),
) -> ScreeningResultResponse | None:
    report = service.latest_for_candidate(current_user, candidate_id)
    return ScreeningResultResponse.from_report(report) if report is not None else None


@router.patch("/{candidate_id}", response_model=CandidateResponse)
def update_candidate(
    candidate_id: int,
    payload: CandidateUpdateRequest,
    current_user: HRUser = Depends(get_current_user),
    service: CandidateService = Depends(get_candidate_service),
) -> CandidateResponse:
    updates = payload.model_dump(exclude_unset=True)
    return _to_response(service.update(current_user, candidate_id, updates))


@router.post("/{candidate_id}/cv", response_model=CandidateResponse)
async def upload_cv(
    candidate_id: int,
    file: UploadFile = File(...),
    current_user: HRUser = Depends(get_current_user),
    service: CandidateService = Depends(get_candidate_service),
) -> CandidateResponse:
    """Upload a CV file (PDF/DOCX/TXT/MD); the backend extracts its text with the
    existing DocumentParser and stores it on the candidate.

    async purely because reading an UploadFile is awaitable -- the extraction and
    persistence beneath it stay synchronous like the rest of the application.
    """
    data = await file.read()
    candidate = service.upload_cv(
        current_user, candidate_id, data=data, filename=file.filename or "cv",
    )
    return _to_response(candidate)


def _to_response(candidate: CandidateRecord) -> CandidateResponse:
    return CandidateResponse(
        id=candidate.id,
        position_id=candidate.position_id,
        full_name=candidate.full_name,
        email=candidate.email,
        phone=candidate.phone,
        cv_text=candidate.cv_text,
        cv_filename=candidate.cv_filename,
        status=candidate.status,
    )
