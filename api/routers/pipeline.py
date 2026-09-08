"""Recruiter pipeline-board endpoints: thin transport only.

Delegates entirely to application/pipeline_service.py. Nothing here creates a
candidate, a plan, or an interview -- the automated pipeline (started from the
public apply endpoint) is the only writer of that; this router only reads its
results and, on request, issues or resends one candidate's durable invitation.
"""
from fastapi import APIRouter, Depends

from api.dependencies import get_current_user, get_pipeline_service, get_settings
from api.schemas.pipeline import InvitationIssuedResponse, PipelineRowResponse
from application.pipeline_service import PipelineService
from config.settings import Settings
from models.platform import HRUser

router = APIRouter(prefix="/api/v1/positions", tags=["pipeline"])


@router.get("/{position_id}/pipeline", response_model=list[PipelineRowResponse])
def get_pipeline(
    position_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: PipelineService = Depends(get_pipeline_service),
) -> list[PipelineRowResponse]:
    return [_to_response(row) for row in service.list_for_position(current_user, position_id)]


@router.post(
    "/{position_id}/pipeline/{application_id}/invitation", response_model=InvitationIssuedResponse,
)
def issue_pipeline_invitation(
    position_id: int,
    application_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: PipelineService = Depends(get_pipeline_service),
    settings: Settings = Depends(get_settings),
) -> InvitationIssuedResponse:
    issued = service.issue_invitation(current_user, position_id, application_id)
    return InvitationIssuedResponse(
        expires_at=issued.expires_at,
        interview_url=f"{settings.voice_public_base_url}/interview/{issued.token}",
    )


def _to_response(row) -> PipelineRowResponse:
    return PipelineRowResponse(
        application_id=row.id,
        candidate_id=row.candidate_id,
        full_name=row.full_name,
        email=row.email_normalized,
        phone=row.phone,
        pipeline_state=row.pipeline_state,
        cv_parse_error=row.cv_parse_error,
        last_error=row.last_error,
        created_at=row.created_at,
    )
