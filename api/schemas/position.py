"""Position request/response schemas."""
from pydantic import BaseModel, Field

from models.common import PositionStatus


class PositionCreateRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    experience_level: str | None = None
    pass_score_threshold: int | None = Field(default=None, ge=0, le=100)
    rubric_profile: str | None = None
    status: PositionStatus = PositionStatus.DRAFT


class PositionUpdateRequest(BaseModel):
    """All fields optional: only fields the client actually set are applied
    (routers call .model_dump(exclude_unset=True))."""

    company_name: str | None = Field(default=None, min_length=1, max_length=255)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    experience_level: str | None = None
    pass_score_threshold: int | None = Field(default=None, ge=0, le=100)
    rubric_profile: str | None = None
    status: PositionStatus | None = None


class PositionResponse(BaseModel):
    id: int
    owner_id: int
    company_name: str
    title: str
    description: str | None
    experience_level: str | None
    pass_score_threshold: int | None
    rubric_profile: str | None
    status: PositionStatus
