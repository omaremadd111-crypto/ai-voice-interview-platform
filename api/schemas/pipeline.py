"""Recruiter pipeline-board response schemas -- api/routers/pipeline.py.

One row per application, carrying what the board needs to display so the
frontend never has to separately fetch a candidate for every row.
"""
from datetime import datetime

from pydantic import BaseModel

from models.common import ApplicationState


class PipelineRowResponse(BaseModel):
    application_id: int
    candidate_id: int | None
    full_name: str
    email: str
    phone: str | None
    pipeline_state: ApplicationState
    cv_parse_error: str | None
    last_error: str | None
    created_at: datetime | None


class InvitationIssuedResponse(BaseModel):
    """Response for the recruiter's manual create/resend-invitation action.

    Carries the plaintext interview_url -- the one place outside the original
    apply() response this ever appears -- so the recruiter can copy/paste it
    to the candidate exactly like the existing voice-invite flow already works.
    """

    expires_at: datetime
    interview_url: str
