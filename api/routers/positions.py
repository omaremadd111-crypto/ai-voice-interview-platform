"""Position endpoints: thin transport only. Ownership enforcement and
persistence orchestration live in application/position_service.py."""
from fastapi import APIRouter, Depends

from api.dependencies import get_current_user, get_position_service
from api.schemas.position import PositionCreateRequest, PositionResponse, PositionUpdateRequest
from application.position_service import PositionService
from models.common import PositionStatus
from models.platform import HRUser, Position

router = APIRouter(prefix="/api/v1/positions", tags=["positions"])


@router.post("", response_model=PositionResponse, status_code=201)
def create_position(
    payload: PositionCreateRequest,
    current_user: HRUser = Depends(get_current_user),
    service: PositionService = Depends(get_position_service),
) -> PositionResponse:
    position = service.create(current_user, **payload.model_dump())
    return _to_response(position)


@router.get("", response_model=list[PositionResponse])
def list_positions(
    status: PositionStatus | None = None,
    current_user: HRUser = Depends(get_current_user),
    service: PositionService = Depends(get_position_service),
) -> list[PositionResponse]:
    return [_to_response(p) for p in service.list(current_user, status=status)]


@router.get("/{position_id}", response_model=PositionResponse)
def get_position(
    position_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: PositionService = Depends(get_position_service),
) -> PositionResponse:
    return _to_response(service.get(current_user, position_id))


@router.patch("/{position_id}", response_model=PositionResponse)
def update_position(
    position_id: int,
    payload: PositionUpdateRequest,
    current_user: HRUser = Depends(get_current_user),
    service: PositionService = Depends(get_position_service),
) -> PositionResponse:
    updates = payload.model_dump(exclude_unset=True)
    return _to_response(service.update(current_user, position_id, updates))


@router.delete("/{position_id}", status_code=204)
def delete_position(
    position_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: PositionService = Depends(get_position_service),
) -> None:
    service.delete(current_user, position_id)


def _to_response(position: Position) -> PositionResponse:
    return PositionResponse(
        id=position.id,
        owner_id=position.owner_id,
        company_name=position.company_name,
        title=position.title,
        description=position.description,
        experience_level=position.experience_level,
        pass_score_threshold=position.pass_score_threshold,
        rubric_profile=position.rubric_profile,
        status=position.status,
    )
