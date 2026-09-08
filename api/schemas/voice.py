"""HTTP-only schemas for signed voice invitations and room tokens."""
from datetime import datetime

from pydantic import BaseModel, Field


class VoiceInvitationResponse(BaseModel):
    invite_url: str
    expires_at: datetime


class VoiceTokenRequest(BaseModel):
    invitation: str = Field(min_length=20, max_length=4096)


class VoiceTokenResponse(BaseModel):
    token: str
    server_url: str
    room_name: str
    participant_identity: str
    participant_name: str
    agent_name: str
    company_name: str


class RecordingConsentRequest(BaseModel):
    invitation: str = Field(min_length=20, max_length=4096)
    #: Must be explicitly true. A default-true field would let a client consent
    #: by omission, which is not consent.
    accepted: bool


class RecordingConsentResponse(BaseModel):
    consented_at: datetime
    consent_version: str
    consent_text: str


class ConsentNoticeResponse(BaseModel):
    """The notice to display before joining, and whether it must be accepted."""

    consent_required: bool
    consent_version: str
    consent_text: str


class TranscriptSegmentResponse(BaseModel):
    sequence: int
    speaker: str
    content: str
    spoken_at: datetime


class InterviewRecordingResponse(BaseModel):
    status: str
    #: None when the recording failed, is still running, or no playback base URL
    #: is configured. The frontend shows a graceful state rather than a player.
    audio_url: str | None = None
    duration_seconds: float | None = None
    file_size_bytes: int | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    error: str | None = None


class InterviewMediaResponse(BaseModel):
    """Everything HR needs to review one interview's audio and transcript."""

    interview_session_id: str
    recording: InterviewRecordingResponse | None = None
    transcript: list[TranscriptSegmentResponse] = Field(default_factory=list)
