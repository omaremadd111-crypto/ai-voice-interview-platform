"""Public, unauthenticated response schemas -- api/routers/public.py only.

A dedicated schema module on purpose: the recruiter-facing PositionResponse
(api/schemas/position.py) carries owner_id, rubric_profile, and
pass_score_threshold, none of which may ever reach an anonymous caller. Keeping
this file separate makes "what a stranger can see about a job posting" a single,
auditable surface instead of a subset of a bigger response someone has to
remember to trim.

The apply endpoint itself is NOT modelled here as a request body: it accepts a
CV file, so it is multipart/form-data, not JSON -- see
api/routers/public.py's apply_to_job, which reads the raw Request so it can
reject an oversized body by Content-Length before parsing the form at all.
This module only carries what such a handler returns.
"""
import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class PublicJobResponse(BaseModel):
    slug: str
    title: str
    company_name: str
    description: str | None
    experience_level: str | None
    require_phone: bool
    application_notice: str | None


class ApplyFields(BaseModel):
    """Parsed and validated form fields from the multipart apply request. Not
    itself a request body FastAPI binds automatically -- see this module's
    docstring -- but validating through one Pydantic model in one place keeps
    every rule (blank checks, email shape, length caps) in a single spot
    instead of scattered across the handler."""

    full_name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    #: Must be explicitly true, mirroring RecordingConsentRequest's pattern --
    #: a default-true field would let a client consent by omission.
    consent: bool

    @field_validator("full_name")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        normalized = v.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("email")
    @classmethod
    def _looks_like_an_email(cls, v: str) -> str:
        normalized = v.strip()
        if not _EMAIL_PATTERN.match(normalized):
            raise ValueError("must be a valid email address")
        return normalized

    @field_validator("phone")
    @classmethod
    def _blank_phone_is_none(cls, v: str | None) -> str | None:
        if v is None:
            return None
        normalized = v.strip()
        return normalized or None


class ApplyResponse(BaseModel):
    application_id: int
    #: Present only once the automatic pipeline has issued a usable interview
    #: link for this application -- absent when auto_create_invitation (or an
    #: earlier automation step) is off, in which case HR finishes by hand.
    interview_token: str | None = None


class InterviewLandingResponse(BaseModel):
    position_title: str
    company_name: str
    candidate_first_name: str
    expires_at: datetime
    #: "not_started" | "preparing" | "ready" | "completed" | "failed"
    stage: str
    #: Set only when stage == "ready" -- the candidate's browser should
    #: navigate to /voice/{voice_invite_url's token} immediately.
    voice_invite_url: str | None = None
