"""Interview preparation endpoint: thin transport only.

Delegates entirely to application/interview_preparation_service.py, which
reuses the existing InterviewAgentService.prepare_from_position() -- no
interview logic is duplicated here. Deliberately does NOT expose the
candidate-facing interview/voice flow; that is out of scope for P3.
"""
from fastapi import APIRouter, Depends

from api.dependencies import get_current_user, get_interview_preparation_service
from api.schemas.interview import PrepareInterviewRequest, PrepareInterviewResponse
from application.interview_preparation_service import InterviewPreparationService
from models.platform import HRUser

router = APIRouter(prefix="/api/v1/interviews", tags=["interviews"])


@router.post("/prepare", response_model=PrepareInterviewResponse, status_code=201)
def prepare_interview(
    payload: PrepareInterviewRequest,
    current_user: HRUser = Depends(get_current_user),
    service: InterviewPreparationService = Depends(get_interview_preparation_service),
) -> PrepareInterviewResponse:
    result = service.prepare(current_user, payload.position_id, payload.candidate_id)
    return PrepareInterviewResponse(
        session_id=result.session_id,
        state=result.state,
        job_analysis=result.job_analysis,
        candidate_analysis=result.candidate_analysis,
        fit_analysis=result.fit_analysis,
        interview_plan=result.interview_plan,
        llm_provider=result.llm_provider,
        is_mock=result.is_mock,
    )
