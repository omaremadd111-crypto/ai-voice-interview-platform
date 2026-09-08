"""Screening-template / automation-settings request/response schemas."""
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field

from models.common import TemplateStatus

#: Mirrors PositionScreeningConfig's own `_positive_offsets` validator
#: (models/platform.py). Enforced here too because Pydantic's model_copy(update=...)
#: -- what every application service uses to apply a partial update -- does NOT
#: re-run the domain model's field validators, so an invalid value would
#: otherwise reach the database untouched unless the request schema itself
#: rejects it first.
PositiveHours = Annotated[int, Field(gt=0)]


class ScreeningSettingsUpdateRequest(BaseModel):
    """All fields optional: only fields the client actually set are applied
    (the router calls .model_dump(exclude_unset=True)).

    accept_public_applications is deliberately absent -- only the publish/
    unpublish actions may change it, so it can never be true while the position
    has no public_slug yet.
    """

    auto_parse_cv: bool | None = None
    auto_create_plan: bool | None = None
    auto_create_invitation: bool | None = None
    allow_immediate_start: bool | None = None
    auto_email_invitation: bool | None = None
    allow_cv_personalization: bool | None = None
    invitation_ttl_hours: int | None = Field(default=None, gt=0)
    reminder_offsets_hours: list[PositiveHours] | None = None
    max_applications_per_day: int | None = Field(default=None, gt=0)
    require_phone: bool | None = None
    application_notice: str | None = None


class ScreeningConfigResponse(BaseModel):
    position_id: int
    template_status: TemplateStatus
    template_approved_at: datetime | None
    accept_public_applications: bool
    auto_parse_cv: bool
    auto_create_plan: bool
    auto_create_invitation: bool
    allow_immediate_start: bool
    auto_email_invitation: bool
    allow_cv_personalization: bool
    invitation_ttl_hours: int
    reminder_offsets_hours: list[int]
    max_applications_per_day: int | None
    require_phone: bool
    application_notice: str | None
    #: From the owning Position, surfaced here so the Screening setup tab needs
    #: only this one endpoint. Null until publish() mints one.
    public_slug: str | None
    #: public_slug joined onto VOICE_PUBLIC_BASE_URL/jobs/. Null until a slug exists.
    public_url: str | None
