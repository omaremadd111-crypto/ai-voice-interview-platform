"""Candidate LiveKit invitation/token endpoints; no interview logic lives here."""
from fastapi import APIRouter, Depends

from api.dependencies import get_current_user, get_voice_invite_service
from api.dependencies import get_interview_media_service, get_settings
from api.schemas.voice import (
    ConsentNoticeResponse,
    InterviewMediaResponse,
    InterviewRecordingResponse,
    RecordingConsentRequest,
    RecordingConsentResponse,
    TranscriptSegmentResponse,
    VoiceInvitationResponse,
    VoiceTokenRequest,
    VoiceTokenResponse,
)
from application.voice_invite_service import (
    RECORDING_CONSENT_TEXT,
    VoiceConsentRequiredError,
)
from application.interview_media_service import InterviewMediaService
from config.settings import Settings
from application.voice_invite_service import VoiceInviteService
from models.platform import HRUser

router = APIRouter(tags=["voice"])


@router.post(
    "/api/v1/queues/{queue_id}/items/{item_id}/voice-invite",
    response_model=VoiceInvitationResponse,
)
def create_voice_invitation(
    queue_id: int,
    item_id: int,
    current_user: HRUser = Depends(get_current_user),
    service: VoiceInviteService = Depends(get_voice_invite_service),
) -> VoiceInvitationResponse:
    result = service.create_invitation(current_user, queue_id, item_id)
    return VoiceInvitationResponse(**result.model_dump())


@router.post("/api/v1/voice/token", response_model=VoiceTokenResponse)
def create_voice_room_token(
    payload: VoiceTokenRequest,
    service: VoiceInviteService = Depends(get_voice_invite_service),
) -> VoiceTokenResponse:
    result = service.issue_room_token(payload.invitation)
    return VoiceTokenResponse(**result.model_dump())


@router.get("/api/v1/voice/consent-notice", response_model=ConsentNoticeResponse)
def get_consent_notice(
    settings: Settings = Depends(get_settings),
) -> ConsentNoticeResponse:
    """The recording notice the candidate must be shown before joining.

    Unauthenticated on purpose: it is shown on the candidate's invitation page,
    which has no HR session, and it contains no interview or candidate data.
    """
    return ConsentNoticeResponse(
        consent_required=settings.recording_consent_required,
        consent_version=settings.recording_consent_version,
        consent_text=RECORDING_CONSENT_TEXT,
    )


@router.post("/api/v1/voice/consent", response_model=RecordingConsentResponse)
def record_recording_consent(
    payload: RecordingConsentRequest,
    service: VoiceInviteService = Depends(get_voice_invite_service),
) -> RecordingConsentResponse:
    """Capture consent before a token is issued.

    Rejects a request that is not an explicit acceptance, so the stored record
    can only ever mean "this candidate agreed".
    """
    if not payload.accepted:
        raise VoiceConsentRequiredError(
            "Recording consent must be explicitly accepted to continue."
        )
    consent = service.record_consent(payload.invitation)
    return RecordingConsentResponse(
        consented_at=consent.consented_at,
        consent_version=consent.consent_version,
        consent_text=consent.consent_text,
    )


@router.get(
    "/api/v1/interviews/{session_id}/media", response_model=InterviewMediaResponse
)
def get_interview_media(
    session_id: str,
    current_user: HRUser = Depends(get_current_user),
    service: InterviewMediaService = Depends(get_interview_media_service),
) -> InterviewMediaResponse:
    """Recording status/audio and the speaker-labelled transcript, for HR review."""
    media = service.get_media(session_id)
    recording = media.recording
    segments = media.transcript

    return InterviewMediaResponse(
        interview_session_id=session_id,
        recording=(
            InterviewRecordingResponse(
                status=recording.status.value,
                # Only a completed recording with a resolvable URL is playable;
                # otherwise the frontend shows why instead of a broken player.
                audio_url=recording.file_url if recording.is_playable else None,
                duration_seconds=recording.duration_seconds,
                file_size_bytes=recording.file_size_bytes,
                started_at=recording.started_at,
                ended_at=recording.ended_at,
                error=recording.error,
            )
            if recording is not None
            else None
        ),
        transcript=[
            TranscriptSegmentResponse(
                sequence=segment.sequence,
                speaker=segment.speaker.value,
                content=segment.content,
                spoken_at=segment.spoken_at,
            )
            for segment in segments
        ],
    )
