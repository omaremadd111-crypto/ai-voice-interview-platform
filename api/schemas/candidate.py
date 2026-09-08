"""Candidate request/response schemas. CV stays optional throughout -- see
CandidateRecord.cv_text in models/platform.py. File upload is deferred (see the
P3 report); only CV text is accepted here."""
from pydantic import BaseModel, Field

from models.common import CandidateStatus


class CandidateCreateRequest(BaseModel):
    position_id: int
    full_name: str = Field(min_length=1, max_length=255)
    email: str | None = None
    phone: str | None = None
    cv_text: str | None = None
    cv_filename: str | None = None


class CandidateUpdateRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    email: str | None = None
    phone: str | None = None
    cv_text: str | None = None
    cv_filename: str | None = None
    status: CandidateStatus | None = None


class CandidateResponse(BaseModel):
    id: int
    position_id: int
    full_name: str
    email: str | None
    phone: str | None
    cv_text: str | None
    cv_filename: str | None
    status: CandidateStatus
